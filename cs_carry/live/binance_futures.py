"""Cliente mínimo REST de Binance USDT-M Futures (requests + HMAC). Endpoints públicos sin key; órdenes/cuenta firmados."""
import hashlib, hmac, os, time
from decimal import Decimal
import pandas as pd, requests

MAIN, TEST = "https://fapi.binance.com", "https://testnet.binancefuture.com"


class BinanceError(RuntimeError):
    pass


class BinanceFutures:
    def __init__(self, key=None, secret=None, testnet=False):
        self.key = key or os.environ.get("BINANCE_API_KEY")
        self.secret = secret or os.environ.get("BINANCE_API_SECRET")
        self.base = TEST if testnet else MAIN
        self.s = requests.Session()
        if self.key: self.s.headers["X-MBX-APIKEY"] = self.key
        self.offset = 0

    # ---------- transporte ----------
    def _req(self, method, path, params=None, signed=False, retries=4):
        params = dict(params or {})
        for i in range(retries):
            p = dict(params)
            if signed:
                if not self.secret: raise BinanceError("faltan BINANCE_API_KEY/BINANCE_API_SECRET")
                p["timestamp"] = int(time.time() * 1000) + self.offset; p["recvWindow"] = 10000
                q = "&".join(f"{k}={v}" for k, v in p.items())
                p["signature"] = hmac.new(self.secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            try:
                r = self.s.request(method, self.base + path, params=p, timeout=20)
            except requests.RequestException:
                time.sleep(1 + i); continue
            if int(r.headers.get("X-MBX-USED-WEIGHT-1M", 0)) > 2000: time.sleep(10)
            if r.status_code in (418, 429):
                time.sleep(int(r.headers.get("Retry-After", 5)) + 1); continue
            if r.status_code >= 500: time.sleep(1 + i); continue
            if r.status_code != 200:
                j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                if j.get("code") == -1021: self.sync_time(); continue
                raise BinanceError(f"{method} {path} -> {r.status_code} {r.text[:300]}")
            return r.json()
        raise BinanceError(f"{method} {path}: reintentos agotados")

    def sync_time(self):
        self.offset = self._req("GET", "/fapi/v1/time")["serverTime"] - int(time.time() * 1000)

    # ---------- mercado ----------
    def exchange_info(self):
        """symbol -> filtros, solo perps USDT en TRADING."""
        out = {}
        for s in self._req("GET", "/fapi/v1/exchangeInfo")["symbols"]:
            if s["contractType"] != "PERPETUAL" or s["quoteAsset"] != "USDT" or s["status"] != "TRADING": continue
            f = {x["filterType"]: x for x in s["filters"]}
            out[s["symbol"]] = {"step": Decimal(f["MARKET_LOT_SIZE"]["stepSize"]), "min_qty": Decimal(f["MARKET_LOT_SIZE"]["minQty"]),
                                "max_qty": Decimal(f["MARKET_LOT_SIZE"]["maxQty"]),
                                "min_notional": Decimal(f.get("MIN_NOTIONAL", {}).get("notional", "5"))}
        return out

    def klines(self, symbol, limit=400):
        """Barras diarias (incluye la en curso). index = open time UTC naive; columnas c, qv."""
        k = self._req("GET", "/fapi/v1/klines", {"symbol": symbol, "interval": "1d", "limit": limit})
        df = pd.DataFrame(k).iloc[:, [0, 4, 7]].astype(float); df.columns = ["t", "c", "qv"]
        df.index = pd.to_datetime(df.t, unit="ms"); return df[["c", "qv"]]

    def funding(self, symbol, start_ms):
        r = self._req("GET", "/fapi/v1/fundingRate", {"symbol": symbol, "startTime": int(start_ms), "limit": 1000})
        s = pd.Series({pd.to_datetime(x["fundingTime"], unit="ms"): float(x["fundingRate"]) for x in r}, dtype=float)
        return s.sort_index()

    def mark_prices(self):
        return {x["symbol"]: float(x["markPrice"]) for x in self._req("GET", "/fapi/v1/premiumIndex")}

    # ---------- cuenta ----------
    def _signed_get(self, paths, params=None):
        for p in paths:                                    # v3 primero, v2 como fallback
            try: return self._req("GET", p, params, signed=True)
            except BinanceError as e:
                last = e
        raise last

    def equity(self):
        a = self._signed_get(["/fapi/v3/account", "/fapi/v2/account"])
        return float(a["totalMarginBalance"])             # wallet + PnL no realizado (USDT)

    def positions(self):
        r = self._signed_get(["/fapi/v3/positionRisk", "/fapi/v2/positionRisk"])
        return {x["symbol"]: float(x["positionAmt"]) for x in r if float(x["positionAmt"]) != 0}

    def dual_side(self):
        return self._req("GET", "/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"]

    def set_leverage(self, symbol, lev):
        return self._req("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": lev}, signed=True)

    def market_order(self, symbol, side, qty, reduce_only, client_id):
        p = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": format(qty, "f"), "newClientOrderId": client_id,
             "newOrderRespType": "RESULT"}
        if reduce_only: p["reduceOnly"] = "true"
        return self._req("POST", "/fapi/v1/order", p, signed=True)
