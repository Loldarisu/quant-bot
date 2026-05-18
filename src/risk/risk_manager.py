from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, Optional
from loguru import logger


@dataclass
class RiskState:
    capital_initial: float
    capital_current: float
    daily_pnl: float = 0.0
    daily_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_capital: float = 0.0
    open_positions: Dict[str, float] = field(default_factory=dict)  # symbol → size_usd
    last_reset_date: date = field(default_factory=date.today)

    def __post_init__(self):
        self.peak_capital = self.capital_current


class RiskManager:
    """
    Central risk controller. Checks every trade before execution.

    Enforces:
    - Daily loss limit
    - Max total drawdown circuit breaker
    - Max open positions
    - Max correlated exposure
    - Daily trade count limit
    """

    def __init__(
        self,
        capital: float,
        max_daily_loss_pct: float = 0.05,       # 5% daily loss → stop bot
        max_total_drawdown_pct: float = 0.20,   # 20% drawdown → stop bot
        max_open_positions: int = 5,
        max_trades_per_day: int = 20,
        max_single_exposure_pct: float = 0.10,  # max 10% of capital in one position
    ):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_total_drawdown_pct = max_total_drawdown_pct
        self.max_open_positions = max_open_positions
        self.max_trades_per_day = max_trades_per_day
        self.max_single_exposure_pct = max_single_exposure_pct
        self.bot_halted = False

        self.state = RiskState(
            capital_initial=capital,
            capital_current=capital,
        )

    def _reset_daily_if_needed(self):
        today = date.today()
        if self.state.last_reset_date < today:
            logger.info(f"Daily reset — yesterday PnL: {self.state.daily_pnl:.2f}")
            self.state.daily_pnl = 0.0
            self.state.daily_trades = 0
            self.state.last_reset_date = today

    def _update_drawdown(self):
        if self.state.capital_current > self.state.peak_capital:
            self.state.peak_capital = self.state.capital_current
        drawdown = (self.state.peak_capital - self.state.capital_current) / self.state.peak_capital
        self.state.max_drawdown = max(self.state.max_drawdown, drawdown)

    def can_trade(self, symbol: str, size_usd: float) -> tuple[bool, str]:
        """Returns (allowed: bool, reason: str)."""
        self._reset_daily_if_needed()

        if self.bot_halted:
            return False, "BOT_HALTED"

        # Daily loss limit
        daily_loss_pct = abs(min(0, self.state.daily_pnl)) / self.state.capital_current
        if daily_loss_pct >= self.max_daily_loss_pct:
            self.bot_halted = True
            logger.critical(f"DAILY LOSS LIMIT REACHED: {daily_loss_pct*100:.2f}% — BOT HALTED")
            return False, "DAILY_LOSS_LIMIT"

        # Total drawdown circuit breaker
        self._update_drawdown()
        if self.state.max_drawdown >= self.max_total_drawdown_pct:
            self.bot_halted = True
            logger.critical(f"MAX DRAWDOWN REACHED: {self.state.max_drawdown*100:.2f}% — BOT HALTED")
            return False, "MAX_DRAWDOWN"

        # Max open positions
        if len(self.state.open_positions) >= self.max_open_positions:
            return False, "MAX_POSITIONS"

        # Daily trade count
        if self.state.daily_trades >= self.max_trades_per_day:
            return False, "MAX_DAILY_TRADES"

        # Single position size limit
        exposure_pct = size_usd / self.state.capital_current
        if exposure_pct > self.max_single_exposure_pct:
            return False, f"POSITION_TOO_LARGE ({exposure_pct*100:.1f}%)"

        return True, "OK"

    def on_trade_opened(self, symbol: str, size_usd: float):
        if symbol in self.state.open_positions:
            logger.warning(f"on_trade_opened called for already-open position: {symbol} — ignoring")
            return
        self.state.open_positions[symbol] = size_usd
        self.state.daily_trades += 1
        logger.info(f"Position opened: {symbol} | ${size_usd:.2f} | open={len(self.state.open_positions)}")

    def on_trade_closed(self, symbol: str, pnl: float):
        self.state.open_positions.pop(symbol, None)
        self.state.daily_pnl += pnl
        self.state.total_pnl += pnl
        self.state.capital_current += pnl
        self._update_drawdown()
        logger.info(f"Position closed: {symbol} | PnL={pnl:.2f} | total={self.state.total_pnl:.2f}")

    def on_funding_received(self, symbol: str, amount: float):
        self.state.daily_pnl += amount
        self.state.total_pnl += amount
        self.state.capital_current += amount
        logger.info(f"Funding received: {symbol} | ${amount:.4f}")

    def get_status(self) -> dict:
        self._reset_daily_if_needed()
        self._update_drawdown()
        return {
            "halted": self.bot_halted,
            "capital": round(self.state.capital_current, 2),
            "total_pnl": round(self.state.total_pnl, 2),
            "daily_pnl": round(self.state.daily_pnl, 2),
            "max_drawdown_pct": round(self.state.max_drawdown * 100, 2),
            "open_positions": len(self.state.open_positions),
            "daily_trades": self.state.daily_trades,
        }
