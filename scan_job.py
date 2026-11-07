"""
scan_job.py — สแกนอัตโนมัติ (รันบน GitHub Actions ตามเวลาที่ตั้งใน .github/workflows/scan.yml)

  1) รายชื่อหุ้น: S&P 500 + NASDAQ 100 + Dow 30 + NASDAQ Composite, market cap >= MIN_CAP_B (ค่าเริ่มต้น 10B)
  2) ราคาจาก Alpaca แบบ incremental (เก็บ cache ราคาไว้ใน .price_cache ระหว่างรอบด้วย actions/cache)
  3) คำนวณหัว Fibo ขาขึ้น / ขาลง (4H / 1H) + EMA89 4H ทุกตัว (ค่า Pine ตั้งต้น + แท่งที่ปิดแล้ว)
     -> results/scan_latest.csv + results/meta.json
  4) ถ้ายังไม่มีแท่ง 4H ใหม่ตั้งแต่รอบก่อน จะข้าม (ไม่ commit) เว้นแต่ FORCE=1

ตัวแปรแวดล้อม: ALPACA_KEY, ALPACA_SECRET (จาก GitHub Secrets), MIN_CAP_B, FORCE
รันเองบนคอม: ALPACA_KEY=... ALPACA_SECRET=... python scan_job.py
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time
from pathlib import Path

import pandas as pd

import data
import scanner
from fibo import drop_unclosed
from heads import HeadParams
from logic import resample_4h

HERE = Path(__file__).parent
CACHE = HERE / ".price_cache"
OUT = HERE / "results"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _progress_logger(itv: str):
    seen = {"d": -1}

    def f(d, n, stats=None):
        if d != seen["d"] and (d % 10 == 0 or d == n):
            seen["d"] = d
            s = stats or {}
            log(f"  {itv}: {d}/{n} กลุ่ม · คำขอ {s.get('req', 0)} · 429 {s.get('r429', 0)} · "
                f"timeout {s.get('timeouts', 0)}")
    return f


def _load(path: Path):
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:          # ไม่มีไฟล์ / ไฟล์เสีย -> ดึงใหม่
        return None


def _last_closed(store_1h: dict, tf: str) -> str | None:
    """เวลาแท่ง 4H ที่ปิดแล้วล่าสุด (ดูจากหุ้นใหญ่ที่มีข้อมูลแน่นอน)"""
    for t in ("AAPL", "MSFT", "NVDA", "AMZN"):
        if t in store_1h:
            d = drop_unclosed(resample_4h(store_1h[t]), tf)
            if not d.empty:
                return d.index[-1].strftime("%Y-%m-%d %H:%M")
    return None


def main() -> int:
    key, secret = os.environ.get("ALPACA_KEY", ""), os.environ.get("ALPACA_SECRET", "")
    if not (key and secret):
        log("ไม่พบ ALPACA_KEY / ALPACA_SECRET — ตั้งค่าใน GitHub: Settings → Secrets and variables → Actions")
        return 1
    min_cap = float(os.environ.get("MIN_CAP_B", "10") or 10)
    force = os.environ.get("FORCE", "0") == "1"
    t_all = time.time()

    uni = data.universe(data.INDEXES)
    uni = uni[(uni["MarketCapB"] >= min_cap) | (uni["MarketCapB"].isna() & uni["Major"])].reset_index(drop=True)
    tickers = list(uni["Yahoo"])
    log(f"รายชื่อหุ้น: {len(tickers):,} ตัว (market cap >= {min_cap:g}B)")

    CACHE.mkdir(exist_ok=True)
    store, missing = {}, {}
    for itv in ("1h",):                     # หัว Fibo ใช้แค่ 1H และ 4H (สร้างจาก 1H)
        t0 = time.time()
        cached = _load(CACHE / f"{itv}.pkl")
        mode = "incremental" if cached else "เต็มช่วง (ครั้งแรก)"
        store[itv] = data.alpaca_update(tickers, itv, key, secret, cached,
                                        progress=_progress_logger(itv))
        with open(CACHE / f"{itv}.pkl", "wb") as f:
            pickle.dump(store[itv], f, protocol=pickle.HIGHEST_PROTOCOL)
        missing[itv] = sorted(set(tickers) - set(store[itv]))
        log(f"ราคา {itv}: {len(store[itv]):,}/{len(tickers):,} ตัว · {mode} · {time.time() - t0:.0f} วิ")

    last4 = _last_closed(store["1h"], "4H")
    OUT.mkdir(exist_ok=True)
    old = {}
    if (OUT / "meta.json").exists():
        old = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))
    if not force and old.get("last_4h_bar") == last4 and old.get("min_cap_b") == min_cap \
            and old.get("format") == scanner.FORMAT:
        log(f"ยังไม่มีแท่ง 4H ใหม่ (ล่าสุด {last4}) — ข้ามรอบนี้")
        return 0

    t0 = time.time()
    raw = scanner.raw_states(uni, store, HeadParams(), ema_len=89, confirmed_only=True, ema_conf=True)
    log(f"คำนวณสถานะ {len(raw):,} ตัว · {time.time() - t0:.0f} วิ")
    meta = {
        "scanned_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "last_4h_bar": last4,
        "n_symbols": int(len(raw)),
        "min_cap_b": min_cap,
        "universe": f"S&P 500 + NASDAQ 100 + Dow 30 + NASDAQ Composite, ≥ {min_cap:g}B",
        "params": "Pine default (Stoch 9/3, OS 20, OB 80, Max Scan 80, Trail 5, Lookback 3 ทั้งขาขึ้น/ขาลง), "
                  "หัว Fibo 4H/1H, EMA89 4H, แท่งที่ปิดแล้ว",
        "missing": {k: len(v) for k, v in missing.items()},
        "duration_sec": round(time.time() - t_all),
    }
    scanner.save_raw(raw, OUT / "scan_latest.csv", meta, OUT / "meta.json")
    log(f"บันทึกผลแล้ว: results/scan_latest.csv ({len(raw):,} แถว) · รวม {meta['duration_sec']} วิ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
