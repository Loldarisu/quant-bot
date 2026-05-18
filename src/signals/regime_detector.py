import numpy as np
import pandas as pd
from enum import Enum
from loguru import logger


class Regime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOL = "high_vol"


class RegimeDetector:
    """
    Identifies market regime using Hurst Exponent + ADX.
    - H < 0.45 → mean-reverting (ranging)  → use mean reversion strategy
    - H > 0.55 → trending                  → use momentum / trend strategy
    - ADX > 25 confirms trend strength
    """

    def __init__(self, hurst_window: int = 100, adx_period: int = 14, vol_threshold: float = 2.0):
        self.hurst_window = hurst_window
        self.adx_period = adx_period
        self.vol_threshold = vol_threshold

    def hurst_exponent(self, series: pd.Series) -> float:
        """Hurst Exponent via rescaled range (R/S) analysis."""
        ts = series.dropna().values
        n = len(ts)
        if n < 20:
            return 0.5

        lags = range(2, min(100, n // 2))
        tau = []
        for lag in lags:
            chunks = [ts[i:i + lag] for i in range(0, n - lag, lag)]
            if not chunks:
                continue
            rs_values = []
            for chunk in chunks:
                mean = np.mean(chunk)
                deviation = np.cumsum(chunk - mean)
                r = np.max(deviation) - np.min(deviation)
                s = np.std(chunk, ddof=1)
                if s > 0:
                    rs_values.append(r / s)
            if rs_values:
                tau.append((np.log(lag), np.log(np.mean(rs_values))))

        if len(tau) < 2:
            return 0.5

        x = [t[0] for t in tau]
        y = [t[1] for t in tau]
        hurst = np.polyfit(x, y, 1)[0]
        return float(np.clip(hurst, 0.0, 1.0))

    def adx(self, df: pd.DataFrame) -> pd.Series:
        """Average Directional Index."""
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)

        dm_plus = (high - high.shift()).clip(lower=0)
        dm_minus = (low.shift() - low).clip(lower=0)
        dm_plus[dm_plus < dm_minus] = 0
        dm_minus[dm_minus < dm_plus] = 0

        p = self.adx_period
        atr = tr.ewm(span=p, adjust=False).mean()
        di_plus = 100 * dm_plus.ewm(span=p, adjust=False).mean() / atr
        di_minus = 100 * dm_minus.ewm(span=p, adjust=False).mean() / atr
        dx = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus)).fillna(0)
        return dx.ewm(span=p, adjust=False).mean()

    def atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    def detect(self, df: pd.DataFrame) -> Regime:
        if len(df) < self.hurst_window:
            logger.warning("Not enough data for regime detection — defaulting to RANGING")
            return Regime.RANGING

        close = df["close"]
        returns = close.pct_change().dropna()

        # Volatility check first
        atr_series = self.atr(df)
        current_atr = atr_series.iloc[-1]
        avg_atr = atr_series.iloc[-50:].mean()
        if current_atr > avg_atr * self.vol_threshold:
            return Regime.HIGH_VOL

        h = self.hurst_exponent(close.iloc[-self.hurst_window:])
        adx_val = self.adx(df).iloc[-1]

        logger.debug(f"Hurst={h:.3f} | ADX={adx_val:.1f}")

        if h > 0.55 and adx_val > 25:
            direction = "up" if close.iloc[-1] > close.iloc[-20] else "down"
            return Regime.TRENDING_UP if direction == "up" else Regime.TRENDING_DOWN

        if h < 0.45:
            return Regime.RANGING

        return Regime.RANGING
