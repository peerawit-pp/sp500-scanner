"""
logic.py — แปลงจาก Pine Script "Reversal Candle [Stoch Oversold]" บรรทัดต่อบรรทัด

  f_lookbackLow()  -> _lookback_low()
  f_htfTrigger() / f_ltfSwLow()  (2 ฟังก์ชันนี้ใน Pine เหมือนกันทุกบรรทัด) -> hl_engine()
  Alert "Higher Low ✅ + Price > X ✅":
      _hl  = not na(sw) and not na(prevSw) and sw > prevSw
      _px  = not na(xLive) and close > xLive
      alert = _hl and _px and (na(alertedX) or xLive != alertedX)

กติกา na ของ Pine ถูกเลียนแบบด้วย NaN:
  - การเปรียบเทียบกับ na = false  (NaN ใน Python ก็ให้ False เหมือนกัน)
  - math.max(x, na) = na          -> ใช้ _pmax()
  - ค่า [i] ที่ย้อนเกินแท่งแรก = na

EMA89 MTF + Fibo Support อยู่ใน fibo.py (แปลงจาก Pine ver7)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

NA = float("nan")


@dataclass
class Params:
    # ค่า default ตรงกับ input ของ Pine
    stoch_k: int = 9              # iK
    stoch_smooth: int = 3         # iSm
    os_level: float = 20.0        # iOS
    ob_level: float = 80.0        # iOB
    max_scan_bars: int = 80       # iReset
    trail_bars: int = 5           # iTrail
    sw_lookback: int = 3          # iSwLookback
    close_on_timeout: bool = True  # iCloseOnTimeout
    # ตัวกรองของ scanner
    max_bars_since_signal: int = 5
    ema_len: int = 89


# ---------- helpers ----------
def _isna(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _pmax(a, b):
    """math.max ของ Pine: ถ้ามี na ให้ผลเป็น na"""
    return NA if _isna(a) or _isna(b) else max(a, b)


def stoch_k(df: pd.DataFrame, k: int, smooth: int) -> pd.Series:
    """ta.sma(ta.stoch(close, high, low, k), smooth)"""
    ll = df["Low"].rolling(k).min()
    hh = df["High"].rolling(k).max()
    raw = 100 * (df["Close"] - ll) / (hh - ll).replace(0, np.nan)
    return raw.rolling(smooth).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """ta.ema — seed ด้วย SMA n แท่งแรก (ข้อมูลยาว >10×n ค่าวิธี seed แทบไม่มีผล)"""
    v = s.to_numpy(dtype=float)
    res = np.full(len(v), np.nan)
    if len(v) >= n:
        a = 2 / (n + 1)
        res[n - 1] = np.nanmean(v[:n])
        for i in range(n, len(v)):
            res[i] = a * v[i] + (1 - a) * res[i - 1]
    return pd.Series(res, index=s.index)


def resample_4h(h1: pd.DataFrame) -> pd.DataFrame:
    """รวม 1H (regular session) เป็น 4H แบบ TradingView: 09:30–13:30, 13:30–16:00 เวลา NY"""
    if h1.empty:
        return h1
    idx = h1.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    idx = idx.tz_convert("America/New_York")
    df = h1.copy()
    df.index = idx
    mins = idx.hour * 60 + idx.minute
    half = np.where(mins < 13 * 60 + 30, "09:30", "13:30")
    key = pd.to_datetime(idx.strftime("%Y-%m-%d") + " " + half).tz_localize("America/New_York")
    g = df.groupby(key)
    out = pd.DataFrame({
        "Open": g["Open"].first(), "High": g["High"].max(),
        "Low": g["Low"].min(), "Close": g["Close"].last(), "Volume": g["Volume"].sum(),
    })
    return out.dropna()


# ---------- f_lookbackLow ----------
def _lookback_low(o, h, l, c, i, lookback):
    def at(arr, j):
        return arr[j] if 0 <= j < len(arr) else NA

    t_low, t_low_bar = l[i], i
    t_low_body = _pmax(o[i], c[i])
    x_body = _pmax(at(o, i - 1), at(c, i - 1))
    x_bar = i - 1
    js = range(1, lookback + 1) if lookback >= 1 else (1, 0)     # Pine: for i = 1 to 0 วนถอยหลัง (1, 0)
    for j in js:
        lj = at(l, i - j)
        if lj < t_low:
            t_low, t_low_bar = lj, i - j
            t_low_body = _pmax(at(o, i - j), at(c, i - j))
            x_body = _pmax(at(o, i - j - 1), at(c, i - j - 1))
            x_bar = i - j - 1
    return t_low, t_low_bar, t_low_body, x_body, x_bar


# ---------- f_htfTrigger / f_ltfSwLow + alert ----------
def hl_engine(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """คืน DataFrame รายแท่ง: k, isOS, sw, prev_sw, x_live, sw_bar, hl, px, alert"""
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    n = len(df)
    k = stoch_k(df, p.stoch_k, p.stoch_smooth).to_numpy(float)
    is_os = k < p.os_level            # NaN -> False เหมือน Pine

    # var ทั้งหมดของ f_htfTrigger
    scan = trailing = wait_rev = cycle_active = False
    t_low = t_low_body = x_body = trigger = NA
    t_low_bar = x_bar = scan_start = exit_bar = None
    confirmed_low2 = last_confirmed_trig = NA
    alerted_x = NA

    out_sw = np.full(n, np.nan); out_prev = np.full(n, np.nan)
    out_x = np.full(n, np.nan); out_swbar = np.full(n, -1)
    out_alert = np.zeros(n, bool)

    for i in range(n):
        enter_os = is_os[i] and not (is_os[i - 1] if i > 0 else False)
        exit_os = (not is_os[i]) and (is_os[i - 1] if i > 0 else False)
        x_body_now = _pmax(o[i - 1], c[i - 1]) if i > 0 else NA

        # ---- if _enterOS
        if enter_os:
            scan, trailing, wait_rev, exit_bar = True, False, False, None
            lb_low, lb_bar, lb_body, lb_xbody, lb_xbar = _lookback_low(o, h, l, c, i, p.sw_lookback)
            if not cycle_active:
                cycle_active = True
                confirmed_low2 = t_low
                trigger = NA
                scan_start = i
                t_low, t_low_bar, t_low_body, x_body, x_bar = lb_low, lb_bar, lb_body, lb_xbody, lb_xbar
            else:
                trigger = NA
                scan_start = i
                if lb_low < t_low:
                    t_low, t_low_bar, t_low_body, x_body, x_bar = lb_low, lb_bar, lb_body, lb_xbody, lb_xbar

        # ---- if _scan and not _trailing and not _waitRev
        if scan and not trailing and not wait_rev:
            if i - scan_start > p.max_scan_bars:
                scan = False
                if p.close_on_timeout:
                    cycle_active = False
            elif is_os[i] and l[i] < t_low:
                t_low, t_low_bar, t_low_body = l[i], i, _pmax(o[i], c[i])
                x_body, x_bar = x_body_now, i - 1
            elif exit_os:
                if l[i] < t_low:
                    t_low, t_low_bar, t_low_body = l[i], i, _pmax(o[i], c[i])
                    x_body, x_bar = x_body_now, i - 1
                scan, trailing, exit_bar = False, True, i

        # ---- if _trailing and not _waitRev
        if trailing and not wait_rev:
            hit_ob = k[i] > p.ob_level
            if not hit_ob and l[i] < t_low:
                t_low, t_low_bar, t_low_body = l[i], i, _pmax(o[i], c[i])
                x_body, x_bar = x_body_now, i - 1
            time_up = exit_bar is not None and i - exit_bar >= p.trail_bars
            if hit_ob or time_up:
                trailing = False
                use_x = t_low_body if x_body < t_low_body else x_body
                cand = t_low if use_x < t_low else use_x
                if c[i] > cand:
                    wait_rev = False          # ราคาข้าม X ไปแล้วตอนยืนยัน -> ทิ้งเงียบๆ
                else:
                    wait_rev = True
                    trigger = cand
                    last_confirmed_trig = cand

        # ---- if _waitRev and close > _trigger
        if wait_rev and not _isna(trigger) and c[i] > trigger:
            wait_rev, trigger = False, NA

        # ---- if _cycleActive and _k > _iob
        if cycle_active and k[i] > p.ob_level:
            cycle_active = False

        # ---- outputs
        eff_x = t_low_body if x_body < t_low_body else x_body
        if wait_rev and not _isna(trigger):
            x_live = trigger
        elif (scan or trailing) and not _isna(x_body):
            x_live = _pmax(eff_x, t_low)
        elif not _isna(last_confirmed_trig):
            x_live = last_confirmed_trig
        else:
            x_live = NA

        out_sw[i], out_prev[i], out_x[i] = t_low, confirmed_low2, x_live
        out_swbar[i] = t_low_bar if t_low_bar is not None else -1

        # ---- alert: Higher Low ✅ + Price > X ✅ (ยิงครั้งเดียวต่อค่า X)
        hl = t_low > confirmed_low2
        px = c[i] > x_live
        if hl and px and (_isna(alerted_x) or x_live != alerted_x):
            out_alert[i] = True
            alerted_x = x_live

    res = pd.DataFrame({"Close": c, "k": k, "isOS": is_os, "sw": out_sw, "prev_sw": out_prev,
                        "x_live": out_x, "sw_bar": out_swbar, "alert": out_alert}, index=df.index)
    res["hl"] = res["sw"] > res["prev_sw"]
    res["px"] = res["Close"] > res["x_live"]
    return res


# ---------- ค่าแบบ Dashboard ของแท่งล่าสุด ----------
def tf_state(df: pd.DataFrame | None, p: Params) -> dict:
    """
    hl : True/False/None  -> Higher Low ✅ / ❌ / — (ยังไม่มี Previous SW)
    px : True/False/None  -> Price vs X ✅ / ❌ / — (ยังไม่มี X)
    """
    out = {"hl": None, "px": None, "x": NA, "sw": NA, "prev": NA, "alert_date": None, "bars_since": None}
    if df is None or len(df) < p.stoch_k + p.stoch_smooth + 5:
        return out
    r = hl_engine(df, p)
    last = r.iloc[-1]
    sw, prev, x = float(last["sw"]), float(last["prev_sw"]), float(last["x_live"])
    out.update(sw=sw, prev=prev, x=x)
    out["hl"] = None if (math.isnan(sw) or math.isnan(prev)) else bool(sw > prev)
    out["px"] = None if math.isnan(x) else bool(last["Close"] > x)
    alerts = np.flatnonzero(r["alert"].to_numpy())
    if alerts.size:
        out["alert_date"] = df.index[alerts[-1]]
        out["bars_since"] = int(len(r) - 1 - alerts[-1])
    return out


# ---------- สรุปสถานะแท่งล่าสุดสำหรับ scanner ----------
def evaluate(df: pd.DataFrame, p: Params) -> dict:
    """
    NEW   = Higher Low ✅ + Price > X ✅ และ alert เกิดภายใน N แท่งล่าสุด
    HOLD  = Higher Low ✅ + Price > X ✅ แต่ alert เกิดนานกว่า N แท่ง
    WATCH = Higher Low ✅ แต่ราคายังไม่ปิดเหนือ X
    """
    if len(df) < p.stoch_k + p.stoch_smooth + 5:
        return {"status": "NONE"}
    r = hl_engine(df, p)
    last = r.iloc[-1]
    if not last["hl"]:
        return {"status": "NONE"}
    info = {"sw_low": last["sw"], "prev_sw_low": last["prev_sw"], "x_level": last["x_live"],
            "sw_date": df.index[int(last["sw_bar"])] if last["sw_bar"] >= 0 else None,
            "in_os": bool(last["isOS"]), "k": last["k"]}
    alerts = np.flatnonzero(r["alert"].to_numpy())
    if last["px"]:
        bars_since = len(r) - 1 - alerts[-1] if alerts.size else None
        info.update(signal_date=df.index[alerts[-1]] if alerts.size else None, bars_since=bars_since)
        fresh = bars_since is not None and bars_since <= p.max_bars_since_signal
        info["status"] = "NEW" if fresh else "HOLD"
    else:
        info["status"] = "WATCH"
    return info
