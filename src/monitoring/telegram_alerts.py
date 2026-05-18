import asyncio
import os
import ssl
import aiohttp
from loguru import logger
from typing import Optional

def _build_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if os.getenv("ENV", "production").lower() == "development":
        # Dev only: bypass self-signed certs from corporate proxies
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class TelegramAlerter:
    """
    Send trading alerts to a Telegram bot.
    Create a bot via @BotFather and get your chat_id via @userinfobot.
    """

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self._ssl_ctx = _build_ssl_ctx()

    async def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            logger.debug(f"[TELEGRAM DISABLED] {text}")
            return False
        url = self.BASE_URL.format(token=self.token)
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}
        try:
            connector = aiohttp.TCPConnector(ssl=self._ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Telegram error {resp.status}")
                        return False
                    return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def trade_opened(self, symbol: str, side: str, size_usd: float, price: float):
        text = (
            f"🟢 <b>TRADE OPENED</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Side: <b>{side}</b>\n"
            f"Size: ${size_usd:,.2f}\n"
            f"Price: ${price:,.4f}"
        )
        await self._send(text)

    async def trade_closed(self, symbol: str, pnl: float, pnl_pct: float, reason: str):
        emoji = "✅" if pnl >= 0 else "🔴"
        text = (
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"PnL: <b>${pnl:+.2f} ({pnl_pct:+.2f}%)</b>\n"
            f"Reason: {reason}"
        )
        await self._send(text)

    async def funding_received(self, symbol: str, amount: float, rate: float):
        text = (
            f"💰 <b>FUNDING RECEIVED</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Amount: <b>${amount:+.4f}</b>\n"
            f"Rate: {rate*100:.4f}%"
        )
        await self._send(text)

    async def circuit_breaker(self, reason: str, daily_pnl: float, drawdown_pct: float):
        text = (
            f"🚨 <b>CIRCUIT BREAKER — BOT HALTED</b>\n"
            f"Reason: <b>{reason}</b>\n"
            f"Daily PnL: ${daily_pnl:+.2f}\n"
            f"Drawdown: {drawdown_pct:.2f}%\n"
            f"<i>Manual intervention required.</i>"
        )
        await self._send(text)

    async def daily_summary(self, capital: float, daily_pnl: float, total_pnl: float, open_pos: int):
        emoji = "📈" if daily_pnl >= 0 else "📉"
        text = (
            f"{emoji} <b>DAILY SUMMARY</b>\n"
            f"Capital: ${capital:,.2f}\n"
            f"Today: <b>${daily_pnl:+.2f}</b>\n"
            f"Total: ${total_pnl:+.2f}\n"
            f"Open positions: {open_pos}"
        )
        await self._send(text)

    async def error_alert(self, context: str, error: str):
        text = (
            f"⚠️ <b>ERROR</b>\n"
            f"Context: {context}\n"
            f"<code>{error[:300]}</code>"
        )
        await self._send(text)
