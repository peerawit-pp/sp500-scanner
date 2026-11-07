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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
CSV_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
NY = "America/New_York"
OHLCV = ["Open", "High", "Low", "Close", "Volume"]

ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
INTRADAY_DAYS = 250      # ประวัติ 1H/4H จาก Alpaca (~340 แท่ง 4H)
DAILY_YEARS = 6          # ประวัติ D/W/M จาก Alpaca (W ~310 แท่ง)


# =====================================================================
# รายชื่อหุ้นตามดัชนี + market cap + sector
# =====================================================================
INDEXES = ["S&P 500", "NASDAQ 100", "Dow Jones 30", "NASDAQ Composite"]
LISTING_URL = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/{ex}/{ex}_full_tickers.json"
_NOT_COMMON = (r"\b(?:Warrants?|Units?|Rights?|Preferred|Notes due|Debentures?|Fund|ETF|"
               r"Depositary Shares, each representing a)\b")
_NAME_TAIL = (r"\s+(?:Class [A-Z]\s+)?(?:Common Stock|Ordinary Shares?|Common Shares|"
              r"American Depositary Shares?|ADS|Depositary Shares?)\b.*$")


def sp500_list() -> pd.DataFrame:
    try:
        df = pd.read_csv(CSV_URL)
    except Exception:
        df = pd.read_csv(HERE / "sp500_fallback.csv")
    df = df.rename(columns={"Security": "Name", "GICS Sector": "Sector"})
    df["Yahoo"] = df["Symbol"].str.replace(".", "-", regex=False)   # BRK.B -> BRK-B
    return df[["Symbol", "Yahoo", "Name", "Sector"]]


def us_listings() -> pd.DataFrame:
    """หุ้นที่จดทะเบียนใน NASDAQ / NYSE / AMEX พร้อม market cap และ sector (ข้อมูลจาก Nasdaq, อัปเดตรายวัน)"""
    try:
        recs = []
        for ex in ("nasdaq", "nyse", "amex"):
            r = requests.get(LISTING_URL.format(ex=ex), timeout=30)
            r.raise_for_status()
            recs += [{"Symbol": x["symbol"], "Name": x["name"], "Sector": x["sector"],
                      "MarketCap": x["marketCap"], "Exchange": ex.upper()} for x in r.json()]
        df = pd.DataFrame(recs)
        if len(df) < 3000:
            raise ValueError("listing too small")
    except Exception:
        df = pd.read_csv(HERE / "us_listings_fallback.csv")
    df["Symbol"] = df["Symbol"].astype(str).str.strip().str.replace("/", ".", regex=False)   # BRK/B -> BRK.B
    df["MarketCapB"] = pd.to_numeric(df["MarketCap"], errors="coerce") / 1e9
    df.loc[df["MarketCapB"] <= 0, "MarketCapB"] = float("nan")
    df["Name"] = df["Name"].astype(str)
    df["Sector"] = df["Sector"].fillna("").replace("", "Other")
    return df.drop_duplicates("Symbol")[["Symbol", "Name", "Sector", "MarketCapB", "Exchange"]]


def universe(selected: list[str]) -> pd.DataFrame:
    """รวมรายชื่อหุ้นจากดัชนีที่เลือก -> Symbol, Yahoo, Name, Sector, MarketCapB, Index, Major"""
    lst = us_listings().set_index("Symbol")
    members: dict[str, list[str]] = {}
    names, sectors = {}, {}
    if "S&P 500" in selected:
        sp = sp500_list()
        for _, r in sp.iterrows():
            members.setdefault(r["Symbol"], []).append("S&P 500")
            names[r["Symbol"]], sectors[r["Symbol"]] = r["Name"], r["Sector"]
    idx = pd.read_csv(HERE / "indices.csv")
    for ix in ("NASDAQ 100", "Dow Jones 30"):
        if ix in selected:
            for s in idx.loc[idx["Index"] == ix, "Symbol"]:
                members.setdefault(s, []).append(ix)
    if "NASDAQ Composite" in selected:
        nas = lst[(lst["Exchange"] == "NASDAQ")]
        ok = ~nas.index.str.contains(r"[\^]", regex=True) & ~nas["Name"].str.contains(_NOT_COMMON, case=False, regex=True)
        for s in nas.index[ok]:
            members.setdefault(s, []).append("NASDAQ Composite")
    rows = []
    for s, ixs in members.items():
        info = lst.loc[s] if s in lst.index else None
        nm = names.get(s) or (None if info is None else
                              pd.Series([info["Name"]]).str.replace(_NAME_TAIL, "", regex=True).iloc[0])
        rows.append({"Symbol": s, "Yahoo": s.replace(".", "-"), "Name": nm or s,
                     "Sector": sectors.get(s) or (None if info is None else info["Sector"]) or "Other",
                     "MarketCapB": None if info is None else info["MarketCapB"],
                     "Index": ", ".join(ixs),
                     "Major": any(i != "NASDAQ Composite" for i in ixs)})
    out = pd.DataFrame(rows, columns=["Symbol", "Yahoo", "Name", "Sector", "MarketCapB", "Index", "Major"])
    out["MarketCapB"] = pd.to_numeric(out["MarketCapB"], errors="coerce")
    return out.sort_values("Symbol").reset_index(drop=True)


def _mcap(t: str) -> float | None:
    import yfinance as yf
    try:
        return float(yf.Ticker(t).fast_info["marketCap"])
    except Exception:
        return None


def market_caps(tickers: list[str]) -> dict[str, float | None]:
    """(สำรอง) market cap จาก Yahoo ทีละตัว"""
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
    """จำกัดจำนวนคำขอต่อนาที (แผนฟรี 200/นาที — ใช้ 190 เผื่อไว้) + หยุดรอทั้งหมดเมื่อ Alpaca บอกว่าโควตาใกล้หมด"""

    def __init__(self, per_min: int = 190):
        self.gap = 60.0 / per_min
        self.lock = threading.Lock()
        self.next_t = 0.0
        self.pause_until = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next_t, self.pause_until)
            self.next_t = t + self.gap
        if t > now:
            time.sleep(t - now)

    def pause(self, seconds: float):
        with self.lock:
            self.pause_until = max(self.pause_until, time.monotonic() + seconds)


def new_stats() -> dict:
    return {"req": 0, "r429": 0, "timeouts": 0, "errors": 0, "sec": 0.0, "wait429": 0.0}


def _reset_wait(r) -> float | None:
    """วินาทีจนโควตารีเซ็ต จาก header X-RateLimit-Reset (epoch)"""
    try:
        return max(0.0, float(r.headers.get("X-RateLimit-Reset")) - time.time()) + 0.5
    except (TypeError, ValueError):
        return None


def _page_to_df(bars: list, session_only: bool) -> pd.DataFrame:
    """แปลงแท่งจาก 1 หน้า -> DataFrame เล็ก (ตัด pre/post ทิ้งทันทีเพื่อประหยัด RAM)"""
    df = pd.DataFrame.from_records(bars, columns=["t", "o", "h", "l", "c", "v"])
    idx = pd.to_datetime(df["t"], utc=True).dt.tz_convert(NY)
    df = df.drop(columns="t").set_axis(pd.DatetimeIndex(idx), axis=0)
    df.columns = OHLCV
    if session_only:
        mins = df.index.hour * 60 + df.index.minute
        df = df[(mins >= 570) & (mins < 960)]
    return df


def _alpaca_fetch_group(syms, timeframe, start, end, limiter, headers, feed, session_only, finish, stats):
    """ดึงหลาย symbol ในคำขอเดียว + ไล่ next_page_token จนครบ แล้วย่อข้อมูลทันที"""
    sess = requests.Session()           # 1 session ต่อ thread
    acc: dict[str, list] = {}
    token = None
    while True:
        params = {"symbols": ",".join(syms), "timeframe": timeframe, "start": start, "end": end,
                  "limit": 10000, "adjustment": "split", "feed": feed, "sort": "asc"}
        if token:
            params["page_token"] = token
        fails, last_err = 0, ""
        while True:
            limiter.wait()
            t0 = time.time()
            try:
                r = sess.get(ALPACA_BARS_URL, params=params, headers=headers, timeout=(10, 60))
            except requests.Timeout:
                stats["timeouts"] += 1
                fails, last_err = fails + 1, "หมดเวลารอ (timeout)"
            except requests.RequestException as e:
                stats["errors"] += 1
                fails, last_err = fails + 1, f"เชื่อมต่อไม่ได้ ({type(e).__name__})"
            else:
                stats["req"] += 1
                stats["sec"] += time.time() - t0
                rem = r.headers.get("X-RateLimit-Remaining")
                if rem is not None and str(rem).isdigit() and int(rem) <= 3:
                    limiter.pause(_reset_wait(r) or 5)          # โควตาใกล้หมด -> พักทุก thread
                if r.status_code == 429:                       # โดนจำกัด: รอจนโควตารีเซ็ต (ไม่นับเป็นความล้มเหลว)
                    w = _reset_wait(r) or 15.0
                    stats["r429"] += 1
                    stats["wait429"] += w
                    if stats["wait429"] > 900:
                        raise AlpacaError("Alpaca จำกัดจำนวนคำขอ (429) ต่อเนื่องนานเกินไป — อาจมีการสแกนอื่นใช้ "
                                          "API Key เดียวกันอยู่ (เช่น GitHub Actions) รอให้เสร็จก่อนแล้วลองใหม่")
                    limiter.pause(w)
                    continue
                if r.status_code in (401, 403):
                    raise AlpacaError(f"Alpaca ปฏิเสธคำขอ ({r.status_code}): {r.text[:200]}")
                if r.status_code < 500:
                    r.raise_for_status()
                    break
                stats["errors"] += 1
                fails, last_err = fails + 1, f"เซิร์ฟเวอร์ Alpaca ขัดข้อง (HTTP {r.status_code})"
            if fails >= 6:
                raise AlpacaError(f"Alpaca ไม่ตอบหลังลองซ้ำ 6 ครั้ง — สาเหตุล่าสุด: {last_err}")
            time.sleep(min(30, 2 ** fails))
        js = r.json()
        for s, bars in (js.get("bars") or {}).items():
            if bars:
                part = _page_to_df(bars, session_only)
                if not part.empty:
                    acc.setdefault(s, []).append(part)
        token = js.get("next_page_token")
        if not token:
            break
    out = {}
    for s, parts in acc.items():
        df = pd.concat(parts)
        df = df[~df.index.duplicated()].sort_index()
        df = finish(df) if finish else df
        if not df.empty:
            out[s] = df
    return out


def alpaca_bars(symbols: list[str], timeframe: str, start: pd.Timestamp, key: str, secret: str,
                feed: str = "sip", group: int = 20, workers: int = 4,
                session_only: bool = False, finish=None, progress=None) -> dict[str, pd.DataFrame]:
    """คืน dict[alpaca_symbol -> DataFrame] index เป็นเวลา NY
    progress(done, total, stats=...) ถูกเรียกจาก thread หลักทุก ~1 วินาที"""
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    # แผนฟรี: ข้อมูล SIP ต้องมี end เก่ากว่า 15 นาที
    end = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_s = start.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    limiter, stats = _RateLimiter(), new_stats()
    groups = [symbols[i:i + group] for i in range(0, len(symbols), group)]
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        pending = {ex.submit(_alpaca_fetch_group, g, timeframe, start_s, end, limiter, headers, feed,
                             session_only, finish, stats) for g in groups}
        done_n = 0
        while pending:
            finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for f in finished:
                try:
                    out.update(f.result())
                except Exception:
                    for p_ in pending:
                        p_.cancel()
                    raise
                done_n += 1
            if progress:
                progress(done_n, len(groups), stats=stats)
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


def _alpaca_fetch(tickers: list[str], interval: str, key: str, secret: str, start: pd.Timestamp,
                  progress=None, group: int = 20) -> dict[str, pd.DataFrame]:
    to_alp = {t: t.replace("-", ".") for t in tickers}          # BRK-B -> BRK.B
    back = {v: k for k, v in to_alp.items()}
    if interval == "1h":
        conv = alpaca_bars(list(to_alp.values()), "30Min", start, key, secret, group=group,
                           session_only=True, finish=session_1h, progress=progress)
    elif interval == "1d":
        def _norm(df):
            df = df.copy()
            df.index = df.index.normalize()                     # วันที่ (เที่ยงคืน NY)
            return df
        conv = alpaca_bars(list(to_alp.values()), "1Day", start, key, secret, group=group,
                           finish=_norm, progress=progress)
    else:
        raise ValueError(interval)
    return {back.get(s, s): df for s, df in conv.items() if not df.empty}


def _window_start(interval: str) -> pd.Timestamp:
    now = pd.Timestamp.now(tz=NY)
    return now - pd.Timedelta(days=INTRADAY_DAYS) if interval == "1h" else now - pd.DateOffset(years=DAILY_YEARS)


def alpaca_download(tickers: list[str], interval: str, key: str, secret: str,
                    progress=None) -> dict[str, pd.DataFrame]:
    """interval: 1h / 1d (1wk, 1mo ให้รวมจาก 1d ด้วย daily_to_weekly / daily_to_monthly)"""
    return _alpaca_fetch(tickers, interval, key, secret, _window_start(interval), progress)


def alpaca_update(tickers: list[str], interval: str, key: str, secret: str,
                  cached: dict[str, pd.DataFrame] | None, progress=None) -> dict[str, pd.DataFrame]:
    """
    อัปเดตแบบเพิ่มเฉพาะส่วนใหม่ (incremental):
      - หุ้นที่มีข้อมูลเดิม -> ดึงตั้งแต่ต้นวันของแท่งล่าสุด (1h) / ย้อน 7 วัน (1d) แล้วต่อท้ายข้อมูลเดิม
      - หุ้นใหม่หรือข้อมูลเก่าเกินไป -> ดึงเต็มช่วง
    """
    cached = cached or {}
    lasts = {t: cached[t].index[-1] for t in tickers if t in cached and not cached[t].empty}
    if not lasts:
        return alpaca_download(tickers, interval, key, secret, progress)
    med = pd.Series(list(lasts.values())).sort_values().iloc[len(lasts) // 2]
    start = med.normalize() if interval == "1h" else med.normalize() - pd.Timedelta(days=7)
    inc = [t for t, lt in lasts.items() if lt >= start]
    full = [t for t in tickers if t not in inc]
    out: dict[str, pd.DataFrame] = {}
    if inc:
        new = _alpaca_fetch(inc, interval, key, secret, start, progress, group=100)
        w0 = _window_start(interval)
        for t in inc:
            old = cached[t]
            if t in new:
                df = pd.concat([old[old.index < start], new[t]])
                df = df[~df.index.duplicated(keep="last")].sort_index()
            else:
                df = old
            out[t] = df[df.index >= w0]
    if full:
        out.update(alpaca_download(full, interval, key, secret, progress))
    return out
