"""Cross-sectional carry en perps de Binance USDT-M.
Timing: señal con info hasta el cierre del día d-1 (00:00 UTC), posición se mantiene durante el día d.
"""
import glob, os, sys, numpy as np, pandas as pd
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TOP_N, VOL_WIN, FUND_WIN, EWMA_SPAN, COST = 40, 180, 7, 20, 5e-4

def load(prefix, col):
    out = {}
    for p in glob.glob(f"{D}/{prefix}_*.parquet"):
        out[os.path.basename(p)[len(prefix)+1:-8]] = pd.read_parquet(p)
    return out

def universe_candidates():
    kl = load("kl", None)
    qv = pd.DataFrame({s: d.qv for s, d in kl.items()}).sort_index()
    med = qv.rolling(VOL_WIN, min_periods=VOL_WIN).median()          # info hasta d (cierre)
    rank = med.rank(axis=1, ascending=False)
    return kl, qv, rank

def build(kl, rank):
    px = pd.DataFrame({s: d.c for s, d in kl.items()}).sort_index()
    ret = px.pct_change(fill_method=None)
    fr = load("fr", None)
    # pago con timestamp en (d 00:00, d+1 00:00] -> etiqueta d (restamos 1 min para el pago de las 00:00:00.003)
    fund = pd.DataFrame({s: d.rate.groupby((d.index - pd.Timedelta(minutes=1)).normalize()).sum() for s, d in fr.items()})
    fund = fund.reindex(index=px.index, columns=px.columns)
    return px, ret, fund

def run(px, ret, fund, rank, top_n=TOP_N, cost=COST, frac=1/3):
    idx = px.index
    in_univ = (rank <= top_n)
    # solo símbolos con funding disponible
    has_fr = fund.notna().rolling(FUND_WIN).sum() >= FUND_WIN - 1
    acc = fund.fillna(0).rolling(FUND_WIN).sum()                      # funding acumulado 7d (hasta cierre d)
    vol = ret.ewm(span=EWMA_SPAN, min_periods=EWMA_SPAN).std()
    elig = in_univ & has_fr & vol.notna() & acc.notna()
    W = pd.DataFrame(0.0, index=idx, columns=px.columns)              # pesos fijados al cierre d
    for t in idx:
        e = elig.loc[t]; n = int(e.sum())
        if n < 9: continue
        k = int(n * frac)
        a = acc.loc[t][e].sort_values()
        lo, hi = a.index[:k], a.index[-k:]
        iv = 1 / vol.loc[t]
        W.loc[t, lo] = 0.5 * iv[lo] / iv[lo].sum()                    # long menor funding
        W.loc[t, hi] = -0.5 * iv[hi] / iv[hi].sum()                   # short mayor funding
    Wp = W.shift(1).fillna(0)                                         # pesos mantenidos durante d
    r0 = ret.fillna(0); f0 = fund.fillna(0)
    px_pnl = (Wp * r0).sum(axis=1)
    fd_pnl = -(Wp * f0).sum(axis=1)                                   # long paga funding positivo
    # turnover contra pesos derivados
    pre = Wp * (1 + r0); pre = pre.div(1 + px_pnl, axis=0)
    turn = (W - pre).abs().sum(axis=1)                               # trade al cierre d
    tc_applied = turn * cost                                          # costo del rebalanceo al cierre d, cargado en d
    gross = px_pnl + fd_pnl
    net = gross - tc_applied
    first = (Wp.abs().sum(axis=1) > 0).idxmax()
    res = pd.DataFrame({"gross": gross, "net": net, "price": px_pnl, "funding": fd_pnl, "cost": -tc_applied,
                        "turnover": turn, "n_univ": in_univ.sum(axis=1),
                        "n_long": (Wp > 0).sum(axis=1), "long_pnl": (Wp.clip(lower=0) * r0).sum(axis=1) - (Wp.clip(lower=0) * f0).sum(axis=1),
                        "short_pnl": (Wp.clip(upper=0) * r0).sum(axis=1) - (Wp.clip(upper=0) * f0).sum(axis=1)}).loc[first:]
    return res, W

def metrics(r, name=""):
    r = r.dropna(); eq = (1 + r).cumprod(); yrs = len(r) / 365
    ann = eq.iloc[-1] ** (1 / yrs) - 1; vol = r.std() * np.sqrt(365)
    dd = eq / eq.cummax() - 1; dn = r[r < 0].std() * np.sqrt(365)
    return {"CAGR": ann, "Vol": vol, "Sharpe": r.mean() * 365 / vol, "Sortino": r.mean() * 365 / dn,
            "MaxDD": dd.min(), "Calmar": ann / -dd.min(), "Skew": r.skew(), "Kurt": r.kurt(),
            "Hit%": (r > 0).mean(), "Worst day": r.min(), "Best day": r.max(), "TotalRet": eq.iloc[-1] - 1}
