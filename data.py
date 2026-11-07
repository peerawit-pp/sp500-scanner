"""
data.py — รายชื่อ S&P 500, market cap และราคา

แหล่งราคา 2 แบบ (คืนค่ารูปแบบเดียวกัน: dict[yahoo_ticker -> DataFrame OHLCV, index เวลา NY])
  - Alpaca (แนะนำ): API ทางการ ฟรี, ข้อมูล SIP (รวมทุกตลาด) ดีเลย์ 15 นาที
        "1h"  : ดึงแท่ง 30Min -> ตัดเหลือ 09:30–16:00 -> รวมเป็น 1H แบบ TradingView (09:30, 10:30, ... 15:30)
                (แท่ง 1Hour ของ Alpaca เริ่มตรงชั่วโมงและรวม pre/post market จึงไม่ใช้)
        "1d"  : แท่ง 1Day (OHLC ไม่รวม extended hours)
        "1wk" : รวมจาก 1Day (สัปดาห์เริ่มวันจันทร์)
        "1mo" : รวมจาก 1Day
  - Yahoo Finance (สำรอง): yfinance, ไม่ต้องสมัคร แต่ไม่ใช่ API ทางการ อาจโดนจำกัด

Market cap ดึงจาก Yahoo เสมอ (Alpaca ไม่มีข้อมูลนี้)
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
CSV_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
NY = "America/New_York"
OHLCV = ["Open", "High", "Low", "Close", "Volume"]

ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
INTRADAY_DAYS = 300      # ประวัติ 1H/4H จาก Alpaca (~410 แท่ง 4H)
DAILY_YEARS = 10         # ประวัติ D/W/M จาก Alpaca


# =====================================================================
# รายชื่อ + market cap
# =====================================================================
def sp500_list() -> pd.DataFrame:
    try:
        df = pd.read_csv(CSV_URL)
    except Exception:
        df = pd.read_csv(HERE / "sp500_fallback.csv")
    df = df.rename(columns={"Security": "Name", "GICS Sector": "Sector"})
    df["Yahoo"] = df["Symbol"].str.replace(".", "-", regex=False)   # BRK.B -> BRK-B
    return df[["Symbol", "Yahoo", "Name", "Sector"]]


def _mcap(t: str) -> float | None:
    import yfinance as yf
    for attempt in range(2):
        try:
            return float(yf.Ticker(t).fast_info["marketCap"])
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return None


def market_caps(tickers: list[str]) -> dict[str, float | None]:
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(zip(tickers, ex.map(_mcap, tickers)))


# =====================================================================
# Yahoo
# =====================================================================
def download(tickers: list[str], interval: str, period: str, chunk: int = 60) -> dict[str, pd.DataFrame]:
    """Yahoo Finance — interval: 1h / 1d / 1wk / 1mo"""
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        for attempt in range(3):
            try:
                raw = yf.download(part, interval=interval, period=period, group_by="ticker",
                                  auto_adjust=False, prepost=False, threads=True, progress=False)
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        else:
            continue
        for t in part:
            try:
                d = raw[t] if len(part) > 1 else raw
                if isinstance(d.columns, pd.MultiIndex):
                    d = d.droplevel(0, axis=1) if t in d.columns.get_level_values(0) else d.droplevel(1, axis=1)
                d = d[OHLCV].dropna()
                if not d.empty:
                    out[t] = d
            except Exception:
                pass
        time.sleep(0.3)
    return out


# =====================================================================
# Alpaca
# =====================================================================
class AlpacaError(Exception):
    pass


class _RateLimiter:
    """จำกัดจำนวนคำขอต่อนาที (แผนฟรี 200/นาที — ใช้ 180 เผื่อไว้)"""

    def __init__(self, per_min: int = 180):
        self.gap = 60.0 / per_min
        self.lock = threading.Lock()
        self.next_t = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next_t)
            self.next_t = t + self.gap
        if t > now:
            time.sleep(t - now)


def _alpaca_fetch_group(syms, timeframe, start, end, limiter, headers, feed):
    """ดึงหลาย symbol ในคำขอเดียว + ไล่ next_page_token จนครบ"""
    sess = requests.Session()           # 1 session ต่อ thread
    acc: dict[str, list] = {}
    token = None
    while True:
        params = {"symbols": ",".join(syms), "timeframe": timeframe, "start": start, "end": end,
                  "limit": 10000, "adjustment": "split", "feed": feed, "sort": "asc"}
        if token:
            params["page_token"] = token
        for attempt in range(6):
            limiter.wait()
            try:
                r = sess.get(ALPACA_BARS_URL, params=params, headers=headers, timeout=30)
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 3 * (attempt + 1))))
                continue
            if r.status_code in (401, 403):
                raise AlpacaError(f"Alpaca ปฏิเสธคำขอ ({r.status_code}): {r.text[:200]}")
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        else:
            raise AlpacaError("Alpaca ไม่ตอบหลังลองซ้ำหลายครั้ง")
        js = r.json()
        for s, bars in (js.get("bars") or {}).items():
            acc.setdefault(s, []).extend(bars)
        token = js.get("next_page_token")
        if not token:
            return acc


def alpaca_bars(symbols: list[str], timeframe: str, start: pd.Timestamp, key: str, secret: str,
                feed: str = "sip", group: int = 40, workers: int = 6) -> dict[str, pd.DataFrame]:
    """คืน dict[alpaca_symbol -> DataFrame] index เป็นเวลา NY"""
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    # แผนฟรี: ข้อมูล SIP ต้องมี end เก่ากว่า 15 นาที
    end = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_s = start.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    limiter = _RateLimiter()
    groups = [symbols[i:i + group] for i in range(0, len(symbols), group)]
    acc: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(lambda g: _alpaca_fetch_group(g, timeframe, start_s, end, limiter, headers, feed),
                           groups):
            for s, bars in part.items():
                acc.setdefault(s, []).extend(bars)
    out = {}
    for s, bars in acc.items():
        if not bars:
            continue
        df = pd.DataFrame(bars)
        df.index = pd.to_datetime(df["t"], utc=True).dt.tz_convert(NY)
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[OHLCV]
        out[s] = df[~df.index.duplicated()].sort_index()
    return out


def session_1h(df30: pd.DataFrame) -> pd.DataFrame:
    """30Min (มี pre/post) -> 1H เฉพาะ 09:30–16:00 แบบ TradingView"""
    idx = df30.index
    mins = idx.hour * 60 + idx.minute
    keep = (mins >= 570) & (mins < 960)
    d = df30[keep]
    if d.empty:
        return d
    m = d.index.hour * 60 + d.index.minute
    start_min = 570 + ((m - 570) // 60) * 60
    key = d.index.normalize() + pd.to_timedelta(start_min, unit="m")
    g = d.groupby(key)
    return pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                         "Close": g["Close"].last(), "Volume": g["Volume"].sum()})


def _agg(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    r = df.resample(rule, label="left", closed="left")
    out = pd.DataFrame({"Open": r["Open"].first(), "High": r["High"].max(), "Low": r["Low"].min(),
                        "Close": r["Close"].last(), "Volume": r["Volume"].sum()})
    return out.dropna(subset=["Close"])


def daily_to_weekly(d: pd.DataFrame) -> pd.DataFrame:
    """สัปดาห์เริ่มวันจันทร์ (label = วันจันทร์) เหมือน TradingView"""
    w = _agg(d, "W-MON")
    return w


def daily_to_monthly(d: pd.DataFrame) -> pd.DataFrame:
    return _agg(d, "MS")


def alpaca_download(tickers: list[str], interval: str, key: str, secret: str) -> dict[str, pd.DataFrame]:
    """interval: 1h / 1d (1wk, 1mo ให้รวมจาก 1d ด้วย daily_to_weekly / daily_to_monthly)"""
    to_alp = {t: t.replace("-", ".") for t in tickers}          # BRK-B -> BRK.B
    back = {v: k for k, v in to_alp.items()}
    now = pd.Timestamp.now(tz=NY)
    if interval == "1h":
        raw = alpaca_bars(list(to_alp.values()), "30Min", now - pd.Timedelta(days=INTRADAY_DAYS), key, secret)
        conv = {s: session_1h(df) for s, df in raw.items()}
    elif interval == "1d":
        raw = alpaca_bars(list(to_alp.values()), "1Day", now - pd.DateOffset(years=DAILY_YEARS), key, secret)
        conv = {}
        for s, df in raw.items():
            df = df.copy()
            df.index = df.index.normalize()                     # วันที่ (เที่ยงคืน NY)
            conv[s] = df
    else:
        raise ValueError(interval)
    return {back.get(s, s): df for s, df in conv.items() if not df.empty}
