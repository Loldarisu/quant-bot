"""
Quick backtest script — fetches historical data and runs the funding arb backtester.
Usage: python run_backtest.py
"""
import asyncio
import os
from dotenv import load_dotenv
from loguru import logger

from src.data.funding_rates import FundingRateFetcher
from src.data.market_data import MarketDataFetcher
from src.backtest.backtester import FundingArbBacktester
from src.monitoring.dashboard import print_summary, plot_equity_curve

load_dotenv("config/.env" if os.path.exists("config/.env") else ".env")

SYMBOL = "BTC/USDT:USDT"
EXCHANGE = "okx"
CAPITAL = 10_000


async def run():
    logger.info(f"Fetching backtest data for {SYMBOL} on {EXCHANGE}...")

    funding_fetcher = FundingRateFetcher(EXCHANGE)
    market_fetcher = MarketDataFetcher(EXCHANGE)

    try:
        funding_df = await funding_fetcher.fetch_funding_history(SYMBOL, limit=500)
        price_df = await market_fetcher.fetch_ohlcv(SYMBOL, timeframe="1h", limit=4000)
    finally:
        await funding_fetcher.close()
        await market_fetcher.close()

    logger.info(f"Got {len(funding_df)} funding periods and {len(price_df)} price bars")

    # Debug: mostrar estatísticas das funding rates reais
    fr = funding_df["fundingRate"]
    logger.info(f"Funding Rate — min={fr.min():.6f} max={fr.max():.6f} avg={fr.mean():.6f} positivos={( fr > 0).sum()}")

    backtester = FundingArbBacktester(
        capital=CAPITAL,
        min_funding_rate=fr.abs().quantile(0.5),  # usar mediana como threshold dinâmico
        exit_funding_rate=fr.abs().quantile(0.2),
        fee_rate=0.0004,
        position_pct=0.8,
    )

    result = backtester.run(funding_df, price_df, SYMBOL)
    print_summary(result)

    # Mostrar detalhe de cada trade
    if result.trades:
        print("\n  DETALHE DOS TRADES:")
        print(f"  {'Entrada':<20} {'Saída':<20} {'Lado':<22} {'PnL USD':>10} {'PnL %':>8}")
        print("  " + "-"*84)
        for t in result.trades:
            print(f"  {str(t.entry_time)[:19]:<20} {str(t.exit_time)[:19]:<20} {t.side:<22} {t.pnl:>10.4f} {t.pnl_pct:>8.4f}%")

    fig = plot_equity_curve(result, title=f"Funding Arb — {SYMBOL}")
    fig.write_html("data/backtest_result.html")
    logger.info("Chart saved to data/backtest_result.html")


if __name__ == "__main__":
    asyncio.run(run())
