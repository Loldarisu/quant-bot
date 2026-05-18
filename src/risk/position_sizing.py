import numpy as np
from dataclasses import dataclass
from loguru import logger


@dataclass
class SizingResult:
    symbol: str
    position_size_usd: float
    position_size_units: float
    risk_amount_usd: float
    risk_pct: float
    leverage: float


class PositionSizer:
    """
    Professional position sizing using:
    1. Half-Kelly Criterion (for speculative strategies)
    2. Fixed Fractional (safer, for funding arb)
    3. Volatility-adjusted sizing (scales down in high-vol regimes)

    Hard limits enforced on every calculation.
    """

    def __init__(
        self,
        capital: float,
        max_risk_per_trade: float = 0.02,   # 2% of capital per trade
        max_position_pct: float = 0.10,     # 10% of capital per position
        target_vol: float = 0.15,           # 15% annualised portfolio vol target
        max_leverage: float = 3.0,
    ):
        self.capital = capital
        self.max_risk_per_trade = max_risk_per_trade
        self.max_position_pct = max_position_pct
        self.target_vol = target_vol
        self.max_leverage = max_leverage

    def half_kelly(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Half-Kelly fraction of capital to risk."""
        if avg_win <= 0 or avg_loss <= 0:
            return 0.0
        loss_rate = 1 - win_rate
        kelly = (win_rate * avg_win - loss_rate * avg_loss) / avg_win
        half_k = kelly / 2
        return float(np.clip(half_k, 0.0, self.max_risk_per_trade))

    def fixed_fractional(self, risk_fraction: float = 0.01) -> float:
        """Simple fixed fraction — most conservative and common in live systems."""
        return float(np.clip(risk_fraction, 0.0, self.max_risk_per_trade))

    def volatility_adjusted_size(
        self,
        symbol: str,
        price: float,
        atr: float,
        stop_distance_atr: float = 2.0,
        method: str = "fixed_fractional",
        win_rate: float = 0.55,
        avg_win: float = 0.015,
        avg_loss: float = 0.01,
    ) -> SizingResult:
        """
        Calculate position size given current price and ATR-based stop.

        stop_distance = stop_distance_atr × ATR
        risk_per_unit = stop_distance / price
        size = risk_budget / risk_per_unit
        """
        if method == "kelly":
            risk_fraction = self.half_kelly(win_rate, avg_win, avg_loss)
        else:
            risk_fraction = self.fixed_fractional()

        risk_budget_usd = self.capital * risk_fraction
        stop_distance_usd = atr * stop_distance_atr
        risk_per_unit = stop_distance_usd / price

        if atr <= 0 or risk_per_unit <= 0:
            logger.warning(f"ATR={atr:.6f} or risk_per_unit={risk_per_unit:.6f} invalid for {symbol} — returning zero size")
            return SizingResult(symbol, 0, 0, 0, 0, 0)

        # Realised vol scalar: scale down when vol is elevated
        realized_vol = (atr / price) * np.sqrt(365)  # rough annualised
        vol_scalar = min(1.0, self.target_vol / realized_vol) if realized_vol > 0 else 1.0

        raw_units = (risk_budget_usd / stop_distance_usd) * vol_scalar
        raw_size_usd = raw_units * price

        # Hard cap: never exceed max_position_pct of capital
        max_size_usd = self.capital * self.max_position_pct
        final_size_usd = min(raw_size_usd, max_size_usd)
        final_units = final_size_usd / price

        leverage = final_size_usd / self.capital
        if leverage > self.max_leverage:
            final_size_usd = self.capital * self.max_leverage
            final_units = final_size_usd / price
            leverage = self.max_leverage

        actual_risk_usd = final_units * stop_distance_usd
        actual_risk_pct = actual_risk_usd / self.capital * 100

        logger.debug(
            f"{symbol} | size={final_size_usd:.2f} USD | risk={actual_risk_pct:.2f}% | lev={leverage:.2f}x"
        )

        return SizingResult(
            symbol=symbol,
            position_size_usd=round(final_size_usd, 2),
            position_size_units=round(final_units, 6),
            risk_amount_usd=round(actual_risk_usd, 2),
            risk_pct=round(actual_risk_pct, 4),
            leverage=round(leverage, 3),
        )
