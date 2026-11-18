"""1) Paridad: target_weights == pesos del backtest. 2) Pipeline completo con cliente simulado (datos históricos)."""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "../..")]
from decimal import Decimal
import numpy as np, pandas as pd
import backtest as b, strategy as st, run_live as rl

kl, qv, rank = b.universe_candidates(); px, ret, fund = b.build(kl, rank)
res, W = b.run(px, ret, fund, rank)
dates = list(res.index[::60]) + list(res.index[-5:])
bad = 0
for t in dates:
    lo = t - pd.Timedelta(days=399)
    w = st.target_weights(px.loc[lo:t], pd.DataFrame({s: d.qv for s, d in kl.items()}).sort_index().loc[lo:t], fund.loc[lo:t], t)
    ref = W.loc[t]; ref = ref[ref != 0]
    d = w.sub(ref, fill_value=0).abs().max() if len(ref) else (0 if w.empty else 1)
    if not (set(w.index) == set(ref.index) and d < 1e-6): bad += 1; print("DIFF", t.date(), d, set(w.index) ^ set(ref.index))
print(f"paridad: {len(dates) - bad}/{len(dates)} fechas idénticas (tol 1e-6)")
assert bad == 0

# ---- cliente simulado: reproduce data de un día pasado como si fuera "ahora"
class Fake:
    key = None
    def __init__(s, now): s.now = now; s.today = now.normalize(); s.orders = []
    def exchange_info(s): return {k: {"step": Decimal("0.001"), "min_qty": Decimal("0.001"), "max_qty": Decimal("100000"), "min_notional": Decimal("5")} for k in kl}
    def klines(s, sym, limit=400):
        d = kl[sym]; d = d[d.index <= s.today].tail(limit).rename(columns={"c": "c"}); return d[["c", "qv"]]   # incluye la barra 'en curso' (se descarta)
    def funding(s, sym, start_ms):
        f = pd.read_parquet(f"{rl.__file__.rsplit('/',1)[0]}/../data/fr_{sym}.parquet").rate if os.path.exists(f"{HERE}/../../data/fr_{sym}.parquet") else pd.Series(dtype=float)
        return f[(f.index <= s.now) & (f.index >= pd.to_datetime(start_ms, unit="ms"))]
    def mark_prices(s): return {k: float(v.c.get(s.today - pd.Timedelta(days=1), np.nan)) for k, v in kl.items() if not np.isnan(v.c.get(s.today - pd.Timedelta(days=1), np.nan))}
    def positions(s): return {}
    def dual_side(s): return False
    def equity(s): return 10000.0

now = pd.Timestamp(res.index[-200]) + pd.Timedelta(days=1, seconds=10)
f = Fake(now)
info, close, qvol, fnd, asof = rl.build_inputs(f, now, funding_wait=(1, 0))
w = st.target_weights(close, qvol, fnd, asof)
ref = W.loc[asof]; ref = ref[ref != 0]
assert asof == res.index[-200] and set(w.index) == set(ref.index) and w.sub(ref, fill_value=0).abs().max() < 1e-6
orders, table = rl.plan_orders(w, 10000.0, f.mark_prices(), {}, info, asof)
args = type("A", (), dict(max_weight=0.25, max_turnover=1.0, first_run=True))()
print("pipeline simulado OK:", asof.date(), len(w), "posiciones,", len(orders), "órdenes;", "sanity:", rl.sanity(w, orders, 10000.0, f.mark_prices(), {}, args))
print(table.head(6).round(4).to_string(index=False))
# rebalanceo con posiciones previas (incluye flip y cierre)
pos = {s: float(table.set_index("symbol").target_qty[s]) * -1 for s in list(table.symbol[:3])}   # posiciones opuestas -> flips
o2, _ = rl.plan_orders(w, 10000.0, f.mark_prices(), pos, info, asof)
assert all(o["reduce_only"] for o in o2[:3]), "reduce-only deben ir primero"
print("flips:", [(o["symbol"], o["side"], str(o["qty"]), o["reduce_only"]) for o in o2[:6]])
