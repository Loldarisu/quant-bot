import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint, adfuller
from dataclasses import dataclass
from typing import Optional


@dataclass
class PairStats:
    symbol_a: str
    symbol_b: str
    hedge_ratio: float
    half_life: float
    is_cointegrated: bool
    adf_pvalue: float
    coint_pvalue: float


class ZScoreEngine:
    """
    Pairs trading / mean reversion via z-score of the spread.
    Entry: |z| > entry_threshold
    Exit:  |z| < exit_threshold
    """

    def __init__(
        self,
        window: int = 60,
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
        coint_pvalue: float = 0.05,
    ):
        self.window = window
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.coint_pvalue = coint_pvalue

    def compute_hedge_ratio(self, price_a: pd.Series, price_b: pd.Series) -> float:
        """OLS regression to find hedge ratio (beta)."""
        log_a = np.log(price_a.dropna())
        log_b = np.log(price_b.dropna())
        idx = log_a.index.intersection(log_b.index)
        x = log_b.loc[idx].values
        y = log_a.loc[idx].values
        beta = np.polyfit(x, y, 1)[0]
        return float(beta)

    def compute_spread(self, price_a: pd.Series, price_b: pd.Series, hedge_ratio: float) -> pd.Series:
        return np.log(price_a) - hedge_ratio * np.log(price_b)

    def compute_half_life(self, spread: pd.Series) -> float:
        """Ornstein-Uhlenbeck half-life estimate."""
        spread_lag = spread.shift(1).dropna()
        spread_diff = spread.diff().dropna()
        idx = spread_diff.index.intersection(spread_lag.index)
        beta = np.polyfit(spread_lag.loc[idx], spread_diff.loc[idx], 1)[0]
        if beta >= 0:
            return float("inf")
        return float(-np.log(2) / beta)

    def compute_zscore(self, spread: pd.Series) -> pd.Series:
        mean = spread.rolling(self.window).mean()
        std = spread.rolling(self.window).std()
        return (spread - mean) / std

    def check_cointegration(self, price_a: pd.Series, price_b: pd.Series) -> tuple[float, float]:
        """Returns (coint_pvalue, adf_pvalue)."""
        score, p_coint, _ = coint(np.log(price_a), np.log(price_b))
        hedge_ratio = self.compute_hedge_ratio(price_a, price_b)
        spread = self.compute_spread(price_a, price_b, hedge_ratio)
        adf_result = adfuller(spread.dropna())
        return float(p_coint), float(adf_result[1])

    def analyse_pair(self, symbol_a: str, price_a: pd.Series, symbol_b: str, price_b: pd.Series) -> PairStats:
        p_coint, p_adf = self.check_cointegration(price_a, price_b)
        hedge_ratio = self.compute_hedge_ratio(price_a, price_b)
        spread = self.compute_spread(price_a, price_b, hedge_ratio)
        half_life = self.compute_half_life(spread)
        return PairStats(
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            hedge_ratio=hedge_ratio,
            half_life=half_life,
            is_cointegrated=(p_coint < self.coint_pvalue and p_adf < self.coint_pvalue),
            adf_pvalue=p_adf,
            coint_pvalue=p_coint,
        )

    def get_signal(self, zscore: float) -> Optional[str]:
        """Returns 'long_a_short_b', 'short_a_long_b', 'exit', or None."""
        if zscore < -self.entry_threshold:
            return "long_a_short_b"
        if zscore > self.entry_threshold:
            return "short_a_long_b"
        if abs(zscore) < self.exit_threshold:
            return "exit"
        return None
