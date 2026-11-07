"""
fibo.py — แปลงจาก Pine "Reversal Candle [Stoch Oversold] Fibo New ver7 + EMA89 MTF"

  f_htfTrigger (ver7)          -> fibo_engine()     ** คนละตัวกับ engine Higher Low ใน logic.py **
                                  (ver7: trail ไม่ตัด OB, ยืนยันเร็วเมื่อ %K >= OB, ไม่ทิ้ง cycle,
                                   reversal แล้วปิด cycle, timeout ปิด cycle เสมอ, Stoch fix 9/3)
  Sticky Fallback D / W / M    -> _sticky_fallback()
  Weekly OB-Qualified + Hidden -> _wob_hidden()
  Fibo levels                  -> fibo_levels()
  EMA89 MTF (conf / live)      -> mtf_ema()
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from logic import NA, _isna, _lookback_low, _pmax, ema, stoch_k

FIB_LEVELS = [("38.2%", 0.382), ("50.0%", 0.5), ("61.8%", 0.618), ("70.5%", 0.705), ("78.6%", 0.786)]


# ---------- แท่งที่ยังไม่ปิด ----------
def _ny_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return (idx.tz_localize("America/New_York") if idx.tz is None else idx.tz_convert("America/New_York"))


def bar_end(ts: pd.Timestamp, tf: str) -> pd.Timestamp:
    """เวลาปิดแท่ง (NY) ของแท่งที่เริ่มที่ ts"""
    day_close = ts.normalize() + pd.Timedelta(hours=16)
    if tf == "1H":
        return min(ts + pd.Timedelta(hours=1), day_close)
    if tf == "4H":
        return min(ts + pd.Timedelta(hours=4), day_close)
    if tf == "D":
        return day_close
    if tf == "W":
        return ts.normalize() - pd.Timedelta(days=ts.weekday()) + pd.Timedelta(days=4, hours=16)
    if tf == "M":
        return (ts.normalize() + pd.offsets.MonthEnd(0)) + pd.Timedelta(hours=16)
    raise ValueError(tf)


def last_bar_closed(df: pd.DataFrame, tf: str, now: pd.Timestamp | None = None) -> bool:
    if df.empty:
        return True
    now = now or pd.Timestamp.now(tz="America/New_York")
    return now >= bar_end(_ny_index(df.index[-1:])[0], tf)


def drop_unclosed(df: pd.DataFrame, tf: str, now=None) -> pd.DataFrame:
    """ตัดแท่งสุดท้ายทิ้งเฉพาะเมื่อยังไม่ปิดจริง (ตลาดปิดแล้วจะไม่ตัด)"""
    return df if last_bar_closed(df, tf, now) else df.iloc[:-1]


# ---------- EMA89 MTF ----------
def mtf_ema(df_tf: pd.DataFrame, tf: str, length: int, confirmed: bool, now=None) -> float:
    """
    confirmed = ta.ema(src,len)[1] + lookahead_on  -> EMA ของแท่ง HTF ที่ปิดแล้ว
    live      = ta.ema(src,len) + lookahead_off    -> EMA รวมแท่งที่ยังไม่ปิด
    """
    if df_tf is None or df_tf.empty:
        return NA
    d = drop_unclosed(df_tf, tf, now) if confirmed else df_tf
    if len(d) < length:
        return NA
    return float(ema(d["Close"], length).iloc[-1])


# ---------- f_htfTrigger (ver7) ----------
def fibo_engine(df: pd.DataFrame, ios=20.0, iob=80.0, reset=80, trail=5, lookback=3) -> pd.DataFrame:
    o = df["Open"].to_numpy(float); h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float); c = df["Close"].to_numpy(float)
    n = len(df)
    k = stoch_k(df, 9, 3).to_numpy(float)          # ใน Pine ver7 hardcode 9, 3
    is_os = k < ios

    scan = trailing = wait_rev = cycle_active = track_high = ob_hit = False
    t_low = t_low_body = x_body = trigger = last_trig = NA
    t_low_bar = scan_start = exit_bar = exit_os_time = last_exit_time = None
    conf_sw_low = sw_high_after = prev_conf_sw_low = prev_sw_high_after = NA
    sw_low_time = sw_high_time = None
    q_low = q_high = NA
    q_low_time = q_high_time = None

    cols = {key: np.full(n, np.nan) for key in
            ["conf_sw_low", "sw_high", "prev_sw_low", "prev_sw_high", "q_sw_low", "q_sw_high",
             "last_trig", "x_live", "q_low_time", "q_high_time", "last_exit_time"]}

    def cur(i):
        return l[i], i, _pmax(o[i], c[i]), (_pmax(o[i - 1], c[i - 1]) if i > 0 else NA)

    for i in range(n):
        prev_os = is_os[i - 1] if i > 0 else False
        enter_os = is_os[i] and not prev_os
        exit_os = (not is_os[i]) and prev_os

        if enter_os:
            scan, trailing, wait_rev, exit_bar = True, False, False, None
            lb_low, lb_bar, lb_body, lb_xbody, _ = _lookback_low(o, h, l, c, i, lookback)
            if not cycle_active:
                cycle_active, trigger, scan_start = True, NA, i
                t_low, t_low_bar, t_low_body, x_body = lb_low, lb_bar, lb_body, lb_xbody
            else:
                trigger, scan_start = NA, i
                if lb_low < t_low:
                    t_low, t_low_bar, t_low_body, x_body = lb_low, lb_bar, lb_body, lb_xbody

        if scan and not trailing and not wait_rev:
            if i - scan_start > reset:
                scan, cycle_active = False, False
            elif is_os[i] and l[i] < t_low:
                t_low, t_low_bar, t_low_body, x_body = cur(i)
            elif exit_os:
                if l[i] < t_low:
                    t_low, t_low_bar, t_low_body, x_body = cur(i)
                exit_os_time = i
                scan, trailing, exit_bar = False, True, i

        if trailing and not wait_rev:
            if l[i] < t_low:
                t_low, t_low_bar, t_low_body, x_body = cur(i)
            ob_early = k[i] >= iob
            if (exit_bar is not None and i - exit_bar >= trail) or ob_early:
                trailing, wait_rev = False, True
                use_x = t_low_body if x_body < t_low_body else x_body
                trigger = t_low if use_x < t_low else use_x
                last_trig = trigger
                last_exit_time = exit_os_time
                prev_conf_sw_low, prev_sw_high_after = conf_sw_low, sw_high_after
                conf_sw_low = t_low
                track_high = True
                lb_high, hi_off, ob_in_leg = NA, 0, False
                lb_len = i - t_low_bar
                if lb_len >= 0:
                    for j in range(lb_len + 1):
                        hj = h[i - j]
                        if _isna(lb_high) or hj > lb_high:
                            lb_high, hi_off = hj, j
                        if k[i - j] >= iob:
                            ob_in_leg = True
                sw_high_after = h[i] if _isna(lb_high) else lb_high
                sw_high_time = i - hi_off
                sw_low_time = i - max(lb_len, 0)
                ob_hit = ob_in_leg or ob_early

        if wait_rev and not _isna(trigger) and c[i] > trigger:
            wait_rev, trigger, cycle_active = False, NA, False

        if track_high:
            if h[i] > sw_high_after or _isna(sw_high_after):
                sw_high_after, sw_high_time = h[i], i
            if not _isna(prev_conf_sw_low):
                if _isna(prev_sw_high_after) or h[i] > prev_sw_high_after:
                    prev_sw_high_after = h[i]
            if not ob_hit and k[i] >= iob:
                ob_hit = True

        if ob_hit and not _isna(conf_sw_low):
            q_low, q_high, q_low_time, q_high_time = conf_sw_low, sw_high_after, sw_low_time, sw_high_time

        if wait_rev and not _isna(trigger):
            x_live = trigger
        elif (scan or trailing) and not _isna(x_body):
            x_live = _pmax(x_body, t_low)
        else:
            x_live = NA

        for key, v in (("conf_sw_low", conf_sw_low), ("sw_high", sw_high_after),
                       ("prev_sw_low", prev_conf_sw_low), ("prev_sw_high", prev_sw_high_after),
                       ("q_sw_low", q_low), ("q_sw_high", q_high), ("last_trig", last_trig),
                       ("x_live", x_live), ("q_low_time", q_low_time), ("q_high_time", q_high_time),
                       ("last_exit_time", last_exit_time)):
            cols[key][i] = NA if v is None else v

    out = pd.DataFrame(cols, index=df.index)
    out["Close"] = c
    out["k"] = k
    return out


# ---------- Sticky fallback (D / W / M) ----------
def _sticky_fallback(r: pd.DataFrame) -> np.ndarray:
    conf, prev, close = r["conf_sw_low"].to_numpy(), r["prev_sw_low"].to_numpy(), r["Close"].to_numpy()
    fb, out = False, np.zeros(len(r), bool)
    for i in range(len(r)):
        if not fb and not np.isnan(conf[i]) and close[i] < conf[i] and not np.isnan(prev[i]):
            fb = True
        if fb and i > 0 and not np.isnan(conf[i]) and not np.isnan(conf[i - 1]) and conf[i] != conf[i - 1]:
            fb = False
        out[i] = fb
    return out


# ---------- Weekly OB-Qualified hidden ----------
def _wob_hidden(r: pd.DataFrame, last_closed: bool) -> bool:
    """closeWConf = close[1] ของ Weekly (แท่งที่ปิดแล้ว), ซ่อนค้างจนกว่า qLowTime จะเปลี่ยน"""
    ql, qt, cl = r["q_sw_low"].to_numpy(), r["q_low_time"].to_numpy(), r["Close"].to_numpy()
    hidden, n = False, len(r)
    for j in range(n):
        # แท่งสุดท้ายของสัปดาห์ j: เห็น q[j] กับ close ของสัปดาห์ก่อน
        if hidden and j > 0 and not np.isnan(qt[j]) and not np.isnan(qt[j - 1]) and qt[j] != qt[j - 1]:
            hidden = False
        if not hidden and j > 0 and not np.isnan(ql[j]) and cl[j - 1] < ql[j]:
            hidden = True
        # ระหว่างสัปดาห์ถัดไป: closeWConf = close ของสัปดาห์ j ที่ปิดแล้ว
        if (j < n - 1 or last_closed) and not hidden and not np.isnan(ql[j]) and cl[j] < ql[j]:
            hidden = True
    return hidden


# ---------- Fibo levels ----------
def fibo_levels(df_tf: pd.DataFrame, source: str, price: float, last_closed: bool = True,
                ios=20.0, iob=80.0, reset=80, trail=5, lookback=3) -> dict:
    """
    source: "Daily" | "Weekly" | "Monthly" (ใช้ df ของ TF นั้น) | "Weekly (OB)" (ใช้ df Weekly)
    คืน: label, fallback, sw_high, sw_low, levels{name: price}, nearest support ใต้ราคา
    """
    empty = {"label": source, "fallback": False, "sw_high": NA, "sw_low": NA, "levels": {},
             "support_name": None, "support": NA, "support_dist": NA}
    if df_tf is None or len(df_tf) < 20:
        return empty
    r = fibo_engine(df_tf, ios, iob, reset, trail, lookback)
    last = r.iloc[-1]
    tag = {"Daily": "D", "Weekly": "W", "Monthly": "M", "Weekly (OB)": "W-OB"}[source]
    if source == "Weekly (OB)":
        hidden = _wob_hidden(r, last_closed)
        hi, lo = (NA, NA) if hidden else (last["q_sw_high"], last["q_sw_low"])
        label, fb = ("W-OB รอ SW ใหม่" if hidden else "W-OB"), hidden
    else:
        fb = bool(_sticky_fallback(r)[-1])
        hi, lo = (last["prev_sw_high"], last["prev_sw_low"]) if fb else (last["sw_high"], last["conf_sw_low"])
        label = f"{tag} ⚠ Prev" if fb else tag
    res = dict(empty, label=label, fallback=fb, sw_high=hi, sw_low=lo)
    if _isna(hi) or _isna(lo):
        return res
    rng = hi - lo
    levels = {name: hi - rng * f for name, f in FIB_LEVELS}
    levels["SW Low"] = lo
    res["levels"] = levels
    below = {nm: v for nm, v in levels.items() if v <= price}
    if below:
        nm = max(below, key=below.get)
        res.update(support_name=nm, support=below[nm], support_dist=(below[nm] / price - 1) * 100)
    return res
