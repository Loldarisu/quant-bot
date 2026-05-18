"""
Grid Trading — captura oscilações de preço num range definido.
Ideal para mercados ranging (sem tendência clara).

Usage: python run_grid.py
"""
import asyncio
import os
from dotenv import load_dotenv
from loguru import logger

from src.data.market_data import MarketDataFetcher
from src.strategies.grid_trading import GridStrategy
from src.monitoring.telegram_alerts import TelegramAlerter

load_dotenv("config/.env" if os.path.exists("config/.env") else ".env")

# ── Configuração ──────────────────────────────────────────────────────────────
EXCHANGE      = "okx"
SYMBOL        = "BTC/USDT:USDT"
PAPER_TRADING = True          # ← manter True até validar

# Range da grelha — ajusta com base no mercado actual
# BTC está a ~$76.600, range recente: $70.500 → $80.000
PRICE_LOW     = 70_000
PRICE_HIGH    = 82_000
N_LEVELS      = 24            # 24 níveis → spacing de $500

# Capital por nível (USD) — total investido = N_LEVELS × capital_per_grid
# Com $1000 de capital: 24 × $41 = $984 investidos
CAPITAL_TOTAL = 1_000
CAPITAL_PER_GRID = CAPITAL_TOTAL / N_LEVELS

CHECK_INTERVAL = 30           # segundos entre verificações
# ─────────────────────────────────────────────────────────────────────────────


async def main():
    alerter = TelegramAlerter(
        token=os.getenv("TELEGRAM_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    # Buscar preço actual para mostrar configuração
    market = MarketDataFetcher(EXCHANGE)
    ticker = await market.fetch_ticker(SYMBOL)
    current_price = float(ticker["last"])
    await market.close()

    spacing = (PRICE_HIGH - PRICE_LOW) / N_LEVELS
    fee_rate = 0.001  # 0.1% taker OKX
    profit_per_cycle = CAPITAL_PER_GRID * (spacing / current_price - 2 * fee_rate)
    fee_per_cycle = CAPITAL_PER_GRID * fee_rate * 2
    cycles_to_roi = (CAPITAL_TOTAL * 0.01) / profit_per_cycle if profit_per_cycle > 0 else 0
    mode_label = "PAPER TRADING" if PAPER_TRADING else "LIVE"

    logger.info(
        f"\n  =========================================="
        f"\n   GRID TRADING - {SYMBOL}"
        f"\n  =========================================="
        f"\n   Preco actual   : ${current_price:>10,.2f}"
        f"\n   Range          : ${PRICE_LOW:,} -> ${PRICE_HIGH:,}"
        f"\n   Niveis         : {N_LEVELS}"
        f"\n   Spacing        : ${spacing:,.0f} por nivel"
        f"\n   Capital/nivel  : ${CAPITAL_PER_GRID:.2f}"
        f"\n   Capital total  : ${CAPITAL_TOTAL:.2f}"
        f"\n  ------------------------------------------"
        f"\n   Fee/ciclo      : ${fee_per_cycle:.4f}"
        f"\n   Lucro/ciclo    : ${profit_per_cycle:.4f}"
        f"\n   Ciclos p/ 1%   : ~{cycles_to_roi:.0f} ciclos"
        f"\n   Modo           : {mode_label}"
        f"\n  =========================================="
    )

    if not PRICE_LOW < current_price < PRICE_HIGH:
        logger.error(f"Preço actual ${current_price:,.2f} está FORA do range definido! Ajusta PRICE_LOW/PRICE_HIGH.")
        return

    grid = GridStrategy(
        symbol=SYMBOL,
        price_low=PRICE_LOW,
        price_high=PRICE_HIGH,
        n_levels=N_LEVELS,
        capital_per_grid=CAPITAL_PER_GRID,
        exchange_id=EXCHANGE,
        api_key=os.getenv("EXCHANGE_API_KEY", ""),
        secret=os.getenv("EXCHANGE_SECRET", ""),
        paper_trading=PAPER_TRADING,
        fee_rate=fee_rate,
        check_interval=CHECK_INTERVAL,
    )

    try:
        await grid.run(alerter)
    except KeyboardInterrupt:
        logger.info("Grid parada pelo utilizador")
    finally:
        await grid.stop()


if __name__ == "__main__":
    asyncio.run(main())
