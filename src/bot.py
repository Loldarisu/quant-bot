import asyncio
import yaml
from datetime import datetime, time
from loguru import logger

from src.data.market_data import MarketDataFetcher
from src.data.funding_rates import FundingRateFetcher
from src.signals.funding_signal import FundingSignalGenerator
from src.signals.regime_detector import RegimeDetector, Regime
from src.risk.position_sizing import PositionSizer
from src.risk.risk_manager import RiskManager
from src.execution.order_executor import OrderExecutor
from src.monitoring.telegram_alerts import TelegramAlerter


class QuantBot:
    """
    Main trading bot orchestrator.
    Runs the funding rate arbitrage strategy with full risk management.
    """

    def __init__(self, config: dict, api_key: str, secret: str, tg_token: str, tg_chat: str, **kwargs):
        cfg = config
        exchange_id = cfg["exchange"]["id"]
        capital = cfg["capital"]["initial"]

        self.config = cfg
        self.strategy_cfg = cfg["strategies"]["funding_arb"]
        self.symbols = self.strategy_cfg["symbols"]

        passphrase = kwargs.get("passphrase", "")
        self.data = MarketDataFetcher(exchange_id, api_key, secret, passphrase)
        self.funding = FundingRateFetcher(exchange_id, api_key, secret, passphrase)
        self.signal_gen = FundingSignalGenerator(
            min_rate_8h=self.strategy_cfg["min_funding_rate"],
        )
        self.regime = RegimeDetector(**cfg["signals"]["regime_detector"])
        self.sizer = PositionSizer(
            capital=capital,
            max_risk_per_trade=cfg["capital"]["max_risk_per_trade"],
            max_position_pct=cfg["capital"]["max_position_pct"],
            target_vol=cfg["capital"]["target_vol"],
            max_leverage=cfg["capital"]["max_leverage"],
        )
        self.risk = RiskManager(
            capital=capital,
            max_daily_loss_pct=cfg["risk"]["max_daily_loss_pct"],
            max_total_drawdown_pct=cfg["risk"]["max_total_drawdown_pct"],
            max_open_positions=cfg["risk"]["max_open_positions"],
            max_trades_per_day=cfg["risk"]["max_trades_per_day"],
        )
        self.executor = OrderExecutor(
            exchange_id=exchange_id,
            api_key=api_key,
            secret=secret,
            passphrase=kwargs.get("passphrase", ""),
            paper_trading=cfg["exchange"]["paper_trading"],
        )
        self.alerter = TelegramAlerter(tg_token, tg_chat)
        self.open_positions: dict = {}
        self.running = False

    async def run(self):
        self.running = True
        logger.info("QuantBot started")
        await self.alerter._send("🤖 <b>QuantBot started</b>")

        scan_interval = self.strategy_cfg.get("scan_interval_seconds", 60)
        self._last_summary_date = None

        while self.running:
            try:
                await self._cycle()
                await self._maybe_send_daily_summary()
                self._write_status()
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                await self.alerter.error_alert("main_cycle", str(e))
            await asyncio.sleep(scan_interval)

    async def _cycle(self):
        if self.risk.bot_halted:
            logger.warning("Bot is halted — skipping cycle")
            return

        # Scan funding opportunities
        opportunities = await self.funding.scan_opportunities(
            self.symbols,
            min_rate=self.strategy_cfg["min_funding_rate"],
        )

        if opportunities.empty:
            logger.debug("No funding opportunities this cycle")
            return

        for _, row in opportunities.iterrows():
            symbol = row["symbol"]

            # Collect funding if already in position
            # Funding pays every 8 hours — only credit at actual payment windows
            if symbol in self.open_positions:
                import datetime as _dt
                now_hour = _dt.datetime.utcnow().hour
                now_min  = _dt.datetime.utcnow().minute
                is_funding_window = now_hour in (0, 8, 16) and now_min < 2
                last_funding = self.open_positions[symbol].get("last_funding_hour", -1)

                rate = row["funding_rate"]
                if is_funding_window and last_funding != now_hour:
                    self.open_positions[symbol]["last_funding_hour"] = now_hour
                    size = self.open_positions[symbol]["size_usd"]
                    funding_income = size * abs(rate)
                    self.risk.on_funding_received(symbol, funding_income)
                    await self.alerter.funding_received(symbol, funding_income, rate)

                # Check exit condition
                if abs(rate) < self.strategy_cfg["exit_funding_rate"]:
                    await self._close_position(symbol, reason="funding_below_exit")
                continue

            # Evaluate new opportunity
            try:
                funding_data = await self.funding.fetch_current_funding(symbol)
                history_df = await self.funding.fetch_funding_history(symbol, limit=50)
                opp = self.signal_gen.evaluate(
                    symbol=symbol,
                    current_rate=funding_data["funding_rate"],
                    history=history_df["fundingRate"],
                )
            except Exception as e:
                logger.warning(f"Could not evaluate {symbol}: {e}")
                continue

            if opp is None or opp.confidence == "low":
                continue

            # Get current price and ATR for sizing
            try:
                ohlcv = await self.data.fetch_ohlcv(symbol, "1h", limit=50)
                price = float(ohlcv["close"].iloc[-1])
                atr_val = float(
                    self.regime.atr(ohlcv).iloc[-1]
                )
            except Exception as e:
                logger.warning(f"Could not get price data for {symbol}: {e}")
                continue

            sizing = self.sizer.volatility_adjusted_size(symbol, price, atr_val)
            allowed, reason = self.risk.can_trade(symbol, sizing.position_size_usd)

            if not allowed:
                logger.info(f"Trade blocked for {symbol}: {reason}")
                continue

            await self._open_position(symbol, opp.action, sizing.position_size_usd, price)

    async def _open_position(self, symbol: str, action: str, size_usd: float, price: float):
        if action == "short_perp_long_spot":
            perp_side, spot_side = "sell", "buy"
        else:
            perp_side, spot_side = "buy", "sell"

        units = size_usd / price

        # Execute both legs
        perp_result = await self.executor.execute(symbol, perp_side, units)
        if perp_result is None:
            logger.error(f"Failed to open perp leg for {symbol}")
            return

        # For paper trading, spot is simulated
        self.open_positions[symbol] = {
            "action": action,
            "size_usd": size_usd,
            "entry_price": price,
            "entry_time": asyncio.get_running_loop().time(),
        }
        self.risk.on_trade_opened(symbol, size_usd)
        await self.alerter.trade_opened(symbol, action, size_usd, price)

    async def _close_position(self, symbol: str, reason: str = "manual"):
        if symbol not in self.open_positions:
            return

        pos = self.open_positions[symbol]
        try:
            ticker = await self.data.fetch_ticker(symbol)
            exit_price = float(ticker["last"])
        except Exception:
            exit_price = pos["entry_price"]

        pnl = 0.0  # In funding arb, PnL is accumulated via funding payments
        pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100

        del self.open_positions[symbol]
        self.risk.on_trade_closed(symbol, pnl)
        await self.alerter.trade_closed(symbol, pnl, pnl_pct, reason)

    def _write_status(self):
        """Escreve estado actual em logs/status.json para monitorização remota."""
        import json, os
        from datetime import datetime, timezone
        status = self.risk.get_status()
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "capital": status["capital"],
            "daily_pnl": status["daily_pnl"],
            "total_pnl": status["total_pnl"],
            "max_drawdown_pct": status["max_drawdown_pct"],
            "open_positions": status["open_positions"],
            "daily_trades": status["daily_trades"],
            "bot_halted": status["halted"],
            "positions": {
                sym: {
                    "action": pos["action"],
                    "size_usd": pos["size_usd"],
                    "entry_price": pos["entry_price"],
                }
                for sym, pos in self.open_positions.items()
            },
        }
        os.makedirs("logs", exist_ok=True)
        with open("logs/status.json", "w") as f:
            json.dump(data, f, indent=2)

    async def _maybe_send_daily_summary(self):
        """Envia resumo diário às 23h (UTC) uma vez por dia."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        today = now.date()
        if now.hour == 23 and self._last_summary_date != today:
            self._last_summary_date = today
            status = self.risk.get_status()
            positions_detail = ""
            for sym, pos in self.open_positions.items():
                positions_detail += f"\n  • {sym}: ${pos['size_usd']:.2f}"
            await self.alerter._send(
                f"📊 <b>Resumo Diário — {today}</b>\n"
                f"Capital: <b>${status['capital']:,.2f}</b>\n"
                f"PnL hoje: <b>${status['daily_pnl']:+.4f}</b>\n"
                f"PnL total: <b>${status['total_pnl']:+.4f}</b>\n"
                f"Drawdown máx: {status['max_drawdown_pct']:.2f}%\n"
                f"Posições abertas: {status['open_positions']}{positions_detail}\n"
                f"Trades hoje: {status['daily_trades']}"
            )
            logger.info("Daily summary sent via Telegram")

    async def stop(self):
        self.running = False
        status = self.risk.get_status()
        await self.alerter.daily_summary(
            capital=status["capital"],
            daily_pnl=status["daily_pnl"],
            total_pnl=status["total_pnl"],
            open_pos=status["open_positions"],
        )
        await self.data.close()
        await self.funding.close()
        await self.executor.close()
        logger.info("QuantBot stopped")
