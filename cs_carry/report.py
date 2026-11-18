import backtest as b, numpy as np, pandas as pd, matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
BLUE, ORANGE, AQUA, INK, MUTE, GRID, SURF = "#2a78d6", "#eb6834", "#1baf7a", "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
plt.rcParams.update({"figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": GRID, "axes.labelcolor": MUTE,
    "xtick.color": MUTE, "ytick.color": MUTE, "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": .8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left"})
kl, qv, rank = b.universe_candidates(); px, ret, fund = b.build(kl, rank)
START = (rank.le(40).sum(axis=1) >= 40).idxmax() + pd.Timedelta(days=30)   # universo completo (40) + warm-up
def go(**kw):
    r, W = b.run(px, ret, fund, rank, **kw); return r.loc[START:], W
res, W = go()
btc = ret["BTCUSDT"].loc[res.index]
eq = lambda r: (1 + r).cumprod()

# ---- métricas
tab = pd.DataFrame({"Neto (5bps)": b.metrics(res.net), "Bruto (0bps)": b.metrics(res.gross), "BTC buy&hold": b.metrics(btc)}).T
extra = pd.Series({"Turnover diario": res.turnover.mean(), "Costo anual (%)": -res.cost.mean() * 365,
    "PnL anual: precio": res.price.mean() * 365, "PnL anual: funding": res.funding.mean() * 365,
    "PnL anual: long leg": res.long_pnl.mean()*365, "PnL anual: short leg": res.short_pnl.mean()*365,
    "Corr con BTC": res.net.corr(btc), "Beta a BTC": res.net.cov(btc) / btc.var()})
pd.set_option("display.width", 200); fmt = tab.copy()
print(START.date(), "->", res.index[-1].date(), len(res), "días"); print(tab.round(3).T.to_string()); print(extra.round(4).to_string())
tab.to_csv("metrics.csv")

# ---- robustez
rob = {}
for name, kw in {"base (5bps)": {}, "0 bps": {"cost": 0}, "10 bps": {"cost": 1e-3}, "20 bps": {"cost": 2e-3}, "top 30": {"top_n": 30}, "top 60": {"top_n": 60}}.items():
    r, _ = go(**kw); rob[name] = b.metrics(r.net)
# señal rezagada 1 día extra (cheque de look-ahead / decaimiento)
sig_fund = fund.copy(); r1, _ = b.run(px, ret, fund.shift(1), rank); r1 = r1.loc[START:]
# ojo: shift de funding también retrasa el cobro; para lag de señal solo, se aproxima rezagando acc -> medimos aparte
rob = pd.DataFrame(rob).T[["CAGR", "Vol", "Sharpe", "MaxDD"]]; print(rob.round(3).to_string()); rob.to_csv("robustness.csv")

# ---- figura
fig = plt.figure(figsize=(13, 13.5), constrained_layout=True); gs = fig.add_gridspec(4, 2)
ax = fig.add_subplot(gs[0:2, :])
ax.plot(eq(res.net), color=BLUE, lw=2, label="Estrategia neta (5 bps)"); ax.plot(eq(res.gross), color=AQUA, lw=1.4, label="Bruta")
ax.plot(eq(btc), color=ORANGE, lw=1.4, label="BTC buy&hold"); ax.set_yscale("log"); ax.set_yticks([.5, 1, 2, 4, 8, 16]); ax.set_yticklabels(["0.5", "1", "2", "4", "8", "16"])
m = tab.loc["Neto (5bps)"]
ax.set_title(f"Equity curve (log), base 1 · CAGR {m.CAGR:.1%} · Vol {m.Vol:.1%} · Sharpe {m.Sharpe:.2f} · MaxDD {m.MaxDD:.1%}")
ax.legend(frameon=False, loc="upper left"); 
a = fig.add_subplot(gs[2, 0]); d = eq(res.net) / eq(res.net).cummax() - 1; a.fill_between(d.index, d, 0, color=BLUE, alpha=.35, lw=0); a.plot(d, color=BLUE, lw=1); a.set_title("Drawdown (neto)"); a.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, 0))
a = fig.add_subplot(gs[2, 1]); sh = res.net.rolling(90).mean() / res.net.rolling(90).std() * np.sqrt(365); a.plot(sh, color=BLUE, lw=1.4); a.axhline(0, color=MUTE, lw=.8); a.set_title("Sharpe rolling 90d (neto)")
a = fig.add_subplot(gs[3, 0]); c = res[["price", "funding", "cost"]].cumsum()
for k, col, lab in [("price", BLUE, "Precio"), ("funding", ORANGE, "Funding"), ("cost", AQUA, "Costos")]: a.plot(c[k], color=col, lw=1.6, label=lab)
a.plot(res.net.cumsum(), color=INK, lw=1.2, ls="--", label="Neto"); a.set_title("PnL acumulado por fuente (suma simple)"); a.legend(frameon=False, ncol=2, loc="upper left")
a = fig.add_subplot(gs[3, 1]); y = res.net.groupby(res.index.year).apply(lambda x: (1 + x).prod() - 1); yb = btc.groupby(btc.index.year).apply(lambda x: (1 + x).prod() - 1)
xs = np.arange(len(y)); a.bar(xs - .2, y.values, .38, color=BLUE, label="Estrategia", ); a.bar(xs + .2, yb.values, .38, color=ORANGE, label="BTC")
for i, v in enumerate(y.values): a.text(i - .2, v + (.02 if v >= 0 else -.06), f"{v:.0%}", ha="center", fontsize=8, color=MUTE)
a.set_xticks(xs); a.set_xticklabels(y.index); a.axhline(0, color=MUTE, lw=.8); a.set_title("Retorno anual (2026 YTD)"); a.legend(frameon=False); a.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, 0))
fig.savefig("cs_carry_backtest.png", dpi=150); res.to_csv("daily_returns.csv")
