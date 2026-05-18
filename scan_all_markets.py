"""
Scan completo OKX — todos os perpetuais por volume.
Cruza volume 24h com funding rate para encontrar as melhores oportunidades.
"""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv("config/.env")

FEE_ROUND_TRIP = 0.00027   # 0.027% round trip OKX limit orders
MIN_VOLUME_USD = 5_000_000  # só mercados com > $5M volume 24h
TOP_N = 40                  # top N por volume


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

    print("A carregar mercados OKX...")
    await exchange.load_markets()

    # Buscar todos os tickers de uma vez (1 chamada)
    print("A buscar tickers (volume 24h)...")
    tickers = await exchange.fetch_tickers()

    # Filtrar só USDT perpetuais com volume suficiente
    swap_tickers = []
    for symbol, t in tickers.items():
        if not symbol.endswith(":USDT"):
            continue
        last = t.get("last") or 0
        base_vol = t.get("baseVolume") or 0
        vol_usd = base_vol * last  # baseVolume × preço = volume em USD
        if vol_usd < MIN_VOLUME_USD:
            continue
        swap_tickers.append({
            "symbol": symbol,
            "volume_usd": vol_usd,
            "last": last,
            "change_pct": t.get("percentage") or 0,
        })

    swap_tickers.sort(key=lambda x: x["volume_usd"], reverse=True)
    top = swap_tickers[:TOP_N]

    print(f"\nEncontrados {len(swap_tickers)} perpetuais USDT com >${MIN_VOLUME_USD/1e6:.0f}M volume")
    print(f"A analisar top {TOP_N} por volume...\n")

    # Buscar funding rate para cada um
    results = []
    for t in top:
        sym = t["symbol"]
        try:
            fr = await exchange.fetch_funding_rate(sym)
            rate = fr.get("fundingRate") or 0
            hist = await exchange.fetch_funding_rate_history(sym, limit=21)
            rates_h = [h["fundingRate"] for h in hist if h.get("fundingRate") is not None]
            avg_7d = sum(rates_h) / len(rates_h) if rates_h else 0
            max_7d = max(rates_h, key=abs) if rates_h else 0
            results.append({
                "symbol": sym,
                "volume_usd": t["volume_usd"],
                "change_pct": t["change_pct"],
                "current_rate": rate,
                "avg_7d": avg_7d,
                "max_7d": max_7d,
                "cur_apy": rate * 3 * 365 * 100,
                "avg_apy": avg_7d * 3 * 365 * 100,
            })
        except Exception as e:
            print(f"  skip {sym}: {e}")

    await exchange.close()

    # ── Tabela principal ──────────────────────────────────────────
    print(f"{'Símbolo':<22} {'Vol24h':>10}  {'Chg%':>6}  {'Atual%':>8}  {'APY':>7}  {'Avg7d%':>8}  {'APY7d':>7}  {'Max7d%':>8}")
    print("-" * 95)
    for r in results:
        vol_m = r["volume_usd"] / 1e6
        print(
            f"{r['symbol']:<22} {vol_m:>9.0f}M"
            f"  {r['change_pct']:>+6.1f}%"
            f"  {r['current_rate']*100:>+8.5f}%"
            f"  {r['cur_apy']:>+7.1f}%"
            f"  {r['avg_7d']*100:>+8.5f}%"
            f"  {r['avg_apy']:>+7.1f}%"
            f"  {abs(r['max_7d'])*100:>8.4f}%"
        )

    # ── Melhores oportunidades agora ─────────────────────────────
    print("\n=== OPORTUNIDADES AGORA (atual > break-even 0.027%) ===")
    now_opps = [r for r in results if abs(r["current_rate"]) > FEE_ROUND_TRIP]
    now_opps.sort(key=lambda x: abs(x["current_rate"]), reverse=True)
    if now_opps:
        for r in now_opps:
            action = "SHORT perp + LONG spot" if r["current_rate"] > 0 else "LONG perp + SHORT spot"
            print(f"  {r['symbol']:<22} {r['current_rate']*100:+.5f}%  ({r['cur_apy']:+.1f}%APY)  Vol=${r['volume_usd']/1e6:.0f}M  -> {action}")
    else:
        print("  Nenhuma acima do break-even agora.")

    # ── Melhores em média 7d ──────────────────────────────────────
    print("\n=== MELHORES MÉDIA 7 DIAS (avg > break-even) ===")
    avg_opps = [r for r in results if abs(r["avg_7d"]) > FEE_ROUND_TRIP]
    avg_opps.sort(key=lambda x: abs(x["avg_7d"]), reverse=True)
    if avg_opps:
        for r in avg_opps:
            print(f"  {r['symbol']:<22} avg={r['avg_7d']*100:+.5f}%  APY={r['avg_apy']:+.1f}%  max={abs(r['max_7d'])*100:.4f}%  Vol=${r['volume_usd']/1e6:.0f}M")
    else:
        print("  Nenhuma com média positiva acima do break-even esta semana.")

    # ── Mercados com picos grandes ────────────────────────────────
    print("\n=== PICOS MAIORES 7 DIAS (max > 0.05%) ===")
    peak_opps = [r for r in results if abs(r["max_7d"]) > 0.0005]
    peak_opps.sort(key=lambda x: abs(x["max_7d"]), reverse=True)
    if peak_opps:
        for r in peak_opps:
            print(f"  {r['symbol']:<22} max={abs(r['max_7d'])*100:.4f}%  atual={r['current_rate']*100:+.5f}%  Vol=${r['volume_usd']/1e6:.0f}M")
    else:
        print("  Nenhum pico relevante.")

    # ── Resumo do ambiente ────────────────────────────────────────
    print("\n=== RESUMO DO AMBIENTE ===")
    pos_rates = [r for r in results if r["current_rate"] > 0]
    neg_rates = [r for r in results if r["current_rate"] < 0]
    above_thresh = [r for r in results if abs(r["current_rate"]) > FEE_ROUND_TRIP]
    avg_rate = sum(r["current_rate"] for r in results) / len(results) if results else 0

    print(f"  Total analisados:          {len(results)}")
    print(f"  Com funding positiva:      {len(pos_rates)} ({len(pos_rates)/len(results)*100:.0f}%)")
    print(f"  Com funding negativa:      {len(neg_rates)} ({len(neg_rates)/len(results)*100:.0f}%)")
    print(f"  Acima break-even agora:    {len(above_thresh)}")
    print(f"  Taxa média do mercado:     {avg_rate*100:+.5f}% ({avg_rate*3*365*100:+.1f}% APY)")
    print()
    if avg_rate > 0.0001:
        print("  SENTIMENTO: Mercado BULLISH — longs dominantes, funding positiva generalizada")
    elif avg_rate < -0.0001:
        print("  SENTIMENTO: Mercado BEARISH — shorts dominantes, funding negativa")
    else:
        print("  SENTIMENTO: Mercado NEUTRO — pouco desequilíbrio entre longs/shorts")


if __name__ == "__main__":
    asyncio.run(main())
