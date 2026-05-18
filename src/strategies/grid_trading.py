import asyncio
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from src.data.market_data import MarketDataFetcher
from src.execution.order_executor import OrderExecutor
from src.monitoring.telegram_alerts import TelegramAlerter
from src.risk.risk_manager import RiskManager


@dataclass
class GridLevel:
    price: float
    side: str        # "buy" ou "sell"
    filled: bool = False
    order_id: str = ""


@dataclass
class GridStats:
    total_cycles: int = 0       # quantas vezes completou buy→sell
    total_pnl: float = 0.0
    total_fees: float = 0.0
    grid_income: float = 0.0    # lucro das oscilações
    current_price: float = 0.0


class GridStrategy:
    """
    Grid Trading — captura oscilações de preço num range definido.

    Lógica:
    - Define N níveis entre price_low e price_high
    - Abaixo do preço actual → ordens de compra
    - Acima do preço actual  → ordens de venda
    - Quando buy preenche → coloca sell no nível acima
    - Quando sell preenche → coloca buy no nível abaixo
    - Lucro por ciclo = grid_spacing - 2 × fees

    Condições ideais: mercado ranging sem tendência forte
    """

    def __init__(
        self,
        symbol: str,
        price_low: float,
        price_high: float,
        n_levels: int,
        capital_per_grid: float,   # USD por nível
        exchange_id: str,
        api_key: str,
        secret: str,
        paper_trading: bool = True,
        fee_rate: float = 0.001,   # 0.1% taker OKX (conservador)
        check_interval: int = 30,  # segundos entre verificações
    ):
        self.symbol = symbol
        self.price_low = price_low
        self.price_high = price_high
        self.n_levels = n_levels
        self.capital_per_grid = capital_per_grid
        self.fee_rate = fee_rate
        self.check_interval = check_interval
        self.running = False
        self.stats = GridStats()

        self.grid_spacing = (price_high - price_low) / n_levels
        # lucro por ciclo em USD = capital_por_nivel × (spacing/price - 2×fee)
        self.profit_per_cycle = capital_per_grid * (self.grid_spacing / ((price_low + price_high) / 2) - 2 * fee_rate)

        self.levels: list[GridLevel] = []
        self.pending_orders: dict[str, GridLevel] = {}  # order_id → level

        self.market = MarketDataFetcher(exchange_id, api_key, secret)
        self.executor = OrderExecutor(exchange_id, api_key, secret, paper_trading)

    def build_grid(self, current_price: float) -> list[GridLevel]:
        """Constrói os níveis da grelha em torno do preço actual."""
        levels = []
        price = self.price_low

        while price <= self.price_high:
            side = "buy" if price < current_price else "sell"
            levels.append(GridLevel(price=round(price, 2), side=side))
            price += self.grid_spacing

        logger.info(
            f"Grelha construída: {len(levels)} níveis | "
            f"spacing=${self.grid_spacing:.2f} | "
            f"lucro/ciclo≈${self.profit_per_cycle:.2f}"
        )
        return levels

    def get_units(self, price: float) -> float:
        return round(self.capital_per_grid / price, 6)

    async def _get_current_price(self) -> float:
        ticker = await self.market.fetch_ticker(self.symbol)
        return float(ticker["last"])

    async def _place_order(self, level: GridLevel) -> bool:
        units = self.get_units(level.price)
        result = await self.executor.execute(
            symbol=self.symbol,
            side=level.side,
            size=units,
            order_type="limit",
            limit_price=level.price,
        )
        if result:
            level.order_id = result.order_id
            self.pending_orders[result.order_id] = level
            logger.debug(f"Ordem colocada: {level.side} {units} @ ${level.price:.2f}")
            return True
        return False

    async def _check_fills(self, current_price: float):
        """Verifica se alguma ordem foi preenchida e reage."""
        for level in self.levels:
            if level.filled:
                continue

            filled = False
            if level.side == "buy" and current_price <= level.price:
                filled = True
            elif level.side == "sell" and current_price >= level.price:
                filled = True

            if not filled:
                continue

            level.filled = True
            units = self.get_units(level.price)
            fee = units * level.price * self.fee_rate

            if level.side == "buy":
                # Colocar sell no nível acima
                next_price = level.price + self.grid_spacing
                if next_price <= self.price_high:
                    next_level = GridLevel(price=round(next_price, 2), side="sell")
                    idx = next((i for i, l in enumerate(self.levels) if abs(l.price - next_price) < 0.01), None)
                    if idx is not None:
                        self.levels[idx].filled = False
                        self.levels[idx].side = "sell"
                    await self._place_order(next_level)
                logger.info(f"BUY preenchido @ ${level.price:.2f} → colocado SELL @ ${next_price:.2f}")

            elif level.side == "sell":
                # Colocar buy no nível abaixo
                prev_price = level.price - self.grid_spacing
                if prev_price >= self.price_low:
                    prev_level = GridLevel(price=round(prev_price, 2), side="buy")
                    idx = next((i for i, l in enumerate(self.levels) if abs(l.price - prev_price) < 0.01), None)
                    if idx is not None:
                        self.levels[idx].filled = False
                        self.levels[idx].side = "buy"
                    await self._place_order(prev_level)

                cycle_pnl = units * self.grid_spacing - 2 * fee
                self.stats.total_cycles += 1
                self.stats.total_pnl += cycle_pnl
                self.stats.total_fees += 2 * fee
                self.stats.grid_income += cycle_pnl
                logger.info(
                    f"CICLO #{self.stats.total_cycles} completo | "
                    f"+${cycle_pnl:.4f} | total=${self.stats.total_pnl:.2f}"
                )

    def print_status(self, current_price: float):
        in_range = self.price_low <= current_price <= self.price_high
        status = "[DENTRO]" if in_range else "[FORA DO RANGE]"
        print(
            f"\n  Grid {self.symbol} | Preco: ${current_price:,.2f} {status}"
            f"\n  Range: ${self.price_low:,} -> ${self.price_high:,} | "
            f"Niveis: {self.n_levels} | Spacing: ${self.grid_spacing:.0f}"
            f"\n  Ciclos: {self.stats.total_cycles} | "
            f"PnL total: ${self.stats.total_pnl:.4f} | "
            f"Fees pagas: ${self.stats.total_fees:.4f}"
        )

    async def run(self, alerter: Optional[TelegramAlerter] = None):
        self.running = True
        current_price = await self._get_current_price()
        self.stats.current_price = current_price

        self.levels = self.build_grid(current_price)

        # Colocar todas as ordens iniciais
        logger.info("A colocar ordens iniciais...")
        for level in self.levels:
            await self._place_order(level)
            await asyncio.sleep(0.1)

        logger.info(f"Grid activa! {len(self.levels)} ordens colocadas")
        if alerter:
            await alerter._send(
                f"🔲 <b>Grid Trading iniciado</b>\n"
                f"Símbolo: <code>{self.symbol}</code>\n"
                f"Range: ${self.price_low:,} → ${self.price_high:,}\n"
                f"Níveis: {self.n_levels} | Spacing: ${self.grid_spacing:.0f}\n"
                f"Capital/nível: ${self.capital_per_grid}\n"
                f"Lucro estimado/ciclo: ${self.profit_per_cycle:.2f}"
            )

        while self.running:
            try:
                current_price = await self._get_current_price()
                self.stats.current_price = current_price
                await self._check_fills(current_price)
                self.print_status(current_price)

                # Alerta se preço sair do range
                if not (self.price_low <= current_price <= self.price_high):
                    logger.warning(f"Preco ${current_price:.2f} saiu do range! Grid pausada.")
                    if alerter:
                        await alerter._send(
                            f"⚠️ <b>Grid fora do range!</b>\n"
                            f"{self.symbol}: ${current_price:,.2f}\n"
                            f"Range: ${self.price_low:,} → ${self.price_high:,}\n"
                            f"Considera ajustar o range."
                        )

            except Exception as e:
                logger.error(f"Erro no ciclo grid: {e}")

            await asyncio.sleep(self.check_interval)

    async def stop(self):
        self.running = False
        await self.market.close()
        await self.executor.close()
        logger.info(
            f"Grid parada | Ciclos: {self.stats.total_cycles} | "
            f"PnL: ${self.stats.total_pnl:.4f}"
        )
