"""
Monitor de funding rates — corre em loop e alerta quando surgem oportunidades.
Não opera — apenas monitoriza e notifica via Telegram.

Usage: python monitor.py
"""
import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

from src.data.funding_rates import FundingRateFetcher
from src.monitoring.telegram_alerts import TelegramAlerter

load_dotenv("config/.env" if os.path.exists("config/.env") else ".env")

# ── Configuração ──────────────────────────────────────────────
EXCHANGE        = "okx"
SCAN_INTERVAL   = 60 * 30          # verificar de 30 em 30 minutos
MIN_RATE_ALERT  = 0.0005           # 0.05% por período → ~55% APY  (threshold para alertar)
MIN_RATE_HIGH   = 0.001            # 0.1%  por período → ~110% APY (threshold "oportunidade forte")

SYMBOLS = [
    # ── Crypto blue chips ─────────────────────────────────────
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
    "BNB/USDT:USDT",
    "DOGE/USDT:USDT",
    "SUI/USDT:USDT",
    "WIF/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
    "DOT/USDT:USDT",
    "ADA/USDT:USDT",
    "INJ/USDT:USDT",
    "TAO/USDT:USDT",
    "HYPE/USDT:USDT",
    "TON/USDT:USDT",
    "WLD/USDT:USDT",
    # ── Commodities tokenizadas ───────────────────────────────
    "XAU/USDT:USDT",   # Ouro
    "XAG/USDT:USDT",   # Prata
    "XPT/USDT:USDT",   # Platina
    "XPD/USDT:USDT",   # Paládio
    "CL/USDT:USDT",    # Petróleo (Crude)
    # ── Stocks tokenizados ────────────────────────────────────
    "MU/USDT:USDT",    # Micron Technology
    "SNDK/USDT:USDT",  # SanDisk
    "INTC/USDT:USDT",  # Intel
    "SPACEX/USDT:USDT",# SpaceX
    # ── Alta volatilidade / meme ──────────────────────────────
    "TRUMP/USDT:USDT",
    "EDEN/USDT:USDT",
    "BERA/USDT:USDT",
    "APE/USDT:USDT",
]
# ─────────────────────────────────────────────────────────────


async def scan_once(fetcher: FundingRateFetcher, alerter: TelegramAlerter) -> list:
    """Faz um scan e devolve oportunidades encontradas."""
    opportunities = []

    for symbol in SYMBOLS:
        try:
            data = await fetcher.fetch_current_funding(symbol)
            rate = data["funding_rate"]
            if rate is None:
                continue

            abs_rate = abs(rate)
            if abs_rate < MIN_RATE_ALERT:
                continue

            annualised = abs_rate * 3 * 365 * 100
            direction  = "SHORT perp + LONG spot" if rate > 0 else "LONG perp + SHORT spot"
            strength   = "🔥 FORTE" if abs_rate >= MIN_RATE_HIGH else "⚡ MODERADA"

            opportunities.append({
                "symbol":      symbol,
                "rate":        rate,
                "annualised":  annualised,
                "direction":   direction,
                "strength":    strength,
            })

        except Exception as e:
            logger.debug(f"Skip {symbol}: {e}")
            continue

    return opportunities


async def main():
    alerter = TelegramAlerter(
        token=os.getenv("TELEGRAM_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    fetcher = FundingRateFetcher(EXCHANGE)

    logger.info(f"Monitor iniciado — exchange={EXCHANGE} | threshold={MIN_RATE_ALERT*100:.3f}% | intervalo={SCAN_INTERVAL//60}min")
    await alerter._send(
        f"👁️ <b>Monitor de Funding Rates iniciado</b>\n"
        f"Exchange: <code>{EXCHANGE.upper()}</code>\n"
        f"Threshold: <b>{MIN_RATE_ALERT*100:.3f}%</b> por período (~{MIN_RATE_ALERT*3*365*100:.0f}% APY)\n"
        f"Símbolos: {len(SYMBOLS)}\n"
        f"Intervalo: {SCAN_INTERVAL//60} minutos"
    )

    scan_count = 0

    try:
        while True:
            scan_count += 1
            now = datetime.utcnow().strftime("%H:%M:%S UTC")
            logger.info(f"[Scan #{scan_count}] {now}")

            opportunities = await scan_once(fetcher, alerter)

            if opportunities:
                # Ordenar por rate absoluta
                opportunities.sort(key=lambda x: abs(x["rate"]), reverse=True)

                lines = [f"🚨 <b>OPORTUNIDADES ENCONTRADAS</b> — {now}\n"]
                for opp in opportunities:
                    lines.append(
                        f"{opp['strength']}\n"
                        f"  <code>{opp['symbol']}</code>\n"
                        f"  Rate: <b>{opp['rate']*100:.4f}%</b> | APY: <b>{opp['annualised']:.1f}%</b>\n"
                        f"  Acção: {opp['direction']}\n"
                    )

                msg = "\n".join(lines)
                await alerter._send(msg)
                logger.info(f"Alertas enviados: {len(opportunities)} oportunidade(s)")

                # Log no terminal mesmo sem Telegram
                for opp in opportunities:
                    logger.info(
                        f"{opp['strength']} | {opp['symbol']} | "
                        f"rate={opp['rate']*100:.4f}% | APY={opp['annualised']:.1f}% | {opp['direction']}"
                    )
            else:
                logger.info(f"Nenhuma oportunidade acima de {MIN_RATE_ALERT*100:.3f}% — mercado calmo")

            # Resumo de hora em hora (a cada 2 scans de 30min)
            if scan_count % 2 == 0:
                logger.info(f"[Resumo] {scan_count} scans realizados sem oportunidades relevantes")

            await asyncio.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Monitor parado pelo utilizador")
        await alerter._send("🛑 <b>Monitor parado</b>")
    finally:
        await fetcher.close()


if __name__ == "__main__":
    asyncio.run(main())
