import asyncio
import os
import yaml
import signal
from dotenv import load_dotenv
from loguru import logger
from src.bot import QuantBot

load_dotenv("config/.env" if os.path.exists("config/.env") else ".env")


def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


async def main():
    config = load_config()

    log_level = config.get("monitoring", {}).get("log_level", "INFO")
    log_file = config.get("monitoring", {}).get("log_file", "logs/bot.log")
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level=log_level, colorize=True)
    logger.add(log_file, rotation="1 day", retention="30 days", level="DEBUG")

    bot = QuantBot(
        config=config,
        api_key=os.getenv("EXCHANGE_API_KEY", ""),
        secret=os.getenv("EXCHANGE_SECRET", ""),
        tg_token=os.getenv("TELEGRAM_TOKEN", ""),
        tg_chat=os.getenv("TELEGRAM_CHAT_ID", ""),
        passphrase=os.getenv("EXCHANGE_PASSPHRASE", ""),
    )

    # add_signal_handler is Unix-only; on Windows use signal.signal instead
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))
    except NotImplementedError:
        # Windows fallback — Ctrl+C will raise KeyboardInterrupt caught below
        pass

    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Interrupted — stopping bot...")
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
