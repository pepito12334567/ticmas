#!/usr/bin/env python3
"""Ejecución diaria del cs-carry en Binance USDT-M. Correr ~00:01 UTC (cron). DRY-RUN por defecto.

  python run_live.py                     # calcula y muestra el plan, no envía órdenes
  python run_live.py --testnet --live    # envía órdenes a testnet.binancefuture.com
  python run_live.py --live              # PRODUCCIÓN (requiere BINANCE_API_KEY / BINANCE_API_SECRET)

Usar una (sub)cuenta DEDICADA en one-way mode: el script asume que TODAS las posiciones abiertas son de la estrategia.
"""
import argparse, json, os, sys, time
from decimal import Decimal, ROUND_DOWN
import pandas as pd
import strategy as st

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")


def build_inputs(c, now, log=print, funding_wait=(30, 20)):
    """Descarga datos y arma close/qv/fund hasta asof = último día completado."""
    today = now.normalize(); asof = today - pd.Timedelta(days=1)
    info = c.exchange_info(); cl, qv = {}, {}
    for i, sym in enumerate(sorted(info)):
        k = c.klines(sym, 400); k = k[k.index < today]    # descarta barra en curso
        if len(k): cl[sym], qv[sym] = k.c, k.qv
    close, qvol = pd.DataFrame(cl).sort_index(), pd.DataFrame(qv).sort_index()
    if close.index[-1] != asof: raise RuntimeError(f"última barra completa {close.index[-1].date()} != {asof.date()}")
    univ = list(st.universe_rank(qvol, asof).pipe(lambda r: r[r <= st.TOP_N]).index)
    log(f"universo: {len(univ)} símbolos con 180d completos (de {len(info)} perps en TRADING)")
    for attempt in range(funding_wait[0]):                # esperar settlement de las 00:00 UTC
        fr = {s: c.funding(s, (today - pd.Timedelta(days=10)).timestamp() * 1000) for s in univ}
        ready = sum(len(v) and v.index[-1] >= today for v in fr.values()) / max(len(univ), 1)
        if ready >= 0.9: break
        log(f"funding de las 00:00 disponible en {ready:.0%} del universo; reintento..."); time.sleep(funding_wait[1])
    else:
        raise RuntimeError("funding de las 00:00 UTC no disponible; abortando (la señal usaría datos desfasados)")
    fund = pd.DataFrame({s: st.daily_funding(v) for s, v in fr.items() if len(v)})
    return info, close, qvol, fund, asof


def plan_orders(w, equity, marks, positions, info, asof):
    """w: pesos objetivo. Devuelve (lista de órdenes, tabla de objetivos). Reduce-only primero."""
    rows, orders = [], []
    for sym in sorted(set(w.index) | set(positions)):
        f = info.get(sym); cur = Decimal(str(positions.get(sym, 0.0)))
        if f is None or sym not in marks:                 # símbolo fuera de TRADING: no se puede operar automáticamente
            rows.append({"symbol": sym, "w": w.get(sym, 0), "note": "NO OPERABLE - revisar manualmente"}); continue
        tgt_not = float(w.get(sym, 0.0)) * equity
        q = (Decimal(str(abs(tgt_not) / marks[sym])) // f["step"]) * f["step"]       # redondeo hacia 0
        if q < f["min_qty"] or q * Decimal(str(marks[sym])) < f["min_notional"]: q = Decimal(0)
        tgt = q if tgt_not > 0 else -q
        rows.append({"symbol": sym, "w": float(w.get(sym, 0)), "target_qty": float(tgt), "current_qty": float(cur),
                     "mark": marks[sym], "target_notional": float(tgt) * marks[sym]})
        legs = []
        if cur != 0 and tgt != 0 and (cur > 0) != (tgt > 0): legs = [(-cur, True), (tgt, False)]   # flip: cerrar y abrir
        elif tgt != cur: legs = [(tgt - cur, abs(tgt) < abs(cur))]
        for d, red in legs:
            if d == 0: continue
            full_close = red and abs(d) == abs(cur)
            if not full_close and abs(d) * Decimal(str(marks[sym])) < f["min_notional"]: continue   # polvo
            n = abs(d); mx = f["max_qty"]
            while n > 0:                                  # trocear si excede maxQty
                part = min(n, mx); n -= part
                orders.append({"symbol": sym, "side": "BUY" if d > 0 else "SELL", "qty": part, "reduce_only": red,
                               "cid": f"cc{asof:%y%m%d}{sym[:14]}{'R' if red else 'O'}{len(orders)}"[:36]})
    orders.sort(key=lambda o: not o["reduce_only"])        # primero reducir, luego abrir
    return orders, pd.DataFrame(rows)


def sanity(w, orders, equity, marks, positions, args):
    errs = []
    if w.empty: errs.append("sin pesos objetivo (menos de 9 nombres elegibles)")
    else:
        if abs(w.abs().sum() - 1) > 1e-6 or abs(w.sum()) > 1e-6: errs.append(f"gross/neto anómalos: {w.abs().sum():.3f}/{w.sum():.3f}")
        if w.abs().max() > args.max_weight: errs.append(f"peso máximo {w.abs().max():.2f} > {args.max_weight}")
    traded = sum(float(o["qty"]) * marks[o["symbol"]] for o in orders)
    if traded > args.max_turnover * equity and not args.first_run: errs.append(f"turnover {traded / equity:.2f}x > {args.max_turnover}x (usa --first-run para el alta inicial)")
    gross_now = sum(abs(a) * marks.get(s, 0) for s, a in positions.items())
    if gross_now > 1.5 * equity: errs.append(f"gross actual {gross_now / equity:.2f}x del capital: ¿posiciones ajenas a la estrategia?")
    return errs


def main(argv=None, client=None, now=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="enviar órdenes (sin esto: dry-run)")
    ap.add_argument("--testnet", action="store_true")
    ap.add_argument("--capital-fraction", type=float, default=1.0, help="fracción del equity que opera la estrategia")
    ap.add_argument("--leverage", type=int, default=3, help="apalancamiento por símbolo (solo afecta margen, no el sizing)")
    ap.add_argument("--max-weight", type=float, default=0.25, help="abortar si algún peso supera este valor (fracción del gross)")
    ap.add_argument("--max-turnover", type=float, default=1.0, help="abortar si el notional operado supera X veces el capital")
    ap.add_argument("--first-run", action="store_true", help="permite turnover grande (primer día)")
    args = ap.parse_args(argv)
    if client is None:
        from binance_futures import BinanceFutures
        client = BinanceFutures(testnet=args.testnet); client.sync_time()
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    os.makedirs(STATE, exist_ok=True); day = f"{now:%Y-%m-%d}"
    marker = os.path.join(STATE, f"done_{day}{'_testnet' if args.testnet else ''}")
    if args.live and os.path.exists(marker): print("ya ejecutado hoy:", marker); return 0
    if now.hour == 0 and now.minute == 0 and now.second < 3: time.sleep(3)

    info, close, qv, fund, asof = build_inputs(client, now)
    w = st.target_weights(close, qv, fund, asof)
    if args.live or args.testnet:
        if client.dual_side(): print("ABORT: cuenta en hedge mode; usar one-way"); return 2
    equity = client.equity() * args.capital_fraction if (args.live or client.key) else float(os.environ.get("DRY_RUN_EQUITY", 10000))
    marks, positions = client.mark_prices(), (client.positions() if (args.live or client.key) else {})
    orders, table = plan_orders(w, equity, marks, positions, info, asof)
    pd.set_option("display.width", 200); print(f"asof {asof.date()}  equity usado {equity:,.2f} USDT"); print(table.round(4).to_string(index=False))
    print(f"\n{len(orders)} órdenes ({sum(o['reduce_only'] for o in orders)} reduce-only)")
    errs = sanity(w, orders, equity, marks, positions, args)
    if errs: print("ABORT - chequeos fallidos:\n  " + "\n  ".join(errs)); return 3
    table.to_csv(os.path.join(STATE, f"targets_{day}.csv"), index=False)
    if not args.live: print("DRY-RUN: no se enviaron órdenes."); return 0

    for sym in sorted({o["symbol"] for o in orders}):
        try: client.set_leverage(sym, args.leverage)
        except Exception as e: print("leverage", sym, e)
    log = open(os.path.join(STATE, f"orders_{day}.jsonl"), "a"); fails = 0
    for o in orders:
        try:
            r = client.market_order(o["symbol"], o["side"], o["qty"], o["reduce_only"], o["cid"])
            rec = {**o, "qty": str(o["qty"]), "status": r.get("status"), "avgPrice": r.get("avgPrice"), "executedQty": r.get("executedQty")}
        except Exception as e:
            fails += 1; rec = {**o, "qty": str(o["qty"]), "error": str(e)}
        log.write(json.dumps(rec) + "\n"); log.flush(); print(rec)
    if fails: print(f"ATENCIÓN: {fails} órdenes fallaron; revisar y re-ejecutar (idempotente: recalcula deltas)."); return 4
    open(marker, "w").write(str(now)); print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
