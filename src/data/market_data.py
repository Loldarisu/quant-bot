import ccxt.async_support as ccxt
import pandas as pd
import asyncio
from loguru import logger
from typing import Optional


class MarketDataFetcher:
    def __init__(self, exchange_id: str, api_key: str = "", secret: str = "", passphrase: str = ""):
        self.exchange_id = exchange_id
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

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
    ) -> pd.DataFrame:
        try:
            raw = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            logger.error(f"fetch_ohlcv {symbol}: {e}")
            raise

    async def fetch_ticker(self, symbol: str) -> dict:
        try:
            return await self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"fetch_ticker {symbol}: {e}")
            raise

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        try:
            return await self.exchange.fetch_order_book(symbol, limit)
        except Exception as e:
            logger.error(f"fetch_order_book {symbol}: {e}")
            raise

    async def close(self):
        await self.exchange.close()
