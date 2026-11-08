"""
heads.py — หัว Fibo ขาขึ้น / ขาลง (Auto) แปลงจาก Pine "Reversal Candle [Stoch Oversold]" บรรทัดต่อบรรทัด

  Pine                         Python
  f_lookbackLow / High    ->   _lookback_low / _lookback_high
  f_htfTriggerHigh        ->   trigger_high()      (Lower High state machine, จบ cycle เมื่อ %K < OS)
  f_htfTriggerLowM        ->   trigger_low_m()     (mirror 100% ของ f_htfTriggerHigh, จบ cycle เมื่อ %K > OB)
  f_headLatchHigh         ->   head_latch_high()   -> headC4 / headC1  = 📉 หัว Fibo ขาลง (Auto)
  f_headLatchLow          ->   head_latch_low()    -> headL4 / headL1  = 📈 หัว Fibo ขาขึ้น (Auto)

ใน Pine ทั้งก้อนถูกคำนวณภายใน request.security(..., "240"/"60", lookahead_off) บนแท่งของ TF นั้นเอง
-> ที่นี่จึงรันบนแท่ง 4H / 1H ของ TF นั้นโดยตรง
Dashboard (f_combHeadRow): ค่า != na -> แสดงค่า (✅), ค่า na -> "-"

กติกา na ของ Pine:
  - เปรียบเทียบกับ na = false        -> NaN ใน Python ก็ให้ False
  - math.max / math.min กับ na = na  -> _pmax / _pmin
  - อ้างแท่งก่อนแท่งแรก [i] = na
  - for i = 1 to 0 (Lookback = 0) ใน Pine วนถอยหลัง i = 1, 0  -> _pine_range()
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from logic import stoch_k

NA = float("nan")


@dataclass
class HeadParams:
    # ค่า default = input ใน Pine
    k_len: int = 9                 # iK
    smooth: int = 3                # iSm
    os_level: float = 20.0         # iOS
    ob_level: float = 80.0         # iOB  (group "Lower High Reversal")
    # หัว Fibo ขาขึ้น (f_headLatchLow) ใช้ iReset / iTrail / iSwLookback / iCloseOnTimeout
    reset_low: int = 80
    trail_low: int = 5
    lookback_low: int = 3
    timeout_low: bool = True
    # หัว Fibo ขาลง (f_headLatchHigh) ใช้ iResetHigh / iTrailHigh / iSwLookbackHigh / iCloseOnTimeoutHigh
    reset_high: int = 80
    trail_high: int = 5
    lookback_high: int = 3
    timeout_high: bool = True


def _isna(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _pmax(a, b):
    return NA if _isna(a) or _isna(b) else max(a, b)


def _pmin(a, b):
    return NA if _isna(a) or _isna(b) else min(a, b)


def _pine_range(start: int, end: int):
    """for i = start to end ของ Pine: ถ้า start > end จะวนถอยหลัง"""
    return range(start, end + 1) if start <= end else range(start, end - 1, -1)


def _at(arr, j):
    return arr[j] if 0 <= j < len(arr) else NA


# ---------- f_lookbackLow / f_lookbackHigh ----------
def _lookback_low(o, h, l, c, i, lookback):
    t_low, t_bar = l[i], i
    t_body = _pmax(o[i], c[i])
    x_body = _pmax(_at(o, i - 1), _at(c, i - 1))
    x_bar = i - 1
    for j in _pine_range(1, lookback):
        lj = _at(l, i - j)
        if lj < t_low:
            t_low, t_bar = lj, i - j
            t_body = _pmax(_at(o, i - j), _at(c, i - j))
            x_body = _pmax(_at(o, i - j - 1), _at(c, i - j - 1))
            x_bar = i - j - 1
    return t_low, t_bar, t_body, x_body, x_bar


def _lookback_high(o, h, l, c, i, lookback):
    t_high, t_bar = h[i], i
    t_body = _pmin(o[i], c[i])
    x_body = _pmin(_at(o, i - 1), _at(c, i - 1))
    x_bar = i - 1
    for j in _pine_range(1, lookback):
        hj = _at(h, i - j)
        if hj > t_high:
            t_high, t_bar = hj, i - j
            t_body = _pmin(_at(o, i - j), _at(c, i - j))
            x_body = _pmin(_at(o, i - j - 1), _at(c, i - j - 1))
            x_bar = i - j - 1
    return t_high, t_bar, t_body, x_body, x_bar


def _arrays(df: pd.DataFrame):
    return (df["Open"].to_numpy(float), df["High"].to_numpy(float),
            df["Low"].to_numpy(float), df["Close"].to_numpy(float))


# ---------- f_htfTriggerHigh ----------
def trigger_high(df: pd.DataFrame, hp: HeadParams) -> dict[str, np.ndarray]:
    """คืน dict ของ array รายแท่ง: sw, prev, x, k, is_ob  (= [_swHighLive, _prevForCompare, _xBodyLive, _k, _isOB])"""
    o, h, l, c = _arrays(df)
    n = len(df)
    k = stoch_k(df, hp.k_len, hp.smooth).to_numpy(float)
    is_ob = k > hp.ob_level
    iob, ios, reset, trail, lookback, close_to = (hp.ob_level, hp.os_level, hp.reset_high, hp.trail_high,
                                                  hp.lookback_high, hp.timeout_high)

    scan = trailing = wait_rev = cycle_active = False
    t_high = t_body = x_body = trigger = NA
    t_bar = x_bar = scan_start = exit_bar = None
    confirmed_high2 = last_trig = NA
    true_high = true_body = true_xbody = NA
    true_bar = true_xbar = None

    out_sw, out_prev, out_x = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)

    for i in range(n):
        prev_ob = bool(is_ob[i - 1]) if i > 0 else False
        enter_ob = bool(is_ob[i]) and not prev_ob
        exit_ob = (not is_ob[i]) and prev_ob
        cur_body = _pmin(o[i], c[i])
        cur_xbody = _pmin(o[i - 1], c[i - 1]) if i > 0 else NA

        if enter_ob:
            scan, trailing, wait_rev, exit_bar = True, False, False, None
            lb_high, lb_bar, lb_body, lb_xbody, lb_xbar = _lookback_high(o, h, l, c, i, lookback)
            if not cycle_active:
                cycle_active = True
                confirmed_high2 = t_high
                trigger = NA
                scan_start = i
                t_high, t_bar, t_body, x_body, x_bar = lb_high, lb_bar, lb_body, lb_xbody, lb_xbar
                true_high = true_body = true_xbody = NA
                true_bar = true_xbar = None
            else:
                trigger = NA
                scan_start = i
                if lb_high > t_high:
                    t_high, t_bar, t_body, x_body, x_bar = lb_high, lb_bar, lb_body, lb_xbody, lb_xbar

        if scan and not trailing and not wait_rev:
            if i - scan_start > reset:
                scan = False
                if close_to:
                    cycle_active = False
            elif is_ob[i] and h[i] > t_high:
                t_high, t_bar, t_body, x_body, x_bar = h[i], i, cur_body, cur_xbody, i - 1
            elif exit_ob:
                if h[i] > t_high:
                    t_high, t_bar, t_body, x_body, x_bar = h[i], i, cur_body, cur_xbody, i - 1
                scan, trailing, exit_bar = False, True, i

        if trailing and not wait_rev:
            hit_os = k[i] < ios
            if not hit_os and h[i] > t_high:
                t_high, t_bar, t_body, x_body, x_bar = h[i], i, cur_body, cur_xbody, i - 1
            time_up = exit_bar is not None and i - exit_bar >= trail
            if hit_os or time_up:
                trailing = False
                use_x = t_body if x_body > t_body else x_body
                cand = t_high if use_x > t_high else use_x
                if c[i] < cand:
                    wait_rev = False
                else:
                    wait_rev = True
                    trigger = cand
                    last_trig = cand

        if wait_rev and not _isna(trigger) and c[i] < trigger:
            wait_rev, trigger = False, NA

        if cycle_active and (_isna(true_high) or h[i] > true_high):
            true_high, true_bar, true_body, true_xbody, true_xbar = h[i], i, cur_body, cur_xbody, i - 1

        if cycle_active and k[i] < ios:
            if not _isna(true_high) and true_high > t_high:
                t_high, t_bar, t_body, x_body, x_bar = true_high, true_bar, true_body, true_xbody, true_xbar
                use_x = t_body if x_body > t_body else x_body
                cand = t_high if use_x > t_high else use_x
                last_trig = cand
                if wait_rev:
                    trigger = cand
            cycle_active = False

        eff_x = t_body if x_body > t_body else x_body
        if wait_rev and not _isna(trigger):
            x_live = trigger
        elif (scan or trailing) and not _isna(x_body):
            x_live = _pmin(eff_x, t_high)
        elif not _isna(last_trig):
            x_live = last_trig
        else:
            x_live = NA
        out_sw[i], out_prev[i], out_x[i] = t_high, confirmed_high2, x_live

    return {"sw": out_sw, "prev": out_prev, "x": out_x, "k": k, "flag": is_ob}


# ---------- f_htfTriggerLowM ----------
def trigger_low_m(df: pd.DataFrame, hp: HeadParams) -> dict[str, np.ndarray]:
    """คืน dict ของ array รายแท่ง: sw, prev, x, k, is_os  (= [_swLowLive, _prevForCompare, _xBodyLive, _k, _isOS])"""
    o, h, l, c = _arrays(df)
    n = len(df)
    k = stoch_k(df, hp.k_len, hp.smooth).to_numpy(float)
    is_os = k < hp.os_level
    ios, iob, reset, trail, lookback, close_to = (hp.os_level, hp.ob_level, hp.reset_low, hp.trail_low,
                                                  hp.lookback_low, hp.timeout_low)

    scan = trailing = wait_rev = cycle_active = False
    t_low = t_body = x_body = trigger = NA
    t_bar = x_bar = scan_start = exit_bar = None
    confirmed_low2 = last_trig = NA
    true_low = true_body = true_xbody = NA
    true_bar = true_xbar = None

    out_sw, out_prev, out_x = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)

    for i in range(n):
        prev_os = bool(is_os[i - 1]) if i > 0 else False
        enter_os = bool(is_os[i]) and not prev_os
        exit_os = (not is_os[i]) and prev_os
        cur_body = _pmax(o[i], c[i])
        cur_xbody = _pmax(o[i - 1], c[i - 1]) if i > 0 else NA

        if enter_os:
            scan, trailing, wait_rev, exit_bar = True, False, False, None
            lb_low, lb_bar, lb_body, lb_xbody, lb_xbar = _lookback_low(o, h, l, c, i, lookback)
            if not cycle_active:
                cycle_active = True
                confirmed_low2 = t_low
                trigger = NA
                scan_start = i
                t_low, t_bar, t_body, x_body, x_bar = lb_low, lb_bar, lb_body, lb_xbody, lb_xbar
                true_low = true_body = true_xbody = NA
                true_bar = true_xbar = None
            else:
                trigger = NA
                scan_start = i
                if lb_low < t_low:
                    t_low, t_bar, t_body, x_body, x_bar = lb_low, lb_bar, lb_body, lb_xbody, lb_xbar

        if scan and not trailing and not wait_rev:
            if i - scan_start > reset:
                scan = False
                if close_to:
                    cycle_active = False
            elif is_os[i] and l[i] < t_low:
                t_low, t_bar, t_body, x_body, x_bar = l[i], i, cur_body, cur_xbody, i - 1
            elif exit_os:
                if l[i] < t_low:
                    t_low, t_bar, t_body, x_body, x_bar = l[i], i, cur_body, cur_xbody, i - 1
                scan, trailing, exit_bar = False, True, i

        if trailing and not wait_rev:
            hit_ob = k[i] > iob
            if not hit_ob and l[i] < t_low:
                t_low, t_bar, t_body, x_body, x_bar = l[i], i, cur_body, cur_xbody, i - 1
            time_up = exit_bar is not None and i - exit_bar >= trail
            if hit_ob or time_up:
                trailing = False
                use_x = t_body if x_body < t_body else x_body
                cand = t_low if use_x < t_low else use_x
                if c[i] > cand:
                    wait_rev = False
                else:
                    wait_rev = True
                    trigger = cand
                    last_trig = cand

        if wait_rev and not _isna(trigger) and c[i] > trigger:
            wait_rev, trigger = False, NA

        if cycle_active and (_isna(true_low) or l[i] < true_low):
            true_low, true_bar, true_body, true_xbody, true_xbar = l[i], i, cur_body, cur_xbody, i - 1

        if cycle_active and k[i] > iob:
            if not _isna(true_low) and true_low < t_low:
                t_low, t_bar, t_body, x_body, x_bar = true_low, true_bar, true_body, true_xbody, true_xbar
                use_x = t_body if x_body < t_body else x_body
                cand = t_low if use_x < t_low else use_x
                last_trig = cand
                if wait_rev:
                    trigger = cand
            cycle_active = False

        eff_x = t_body if x_body < t_body else x_body
        if wait_rev and not _isna(trigger):
            x_live = trigger
        elif (scan or trailing) and not _isna(x_body):
            x_live = _pmax(eff_x, t_low)
        elif not _isna(last_trig):
            x_live = last_trig
        else:
            x_live = NA
        out_sw[i], out_prev[i], out_x[i] = t_low, confirmed_low2, x_live

    return {"sw": out_sw, "prev": out_prev, "x": out_x, "k": k, "flag": is_os}


# ---------- f_headLatchHigh ----------
def head_latch_high(df: pd.DataFrame, hp: HeadParams) -> np.ndarray:
    """📉 หัว Fibo ขาลง (Auto) รายแท่ง (NaN = '-')"""
    tr = trigger_high(df, hp)
    sw, prv, x, is_ob = tr["sw"], tr["prev"], tr["x"], tr["flag"]
    hi, cl = df["High"].to_numpy(float), df["Close"].to_numpy(float)
    n = len(df)
    head = ref_prev = last_prev = pp = ref_pp = NA
    ob_seen = inval = ref_broken = False
    out = np.full(n, np.nan)
    for i in range(n):
        swH, prevH, xH = sw[i], prv[i], x[i]
        if not _isna(prevH) and (_isna(last_prev) or prevH != last_prev):
            pp = last_prev
            last_prev = prevH
        if is_ob[i]:
            ob_seen = True
        sw_updated = not _isna(head) and not _isna(swH) and swH > head
        if (ob_seen or sw_updated) and not _isna(swH) and not _isna(prevH) and swH < prevH \
                and not _isna(xH) and cl[i] < xH:
            head, ref_prev, ref_pp = swH, prevH, pp
            ref_broken = ob_seen = inval = False
        if not _isna(head) and hi[i] > head:
            inval = True
        if not _isna(ref_prev) and hi[i] > ref_prev:
            ref_broken = True
        if not _isna(head) and not _isna(swH) and not _isna(prevH) and swH > prevH and swH > head:
            ref_broken = True
        ref_ok = not _isna(ref_prev) and not _isna(ref_pp) and ref_prev < ref_pp
        if _isna(head):
            out[i] = NA
        elif not inval:
            out[i] = head
        elif not ref_broken and ref_ok:
            out[i] = ref_prev
        else:
            out[i] = NA
    return out


# ---------- f_headLatchLow ----------
def head_latch_low(df: pd.DataFrame, hp: HeadParams) -> np.ndarray:
    """📈 หัว Fibo ขาขึ้น (Auto) รายแท่ง (NaN = '-')"""
    tr = trigger_low_m(df, hp)
    sw, prv, x, is_os = tr["sw"], tr["prev"], tr["x"], tr["flag"]
    lo, cl = df["Low"].to_numpy(float), df["Close"].to_numpy(float)
    n = len(df)
    head = ref_prev = last_prev = pp = ref_pp = NA
    os_seen = inval = ref_broken = False
    out = np.full(n, np.nan)
    for i in range(n):
        swL, prevL, xL = sw[i], prv[i], x[i]
        if not _isna(prevL) and (_isna(last_prev) or prevL != last_prev):
            pp = last_prev
            last_prev = prevL
        if is_os[i]:
            os_seen = True
        sw_updated = not _isna(head) and not _isna(swL) and swL < head
        if (os_seen or sw_updated) and not _isna(swL) and not _isna(prevL) and swL > prevL \
                and not _isna(xL) and cl[i] > xL:
            head, ref_prev, ref_pp = swL, prevL, pp
            ref_broken = os_seen = inval = False
        if not _isna(head) and lo[i] < head:
            inval = True
        if not _isna(ref_prev) and lo[i] < ref_prev:
            ref_broken = True
        if not _isna(head) and not _isna(swL) and not _isna(prevL) and swL < prevL and swL < head:
            ref_broken = True
        ref_ok = not _isna(ref_prev) and not _isna(ref_pp) and ref_prev > ref_pp
        if _isna(head):
            out[i] = NA
        elif not inval:
            out[i] = head
        elif not ref_broken and ref_ok:
            out[i] = ref_prev
        else:
            out[i] = NA
    return out


def heads_last(df: pd.DataFrame | None, hp: HeadParams) -> tuple[float, float]:
    """(หัว Fibo ขาขึ้น, หัว Fibo ขาลง) ของแท่งล่าสุด  — NaN = '-'"""
    if df is None or len(df) < hp.k_len + hp.smooth + 2:
        return NA, NA
    return float(head_latch_low(df, hp)[-1]), float(head_latch_high(df, hp)[-1])


# ---------- Watchlist: เวลาที่ "มีหัว Fibo ขาลง (4H หรือ 1H)" เปลี่ยนเป็น "ไม่มีหัว Fibo ขาลง" ----------
def _ends(idx: pd.DatetimeIndex, tf: str) -> np.ndarray:
    """เวลาปิดแท่ง (UTC ns) ของแต่ละแท่ง"""
    from fibo import bar_end
    ny = idx.tz_localize("America/New_York") if idx.tz is None else idx.tz_convert("America/New_York")
    return np.array([bar_end(t, tf).value for t in ny], dtype="int64")


def heads_with_clear(df1h: pd.DataFrame | None, df4h: pd.DataFrame | None, hp: HeadParams) -> dict:
    """
    หัว Fibo ล่าสุดของ 4H / 1H + เวลาที่กลุ่มเปลี่ยนจาก "มีหัวขาลง" -> "ไม่มีหัวขาลง" ครั้งล่าสุด
    ไล่ตามเวลาปิดแท่ง 1H ทุกแท่ง: สถานะ = หัวขาลง 1H ของแท่งนั้น หรือ หัวขาลง 4H ของแท่ง 4H ล่าสุดที่ปิดแล้ว ณ เวลานั้น
    clear_at = เวลาปิดแท่งแรกที่ไม่มีหัวขาลงทั้งสอง TF (และยังไม่มีจนถึงปัจจุบัน) — None ถ้ายังมีหัวขาลงอยู่
               หรือในช่วงข้อมูลไม่เคยมีหัวขาลงเลย
    """
    out = {"4H_up": NA, "4H_dn": NA, "1H_up": NA, "1H_dn": NA,
           "dn_clear_at": None, "dn_prev_4H": NA, "dn_prev_1H": NA,
           "dn_os_4H": None, "dn_os_1H": None}
    ok = lambda d: d is not None and len(d) >= hp.k_len + hp.smooth + 2   # noqa: E731
    up1 = dn1 = up4 = dn4 = None
    if ok(df1h):
        up1, dn1 = head_latch_low(df1h, hp), head_latch_high(df1h, hp)
        out["1H_up"], out["1H_dn"] = float(up1[-1]), float(dn1[-1])
    if ok(df4h):
        up4, dn4 = head_latch_low(df4h, hp), head_latch_high(df4h, hp)
        out["4H_up"], out["4H_dn"] = float(up4[-1]), float(dn4[-1])
    if dn1 is None:
        return out
    t1 = _ends(df1h.index, "1H")
    dn4_at = np.full(len(t1), np.nan)
    if dn4 is not None:
        t4 = _ends(df4h.index, "4H")
        j = np.searchsorted(t4, t1, side="right") - 1          # แท่ง 4H ล่าสุดที่ปิดแล้ว ณ เวลาปิดแท่ง 1H
        valid = j >= 0
        dn4_at[valid] = dn4[j[valid]]
    has = ~np.isnan(dn1) | ~np.isnan(dn4_at)
    if has[-1] or not has.any():
        return out
    k = int(np.flatnonzero(has)[-1])                            # แท่งสุดท้ายที่ยังมีหัวขาลง
    out["dn_clear_at"] = pd.Timestamp(int(t1[k + 1]), tz="UTC").tz_convert("America/New_York")
    out["dn_prev_4H"], out["dn_prev_1H"] = float(dn4_at[k]), float(dn1[k])
    # หัวขาลงที่หายไป "ผ่าน Oversold" หรือยัง: %K ของ TF นั้น < OS อย่างน้อย 1 แท่ง
    # ตลอดช่วงที่หัวนั้นแสดงต่อเนื่อง (ตั้งแต่แท่งที่หัวเกิด จนถึงแท่งสุดท้ายก่อนหัวหาย)
    if not np.isnan(dn1[k]):
        out["dn_os_1H"] = _passed_os(dn1, stoch_k(df1h, hp.k_len, hp.smooth).to_numpy(float), k, hp.os_level)
    if dn4 is not None and not np.isnan(dn4_at[k]):
        out["dn_os_4H"] = _passed_os(dn4, stoch_k(df4h, hp.k_len, hp.smooth).to_numpy(float), int(j[k]), hp.os_level)
    return out


def _passed_os(head: np.ndarray, k: np.ndarray, end: int, os_level: float) -> bool:
    start = end
    while start > 0 and not np.isnan(head[start - 1]):
        start -= 1
    return bool(np.any(k[start:end + 1] < os_level))
