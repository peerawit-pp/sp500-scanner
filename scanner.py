"""
scanner.py — แกนการสแกน (ไม่มี Streamlit) ใช้ร่วมกันระหว่าง app.py และ scan_job.py (สแกนอัตโนมัติ)

คำนวณ "สถานะดิบ" ของหุ้นทุกตัว (ยังไม่กรอง):
  Price (ราคาปิดแท่ง 1H ล่าสุด), EMA89 4H (MTF),
  หัว Fibo ขาขึ้น / ขาลง (Auto) บน 4H และ 1H  — ค่า NaN = "-"
การกรองตามเงื่อนไขในแถบซ้ายทำทีหลัง -> เปลี่ยนเงื่อนไขแล้วเห็นผลทันทีโดยไม่ต้องสแกนใหม่
"""
from __future__ import annotations

import json
import math

import pandas as pd

from fibo import drop_unclosed, mtf_ema
from heads import HeadParams, heads_last
from logic import resample_4h

TFS = ["4H", "1H"]
NAN = float("nan")
FORMAT = "fibo-heads-v1"


def frames_for(t: str, store: dict) -> dict:
    """store: {"1h": {ticker: df}} -> {"1H": df, "4H": df}"""
    out = {}
    if t in store.get("1h", {}):
        out["1H"] = store["1h"][t]
        out["4H"] = resample_4h(store["1h"][t])
    return out


def tf_frame(fr: dict, tf: str, confirmed_only: bool):
    d = fr.get(tf)
    if d is None or d.empty:
        return None
    d = drop_unclosed(d, tf) if confirmed_only else d
    return None if d.empty else d


def latest_price(fr: dict, confirmed_only: bool) -> float:
    d = tf_frame(fr, "1H", confirmed_only)
    return NAN if d is None else float(d["Close"].iloc[-1])


def symbol_state(meta: dict, fr: dict, hp: HeadParams, ema_len: int, confirmed_only: bool,
                 ema_conf: bool) -> dict | None:
    price = latest_price(fr, confirmed_only)
    if math.isnan(price):
        return None
    row = {**meta, "Price": price, "EMA4H": mtf_ema(fr.get("4H"), "4H", ema_len, ema_conf)}
    for tf in TFS:
        d = tf_frame(fr, tf, confirmed_only)
        up, dn = heads_last(d, hp)
        row[f"{tf}_up"] = up
        row[f"{tf}_dn"] = dn
        row[f"{tf}_last"] = None if d is None else d.index[-1]
    return row


def raw_states(uni: pd.DataFrame, store: dict, hp: HeadParams, ema_len: int = 89,
               confirmed_only: bool = True, ema_conf: bool = True) -> pd.DataFrame:
    """uni: Symbol, Yahoo, Name, Index, Sector, MarketCapB (+ Major)"""
    rows = []
    keep = [c for c in ("Symbol", "Yahoo", "Name", "Index", "Sector", "MarketCapB", "Major") if c in uni.columns]
    for rec in uni[keep].to_dict("records"):
        st_ = symbol_state(rec, frames_for(rec["Yahoo"], store), hp, ema_len, confirmed_only, ema_conf)
        if st_ is not None:
            rows.append(st_)
    return pd.DataFrame(rows)


def is_current_format(raw: pd.DataFrame) -> bool:
    return all(f"{tf}_{s}" in raw.columns for tf in TFS for s in ("up", "dn"))


# ---------- บันทึก / โหลดผล (สำหรับสแกนอัตโนมัติ) ----------
def save_raw(raw: pd.DataFrame, path_csv, meta: dict, path_meta) -> None:
    out = raw.copy()
    for c in out.columns:
        if c.endswith("_last"):
            out[c] = [None if v is None or (isinstance(v, float) and math.isnan(v)) else pd.Timestamp(v).isoformat()
                      for v in out[c]]
    out.to_csv(path_csv, index=False)
    with open(path_meta, "w", encoding="utf-8") as f:
        json.dump({**meta, "format": FORMAT}, f, ensure_ascii=False, indent=2, default=str)


def load_raw(path_csv, path_meta) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path_csv, keep_default_na=True)
    for c in raw.columns:
        if c.endswith("_last"):
            raw[c] = [None if (isinstance(v, float) and math.isnan(v)) else pd.Timestamp(v) for v in raw[c]]
    if "Major" in raw.columns:
        raw["Major"] = raw["Major"].astype(str).eq("True")
    with open(path_meta, encoding="utf-8") as f:
        meta = json.load(f)
    return raw, meta
