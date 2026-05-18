import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional
from loguru import logger


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: Optional[pd.Timestamp]
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    exit_reason: str


@dataclass
class BacktestResult:
    trades: List[Trade]
    equity_curve: pd.Series
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    avg_trade_pct: float
    total_trades: int
    avg_holding_periods: float


class FundingArbBacktester:
    """
    Backtest funding rate arbitrage strategy.

    Assumptions:
    - Enter short perp + long spot when funding > min_rate
    - Exit when funding drops below exit_rate
    - PnL = funding collected - fees (both legs)
    - Fees: 0.04% per leg (taker), 0.01% (maker)
    """

    def __init__(
        self,
        capital: float = 10_000,
        fee_rate: float = 0.0004,
        min_funding_rate: float = 0.0003,
        exit_funding_rate: float = 0.0001,
        position_pct: float = 0.8,
    ):
        self.capital = capital
        self.fee_rate = fee_rate
        self.min_funding_rate = min_funding_rate
        self.exit_funding_rate = exit_funding_rate
        self.position_pct = position_pct

    def run(self, funding_history: pd.DataFrame, price_history: pd.DataFrame, symbol: str) -> BacktestResult:
        """
        Parameters
        ----------
        funding_history : DataFrame with columns ['fundingRate'], index=datetime (8h intervals)
        price_history   : DataFrame with columns ['close'], index=datetime
        """
        funding = funding_history["fundingRate"].resample("8h").last().ffill()
        prices = price_history["close"].resample("8h").last().ffill()
        df = pd.DataFrame({"funding": funding, "price": prices}).dropna()

        trades: List[Trade] = []
        equity = self.capital
        equity_series = []
        in_position = False
        entry_time = None
        entry_price = None
        entry_fee = 0.0
        equity_at_entry = 0.0
        accumulated_funding = 0.0
        trade_side = "short_perp_long_spot"

        for ts, row in df.iterrows():
            rate = row["funding"]
            price = row["price"]
            position_size = equity * self.position_pct

            # Entry: positive funding → short perp + long spot
            # Entry: negative funding → long perp + short spot
            if not in_position and abs(rate) > self.min_funding_rate:
                entry_time = ts
                entry_price = price
                entry_fee = position_size * self.fee_rate * 2
                equity_at_entry = equity
                accumulated_funding = 0.0
                equity -= entry_fee
                in_position = True
                trade_side = "short_perp_long_spot" if rate > 0 else "long_perp_short_spot"

            elif in_position:
                # Collect funding — always positive regardless of direction
                funding_income = position_size * abs(rate)
                accumulated_funding += funding_income
                equity += funding_income

                # Exit when rate drops below exit threshold
                if abs(rate) < self.exit_funding_rate:
                    exit_price = price
                    exit_fee = position_size * self.fee_rate * 2
                    equity -= exit_fee

                    pnl = accumulated_funding - entry_fee - exit_fee
                    pnl_pct = (pnl / equity_at_entry) * 100

                    trades.append(Trade(
                        entry_time=entry_time,
                        exit_time=ts,
                        symbol=symbol,
                        side=trade_side,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        size=position_size,
                        pnl=round(pnl, 4),
                        pnl_pct=round(pnl_pct, 4),
                        exit_reason="funding_below_exit",
                    ))
                    in_position = False
                    accumulated_funding = 0.0

            equity_series.append((ts, equity))

        eq_series = pd.Series(
            [e[1] for e in equity_series],
            index=[e[0] for e in equity_series],
            name="equity",
        )

        return self._compute_metrics(trades, eq_series, symbol)

    def _compute_metrics(self, trades: List[Trade], equity: pd.Series, symbol: str) -> BacktestResult:
        if equity.empty or len(trades) == 0:
            logger.warning(f"No trades generated for {symbol}")
            return BacktestResult(
                trades=[], equity_curve=equity, total_return_pct=0, cagr_pct=0,
                sharpe_ratio=0, sortino_ratio=0, max_drawdown_pct=0,
                win_rate_pct=0, profit_factor=0, avg_trade_pct=0,
                total_trades=0, avg_holding_periods=0,
            )

        total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
        n_years = max((equity.index[-1] - equity.index[0]).days / 365, 0.01)
        cagr = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1) * 100

        daily_ret = equity.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(365)) if daily_ret.std() > 0 else 0
        downside = daily_ret[daily_ret < 0].std()
        sortino = (daily_ret.mean() / downside * np.sqrt(365)) if downside > 0 else 0

        peak = equity.cummax()
        drawdown = ((equity - peak) / peak).min() * 100

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_trade = np.mean([t.pnl_pct for t in trades]) if trades else 0

        logger.info(f"""
        ===== BACKTEST RESULTS: {symbol} =====
        Total Return : {total_return:.2f}%
        CAGR         : {cagr:.2f}%
        Sharpe       : {sharpe:.2f}
        Sortino      : {sortino:.2f}
        Max Drawdown : {drawdown:.2f}%
        Win Rate     : {win_rate:.1f}%
        Profit Factor: {profit_factor:.2f}
        Total Trades : {len(trades)}
        ======================================
        """)

        return BacktestResult(
            trades=trades,
            equity_curve=equity,
            total_return_pct=round(total_return, 2),
            cagr_pct=round(cagr, 2),
            sharpe_ratio=round(float(sharpe), 3),
            sortino_ratio=round(float(sortino), 3),
            max_drawdown_pct=round(float(drawdown), 2),
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(profit_factor, 3),
            avg_trade_pct=round(float(avg_trade), 4),
            total_trades=len(trades),
            avg_holding_periods=0,
        )
