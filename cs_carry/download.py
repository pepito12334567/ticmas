"""Descarga klines 1d y funding de Binance USDT-M desde data.binance.vision."""
import re, io, zipfile, sys, os, requests, pandas as pd
from concurrent.futures import ThreadPoolExecutor
BASE = "https://data.binance.vision"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
OUT = os.path.join(os.path.dirname(__file__), "data")
S = requests.Session()
import time
def get(url, **kw):
    for i in range(6):
        try:
            return S.get(url, timeout=60, **kw)
        except requests.exceptions.RequestException:
            time.sleep(1.5 ** i + 0.5)
    raise RuntimeError("fail " + url)

def list_keys(prefix, delim="/"):
    keys, marker = [], ""
    while True:
        r = get(S3, params={"delimiter": delim, "prefix": prefix, "marker": marker}).text
        keys += re.findall(r"<Prefix>([^<]+)</Prefix>" if delim else r"<Key>([^<]+)</Key>", r)
        keys = [k for k in keys if k != prefix]
        if "<IsTruncated>true" not in r: break
        nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", r)
        marker = nm.group(1) if nm else keys[-1]
    return keys

def symbols():
    ks = list_keys("data/futures/um/monthly/klines/")
    s = sorted({k.split("/")[-2] for k in ks})
    return [x for x in s if x.endswith("USDT")]

def fetch_zip(url):
    r = get(url)
    if r.status_code != 200: return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return pd.read_csv(z.open(z.namelist()[0]), header=None if "klines" in url else 0)

def files_for(kind, sym, interval=None):
    sub = f"{kind}/{sym}/" + (f"{interval}/" if interval else "")
    m = list_keys(f"data/futures/um/monthly/{sub}", delim="")
    d = list_keys(f"data/futures/um/daily/{sub}", delim="")
    months = {re.search(r"(\d{4}-\d{2})\.zip", k).group(1) for k in m if k.endswith(".zip")}
    d = [k for k in d if k.endswith(".zip") and re.search(r"(\d{4}-\d{2})-\d{2}", k).group(1) not in months]
    return [k for k in m if k.endswith(".zip")] + d

def klines(sym):
    p = f"{OUT}/kl_{sym}.parquet"
    if os.path.exists(p): return
    dfs = [fetch_zip(f"{BASE}/{k}") for k in files_for("klines", sym, "1d")]
    dfs = [d for d in dfs if d is not None]
    if not dfs: return
    df = pd.concat(dfs)
    df = df[pd.to_numeric(df[0], errors="coerce").notna()].astype(float)
    df = df.iloc[:, :8]; df.columns = ["t","o","h","l","c","v","ct","qv"]
    df["date"] = pd.to_datetime(df.t, unit="ms").dt.normalize()
    df.drop_duplicates("date").set_index("date")[["o","h","l","c","v","qv"]].to_parquet(p)

def funding(sym):
    p = f"{OUT}/fr_{sym}.parquet"
    if os.path.exists(p): return
    dfs = [fetch_zip(f"{BASE}/{k}") for k in files_for("fundingRate", sym)]
    dfs = [d for d in dfs if d is not None]
    if not dfs: return
    df = pd.concat(dfs); df.columns = ["t","interval","rate"][:df.shape[1]]
    df["t"] = pd.to_datetime(df.t.astype("int64"), unit="ms")
    df.drop_duplicates("t").set_index("t")[["rate"]].to_parquet(p)

if __name__ == "__main__":
    what = sys.argv[1]
    syms = symbols() if what == "klines" else sys.argv[2:]
    fn = klines if what == "klines" else funding
    print(len(syms), "symbols", flush=True)
    def safe(x):
        try: fn(x)
        except Exception as e: print("ERR", x, e, flush=True)
    with ThreadPoolExecutor(12) as ex: list(ex.map(safe, syms))
    print("done")
