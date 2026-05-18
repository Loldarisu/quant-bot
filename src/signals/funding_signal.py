import pandas as pd
from dataclasses import dataclass
from typing import Optional
from loguru import logger


@dataclass
class FundingOpportunity:
    symbol: str
    funding_rate: float
    annualised_pct: float
    action: str          # "short_perp_long_spot" or "long_perp_short_spot"
    confidence: str      # "high" | "medium" | "low"


class FundingSignalGenerator:
    """
    Generates funding rate arbitrage signals.

    Strategy:
    - If funding_rate > threshold: SHORT perp + LONG spot (collect funding)
    - If funding_rate < -threshold: LONG perp + SHORT spot (collect negative funding)

    Edge: market-neutral, earns funding every 8 hours without directional risk.
    """

    def __init__(
        self,
        min_rate_8h: float = 0.0003,   # 0.03% per 8h → ~33% annualised
        high_rate_8h: float = 0.001,   # 0.1%  per 8h → ~110% annualised
        min_history_periods: int = 12, # at least 12 × 8h = 4 days of history
    ):
        self.min_rate_8h = min_rate_8h
        self.high_rate_8h = high_rate_8h
        self.min_history_periods = min_history_periods

    def evaluate(self, symbol: str, current_rate: float, history: pd.Series) -> Optional[FundingOpportunity]:
        """
        Evaluate a symbol for a funding arb opportunity.

        Parameters
        ----------
        symbol       : e.g. "BTC/USDT:USDT"
        current_rate : funding rate for the next 8h window
        history      : pd.Series of past funding rates (index=datetime)
        """
        if abs(current_rate) < self.min_rate_8h:
            return None

        if len(history) >= self.min_history_periods:
            avg_rate = history.tail(self.min_history_periods).mean()
            # Only enter if current rate is in the same direction as recent average
            if current_rate > 0 and avg_rate < 0:
                logger.debug(f"{symbol}: rate recently flipped — skipping")
                return None
            if current_rate < 0 and avg_rate > 0:
                logger.debug(f"{symbol}: rate recently flipped — skipping")
                return None

        annualised = current_rate * 3 * 365 * 100

        if abs(current_rate) >= self.high_rate_8h:
            confidence = "high"
        elif abs(current_rate) >= self.min_rate_8h * 2:
            confidence = "medium"
        else:
            confidence = "low"

        action = "short_perp_long_spot" if current_rate > 0 else "long_perp_short_spot"

        return FundingOpportunity(
            symbol=symbol,
            funding_rate=current_rate,
            annualised_pct=round(annualised, 2),
            action=action,
            confidence=confidence,
        )

    def rank_opportunities(self, opportunities: list[FundingOpportunity]) -> list[FundingOpportunity]:
        """Sort by absolute funding rate descending."""
        return sorted(opportunities, key=lambda x: abs(x.funding_rate), reverse=True)
