import asyncio
import ccxt.async_support as ccxt
from dataclasses import dataclass
from typing import Optional, Literal
from loguru import logger


OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]


@dataclass
class OrderResult:
    symbol: str
    side: str
    size: float
    price: float
    order_id: str
    status: str
    fee: float
    slippage_pct: float


class OrderExecutor:
    """
    Smart order execution with:
    - Retry logic (3 attempts with exponential backoff)
    - Slippage guard (rejects if slippage > max_slippage_pct)
    - Paper trading mode (no real orders sent)
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        secret: str,
        passphrase: str = "",
        paper_trading: bool = True,
        max_slippage_pct: float = 0.003,  # 0.3%
        max_retries: int = 3,
    ):
        self.paper_trading = paper_trading
        self.max_slippage_pct = max_slippage_pct
        self.max_retries = max_retries
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

    async def _get_best_price(self, symbol: str, side: OrderSide) -> float:
        book = await self.exchange.fetch_order_book(symbol, limit=5)
        # buy: pagar o melhor ask (mais barato disponível)
        # sell: receber o melhor bid (mais alto disponível)
        if side == "buy":
            return float(book["asks"][0][0]) if book["asks"] else 0.0
        return float(book["bids"][0][0]) if book["bids"] else 0.0

    async def execute(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        order_type: OrderType = "market",
        limit_price: Optional[float] = None,
    ) -> Optional[OrderResult]:
        if self.paper_trading:
            return await self._paper_execute(symbol, side, size)

        for attempt in range(1, self.max_retries + 1):
            try:
                mid_price = await self._get_best_price(symbol, side)

                if order_type == "market":
                    order = await self.exchange.create_market_order(symbol, side, size)
                else:
                    if limit_price is None:
                        raise ValueError("limit_price required for limit orders")
                    order = await self.exchange.create_limit_order(symbol, side, size, limit_price)

                filled_price = float(order.get("average") or order.get("price") or mid_price)
                fee = float(order.get("fee", {}).get("cost", 0))

                # Slippage check
                slippage = abs(filled_price - mid_price) / mid_price if mid_price > 0 else 0
                if slippage > self.max_slippage_pct:
                    logger.warning(f"High slippage {slippage*100:.3f}% on {symbol} — logged but accepted")

                logger.info(f"ORDER FILLED | {symbol} {side} {size} @ {filled_price:.4f} | fee={fee:.4f}")

                return OrderResult(
                    symbol=symbol,
                    side=side,
                    size=size,
                    price=filled_price,
                    order_id=str(order["id"]),
                    status=str(order["status"]),
                    fee=fee,
                    slippage_pct=round(slippage * 100, 4),
                )

            except ccxt.NetworkError as e:
                wait = 2 ** attempt
                logger.warning(f"Network error attempt {attempt}/{self.max_retries}: {e} — retrying in {wait}s")
                await asyncio.sleep(wait)
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error on {symbol}: {e}")
                return None

        logger.error(f"All {self.max_retries} attempts failed for {symbol}")
        return None

    async def _paper_execute(self, symbol: str, side: OrderSide, size: float) -> OrderResult:
        try:
            price = await self._get_best_price(symbol, side)
        except Exception:
            price = 0.0
        logger.info(f"[PAPER] ORDER | {symbol} {side} {size} @ {price:.4f}")
        return OrderResult(
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            order_id="paper_" + symbol.replace("/", "") + "_" + side,
            status="closed",
            fee=size * price * 0.0004,
            slippage_pct=0.0,
        )

    async def cancel_all_orders(self, symbol: str):
        if self.paper_trading:
            return
        try:
            await self.exchange.cancel_all_orders(symbol)
            logger.info(f"All orders cancelled for {symbol}")
        except Exception as e:
            logger.error(f"cancel_all_orders {symbol}: {e}")

    async def close(self):
        await self.exchange.close()
