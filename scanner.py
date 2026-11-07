"""
scanner.py — แกนการสแกน (ไม่มี Streamlit) ใช้ร่วมกันระหว่าง app.py และ scan_job.py (สแกนอัตโนมัติ)

คำนวณ "สถานะดิบ" ของหุ้นทุกตัว (ยังไม่กรอง):
  Price, EMA89 4H และต่อ TF (4H / D / W): Higher Low, Price vs X, X, SW Low, Previous SW,
  วันที่ Alert ล่าสุด, จำนวนแท่งตั้งแต่ Alert, เวลาแท่งล่าสุด
การกรองตามเงื่อนไขในแถบซ้ายทำทีหลัง -> เปลี่ยนเงื่อนไขแล้วเห็นผลทันทีโดยไม่ต้องสแกนใหม่
"""
from __future__ import annotations

import json
import math

import pandas as pd

from fibo import drop_unclosed, mtf_ema
from logic import Params, resample_4h, tf_state

TFS = ["4H", "D", "W"]
NAN = float("nan")


def frames_for(t: str, store: dict) -> dict:
    """store: {"1h": {ticker: df}, "1d": ..., "1wk": ..., "1mo": ...} -> {TF: df}"""
    out = {}
    if t in store.get("1h", {}):
        out["1H"] = store["1h"][t]
        out["4H"] = resample_4h(store["1h"][t])
    for tf, itv in (("D", "1d"), ("W", "1wk"), ("M", "1mo")):
        if t in store.get(itv, {}):
            out[tf] = store[itv][t]
    return out


def tf_frame(fr: dict, tf: str, confirmed_only: bool):
    d = fr.get(tf)
    if d is None or d.empty:
        return None
    d = drop_unclosed(d, tf) if confirmed_only else d
    return None if d.empty else d


def latest_price(fr: dict, confirmed_only: bool) -> float:
    for tf in ("1H", "D", "W"):
        d = tf_frame(fr, tf, confirmed_only)
        if d is not None:
            return float(d["Close"].iloc[-1])
    return NAN


def symbol_state(meta: dict, fr: dict, p: Params, confirmed_only: bool, ema_conf: bool) -> dict | None:
    price = latest_price(fr, confirmed_only)
    if math.isnan(price):
        return None
    e4 = mtf_ema(fr.get("4H"), "4H", p.ema_len, ema_conf)
    row = {**meta, "Price": price, "EMA4H": e4}
    for tf in TFS:
        d = tf_frame(fr, tf, confirmed_only)
        s = tf_state(d, p)
        row[f"{tf}_hl"] = s["hl"]
        row[f"{tf}_px"] = s["px"]
        row[f"{tf}_x"] = s["x"]
        row[f"{tf}_sw"] = s["sw"]
        row[f"{tf}_prev"] = s["prev"]
        row[f"{tf}_alert"] = s["alert_date"]
        row[f"{tf}_bs"] = s["bars_since"]
        row[f"{tf}_last"] = None if d is None else d.index[-1]
    return row


def raw_states(uni: pd.DataFrame, store: dict, p: Params, confirmed_only: bool = True,
               ema_conf: bool = True, extra=None) -> pd.DataFrame:
    """uni: Symbol, Yahoo, Name, Index, Sector, MarketCapB (+ Major)
    extra(fr, price) -> dict  (ใช้เพิ่มคอลัมน์ เช่น Fibo)"""
    rows = []
    keep = [c for c in ("Symbol", "Yahoo", "Name", "Index", "Sector", "MarketCapB", "Major") if c in uni.columns]
    for rec in uni[keep].to_dict("records"):
        fr = frames_for(rec["Yahoo"], store)
        st_ = symbol_state(rec, fr, p, confirmed_only, ema_conf)
        if st_ is None:
            continue
        if extra:
            st_.update(extra(fr, st_["Price"]))
        rows.append(st_)
    return pd.DataFrame(rows)


# ---------- บันทึก / โหลดผล (สำหรับสแกนอัตโนมัติ) ----------
def save_raw(raw: pd.DataFrame, path_csv, meta: dict, path_meta) -> None:
    out = raw.copy()
    for c in out.columns:
        if c.endswith(("_alert", "_last")):
            out[c] = [None if v is None or (isinstance(v, float) and math.isnan(v)) else pd.Timestamp(v).isoformat()
                      for v in out[c]]
        if c.endswith(("_hl", "_px")):
            out[c] = ["" if v is None or (isinstance(v, float) and math.isnan(v)) else str(bool(v)) for v in out[c]]
    out.to_csv(path_csv, index=False)
    with open(path_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)


def load_raw(path_csv, path_meta) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path_csv, keep_default_na=True)
    for c in raw.columns:
        if c.endswith(("_hl", "_px")):
            raw[c] = [None if (isinstance(v, float) and math.isnan(v)) or v == "" else str(v) == "True"
                      for v in raw[c]]
        elif c.endswith(("_alert", "_last")):
            raw[c] = [None if (isinstance(v, float) and math.isnan(v)) else pd.Timestamp(v) for v in raw[c]]
        elif c.endswith("_bs"):
            raw[c] = [None if (isinstance(v, float) and math.isnan(v)) else int(v) for v in raw[c]]
    if "Major" in raw.columns:
        raw["Major"] = raw["Major"].astype(str).eq("True")
    with open(path_meta, encoding="utf-8") as f:
        meta = json.load(f)
    return raw, meta
