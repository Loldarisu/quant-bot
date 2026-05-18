import ccxt.async_support as ccxt
import pandas as pd
from loguru import logger
from typing import List, Dict


class FundingRateFetcher:
    """
    Fetch and analyse perpetual funding rates across symbols.
    Positive funding = longs pay shorts (short bias is rewarded).
    Negative funding = shorts pay longs (long bias is rewarded).
    """

    def __init__(self, exchange_id: str, api_key: str = "", secret: str = "", passphrase: str = ""):
        market_type = "swap" if exchange_id == "okx" else "future"
        hostname = "my.okx.com" if exchange_id == "okx" else ""
        self.exchange = getattr(ccxt, exchange_id)({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
            "enableRateLimit": True,
            "hostname": hostname,
            "options": {"defaultType": market_type},
        })

    async def fetch_current_funding(self, symbol: str) -> Dict:
        try:
            info = await self.exchange.fetch_funding_rate(symbol)
            return {
                "symbol": symbol,
                "funding_rate": info["fundingRate"],
                "next_funding_time": info.get("nextFundingDatetime"),
                "timestamp": info.get("datetime"),
            }
        except Exception as e:
            logger.error(f"fetch_current_funding {symbol}: {e}")
            raise

    async def fetch_funding_history(self, symbol: str, limit: int = 200) -> pd.DataFrame:
        try:
            history = await self.exchange.fetch_funding_rate_history(symbol, limit=limit)
            df = pd.DataFrame(history)
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("datetime", inplace=True)
            return df[["fundingRate"]]
        except Exception as e:
            logger.error(f"fetch_funding_history {symbol}: {e}")
            raise

    async def scan_opportunities(self, symbols: List[str], min_rate: float = 0.0003) -> pd.DataFrame:
        """Return symbols where |funding_rate| >= min_rate (annualised ~33%)."""
        results = []
        for sym in symbols:
            try:
                data = await self.fetch_current_funding(sym)
                rate = data["funding_rate"]
                annualised = rate * 3 * 365 * 100  # 3 payments/day → % per year
                results.append({
                    "symbol": sym,
                    "funding_rate": rate,
                    "annualised_pct": round(annualised, 2),
                    "direction": "SHORT_PERP" if rate > 0 else "LONG_PERP",
                })
            except Exception:
                continue

        df = pd.DataFrame(results)
        if df.empty:
            return df
        df = df[df["funding_rate"].abs() >= min_rate]
        return df.sort_values("annualised_pct", ascending=False)

    async def close(self):
        await self.exchange.close()
