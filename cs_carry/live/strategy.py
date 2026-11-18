"""Señal pura del cs-carry (misma lógica que ../backtest.py). Sin I/O: recibe DataFrames diarios y devuelve pesos.

close, qv: DataFrames (index = día UTC, columns = símbolos) con barras DIARIAS COMPLETADAS hasta `asof`.
fund: DataFrame diario de funding. La etiqueta d agrupa pagos en (d 00:00, d+1 00:00] UTC (ver daily_funding),
      por lo que la etiqueta `asof` incluye el settlement de las 00:00 UTC de asof+1.
"""
import pandas as pd

TOP_N, VOL_WIN, FUND_WIN, EWMA_SPAN, FRAC = 40, 180, 7, 20, 1 / 3
MIN_NAMES = 9


def daily_funding(rates: pd.Series) -> pd.Series:
    """rates: index = timestamp UTC naive del pago, values = tasa. Suma por día-etiqueta."""
    return rates.groupby((rates.index - pd.Timedelta(minutes=1)).normalize()).sum()


def universe_rank(qv: pd.DataFrame, asof) -> pd.Series:
    w = qv.loc[:asof].tail(VOL_WIN)
    med = w.median().where(w.count() >= VOL_WIN)          # exige 180 días completos, como el backtest
    return med.rank(ascending=False)


def target_weights(close: pd.DataFrame, qv: pd.DataFrame, fund: pd.DataFrame, asof) -> pd.Series:
    """Pesos objetivo (fracción del equity; long+, short-; gross=1, neto=0). Vacío si no hay suficientes nombres."""
    rank = universe_rank(qv, asof)
    ret = close.loc[:asof].pct_change(fill_method=None)
    vol = ret.ewm(span=EWMA_SPAN, min_periods=EWMA_SPAN).std().iloc[-1]
    f = fund.reindex(index=close.loc[:asof].index, columns=close.columns).tail(FUND_WIN)
    has_fr = f.notna().sum() >= FUND_WIN - 1
    acc = f.fillna(0).sum()
    elig = (rank <= TOP_N) & has_fr & vol.notna()
    n = int(elig.sum())
    if n < MIN_NAMES or len(f) < FUND_WIN:
        return pd.Series(dtype=float)
    k = int(n * FRAC)
    a = acc[elig].sort_values()
    lo, hi = a.index[:k], a.index[-k:]                     # long menor funding acumulado, short mayor
    iv = 1 / vol
    w = pd.Series(0.0, index=close.columns)
    w[lo] = 0.5 * iv[lo] / iv[lo].sum()
    w[hi] = -0.5 * iv[hi] / iv[hi].sum()
    return w[w != 0]
