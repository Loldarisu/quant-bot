"""Analisa funding rates dos ultimos 7 dias na OKX."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv("config/.env")


async def main():
    import ccxt.async_support as ccxt

    exchange = ccxt.okx({
        "apiKey": os.getenv("EXCHANGE_API_KEY"),
        "secret": os.getenv("EXCHANGE_SECRET"),
        "password": os.getenv("EXCHANGE_PASSPHRASE"),
        "enableRateLimit": True,
        "hostname": "my.okx.com",
        "options": {"defaultType": "swap"},
    })

    symbols = [
        "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
        "XRP/USDT:USDT", "BNB/USDT:USDT", "DOGE/USDT:USDT",
        "SUI/USDT:USDT", "WIF/USDT:USDT", "AVAX/USDT:USDT",
        "LINK/USDT:USDT", "DOT/USDT:USDT", "ADA/USDT:USDT",
    ]

    print("=== FUNDING RATES - HISTORICO 7 DIAS ===\n")
    results = {}

    for sym in symbols:
        try:
            hist = await exchange.fetch_funding_rate_history(sym, limit=21)
            rates = [h["fundingRate"] for h in hist if h.get("fundingRate") is not None]
            if not rates:
                continue

            current = await exchange.fetch_funding_rate(sym)
            cur_rate = current["fundingRate"]

            avg = sum(rates) / len(rates)
            max_r = max(rates, key=abs)
            max_pos = max(rates)
            min_neg = min(rates)
            apy = avg * 3 * 365 * 100
            cur_apy = cur_rate * 3 * 365 * 100

            results[sym] = {
                "current": cur_rate,
                "cur_apy": cur_apy,
                "avg_7d": avg,
                "avg_apy": apy,
                "max_7d": max_r,
                "max_pos": max_pos,
                "min_neg": min_neg,
                "n": len(rates),
            }

            sign = "+" if cur_rate >= 0 else ""
            print(
                f"{sym:<20} | atual: {sign}{cur_rate*100:.5f}% ({sign}{cur_apy:.1f}%APY)"
                f" | avg7d: {avg*100:.5f}% ({apy:.1f}%APY)"
                f" | max_abs: {abs(max_r)*100:.4f}%"
            )
        except Exception as e:
            print(f"{sym}: ERRO - {e}")

    await exchange.close()

    print()
    print("=== ANALISE ===")
    fee_break_even = 0.00027  # fees round trip OKX (~0.027%)
    viable = {k: v for k, v in results.items() if abs(v["avg_7d"]) >= fee_break_even}
    strong_peaks = {k: v for k, v in results.items() if abs(v["max_7d"]) >= 0.0008}
    now_opp = {k: v for k, v in results.items() if abs(v["current"]) >= fee_break_even}

    print(f"Break-even taxa (fees round trip OKX): ~0.027% por periodo")
    print(f"Simbolos viaveis em media 7d:  {len(viable)}")
    print(f"Simbolos com pico >= 0.08%:    {len(strong_peaks)}")
    print(f"Oportunidades AGORA:           {len(now_opp)}")

    if viable:
        print("\nViaveis avg 7d (ordenado por taxa absoluta):")
        for k, v in sorted(viable.items(), key=lambda x: abs(x[1]["avg_7d"]), reverse=True):
            print(f"  {k:<20} avg={v['avg_7d']*100:.5f}%  APY={v['avg_apy']:.1f}%  max={abs(v['max_7d'])*100:.4f}%")

    if strong_peaks:
        print("\nPicos fortes nos 7 dias:")
        for k, v in sorted(strong_peaks.items(), key=lambda x: abs(x[1]["max_7d"]), reverse=True):
            print(f"  {k:<20} max={abs(v['max_7d'])*100:.4f}% | atual={v['current']*100:.5f}%")

    print("\nDistribuicao geral (todos):")
    all_avgs = [(k, v["avg_7d"]) for k, v in results.items()]
    all_avgs.sort(key=lambda x: abs(x[1]), reverse=True)
    for k, r in all_avgs:
        bar = "#" * int(abs(r) * 100000)
        print(f"  {k:<20} {r*100:+.5f}%  {bar}")


if __name__ == "__main__":
    asyncio.run(main())
