"""
US Stock Multi-TF Scanner (S&P 500 / NASDAQ 100 / Dow 30 / NASDAQ Composite) — Higher Low + Price > X บน 4H / D / W  +  MTF EMA89 4H
ลอจิกแปลงจาก Pine:
  - "Reversal Candle [Stoch Oversold]"                      -> logic.py  (Higher Low + Price > X)
  - "Reversal Candle [Stoch Oversold] Fibo New ver7 + EMA89" -> fibo.py   (EMA89 MTF + Fibo Support)
รัน:  streamlit run app.py
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
import scanner
from fibo import FIB_LEVELS, drop_unclosed, fibo_levels, last_bar_closed, mtf_ema
from logic import Params, ema, hl_engine, resample_4h, tf_state

st.set_page_config(page_title="US Stock Multi-TF Scanner", page_icon="📈", layout="wide")
st.title("📈 US Stock Scanner — Higher Low (4H / D / W) + EMA89 4H")
st.caption("ลอจิกจาก Reversal Candle [Stoch Oversold] + MTF EMA89 4H · ราคาจาก Alpaca (SIP ดีเลย์ 15 นาที) "
           "หรือ Yahoo Finance · ใช้ประกอบการตัดสินใจเท่านั้น ยืนยันกราฟบน TradingView ก่อนเข้า order")

TFS = scanner.TFS
SRC = {"1h": "730d", "1d": "10y", "1wk": "10y", "1mo": "max"}
FIBO_TF = {"Daily": "D", "Weekly": "W", "Weekly (OB)": "W", "Monthly": "M"}
ALPACA, YAHOO = "Alpaca (แนะนำ)", "Yahoo Finance"
NAN = float("nan")


def _secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:          # ไม่มีไฟล์ secrets
        return ""


# ---------- cache ----------
@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_universe(indexes: tuple) -> pd.DataFrame:
    return data.universe(list(indexes))


@st.cache_data(ttl=15 * 60, show_spinner=False)
def _yahoo(tickers: tuple, interval: str):
    return data.download(list(tickers), interval, SRC[interval])


PRICE_TTL = 15 * 60


@st.cache_resource
def _price_cache() -> dict:
    """cache ราคา Alpaca ร่วมกันทุก session: {(interval, tickers, key_id): (เวลา, data)}"""
    return {}


KEEP_STALE = 3 * 24 * 3600     # เก็บข้อมูลเก่าไว้ต่อยอดแบบ incremental ได้ 3 วัน
BATCH = 100                    # สแกนสดทีละกี่ตัว (ชุดที่เสร็จแล้วไม่ต้องโหลดซ้ำ)


def _merged(cache: dict, interval: str, key_id: str, max_age: float) -> tuple[set, dict]:
    """รวมข้อมูลจากทุกชุดใน cache (ใหม่สุดชนะ) -> (ชุดหุ้นที่ครอบคลุม, {ticker: df})"""
    now, covered, merged = time.time(), set(), {}
    for (itv, tks, kid), (t0, res) in sorted(cache.items(), key=lambda kv: kv[1][0]):   # เก่า -> ใหม่
        if itv == interval and kid == key_id and now - t0 <= max_age:
            covered |= set(tks)
            merged.update(res)
    return covered, merged


def _alpaca(tickers: tuple, interval: str, key_id: str, secret_key: str, progress=None):
    cache, now = _price_cache(), time.time()
    for k in [k for k, (t0, _) in cache.items() if now - t0 > KEEP_STALE]:
        cache.pop(k, None)                                   # ล้างของเก่ามาก
    want = set(tickers)
    covered, fresh = _merged(cache, interval, key_id, PRICE_TTL)
    if want <= covered:                                      # มีครบและยังสด (รวมจากหลายชุดได้)
        return {t: fresh[t] for t in tickers if t in fresh}
    if interval in ("1h", "1d"):
        todo = [t for t in tickers if t not in covered]      # ดึงเฉพาะตัวที่ยังไม่มีของสด
        _, stale = _merged(cache, interval, key_id, KEEP_STALE)
        res = data.alpaca_update(todo, interval, key_id, secret_key, stale, progress=progress)
        cache[(interval, tuple(todo), key_id)] = (time.time(), res)
        _, fresh = _merged(cache, interval, key_id, PRICE_TTL)
        return {t: fresh[t] for t in tickers if t in fresh}
    d = _alpaca(tickers, "1d", key_id, secret_key, progress)
    f = data.daily_to_weekly if interval == "1wk" else data.daily_to_monthly
    res = {t: f(df) for t, df in d.items()}
    cache[(interval, tickers, key_id)] = (time.time(), res)
    return res


def get_prices(tickers: tuple, interval: str, progress=None):
    if source == ALPACA:
        try:
            return _alpaca(tickers, interval, alp_key, alp_secret, progress)
        except data.AlpacaError as e:
            st.error(f"{e}\n\nตรวจสอบ API Key / Secret ของ Alpaca แล้วลองใหม่ หรือเปลี่ยนไปใช้ Yahoo ชั่วคราว")
            st.stop()
    return _yahoo(tickers, interval)


frames_for = scanner.frames_for


def r2(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 2)


# ---------- sidebar ----------
with st.sidebar:
    st.header("⚙️ เงื่อนไข")
    st.subheader("🔌 แหล่งข้อมูลราคา")
    source = st.radio("ราคาจาก", [ALPACA, YAHOO], horizontal=True, label_visibility="collapsed")
    alp_key, alp_secret = _secret("ALPACA_KEY"), _secret("ALPACA_SECRET")
    if source == ALPACA:
        if alp_key and alp_secret:
            st.caption("🔑 ใช้ API Key จาก Secrets")
        else:
            alp_key = st.text_input("Alpaca API Key", type="password").strip()
            alp_secret = st.text_input("Alpaca Secret Key", type="password").strip()
            st.caption("ใส่ไว้ใน Secrets ของ Streamlit จะได้ไม่ต้องกรอกทุกครั้ง (ดู README)")
    need_keys = source == ALPACA and not (alp_key and alp_secret)

    st.subheader("📊 ดัชนี")
    sel_idx = st.multiselect("หุ้นในดัชนี", data.INDEXES, default=data.INDEXES, label_visibility="collapsed")

    st.subheader("Market cap (พันล้าน USD)")
    c1, c2 = st.columns(2)
    cap_min = c1.number_input("ขั้นต่ำ (B)", 0.0, 10000.0, 10.0, step=1.0)
    cap_max = c2.number_input("สูงสุด (B)", 0.0, 10000.0, 10000.0, step=10.0)
    cap_unknown = st.checkbox("รวมหุ้นใน S&P / NDX / Dow ที่ไม่มีข้อมูล market cap", True,
                              help="หุ้นใน NASDAQ Composite ที่ไม่มี market cap (ส่วนใหญ่เป็นกองทุน/หุ้นจิ๋ว) จะถูกตัดออกเสมอ")

    uni_all = get_universe(tuple(sel_idx)) if sel_idx else pd.DataFrame(columns=["MarketCapB", "Major", "Yahoo"])
    in_range = (uni_all["MarketCapB"] >= cap_min) & (uni_all["MarketCapB"] <= cap_max)
    uni_sel = uni_all[in_range | (uni_all["MarketCapB"].isna() & uni_all["Major"].astype(bool) & cap_unknown)]
    est_min = len(uni_sel) / 494 * (2.5 if source == ALPACA else 3.0)
    st.caption(f"รวม {len(uni_all):,} ตัว → ผ่าน market cap **{len(uni_sel):,} ตัว** "
               f"· สแกนครั้งแรกประมาณ {max(est_min, 0.3):.0f} นาที")
    if len(uni_sel) > 1200:
        st.warning("หุ้นเยอะมาก จะใช้เวลานานและอาจใช้ RAM เกินแผนฟรีของ Streamlit "
                   "แนะนำเพิ่ม market cap ขั้นต่ำ (เช่น 2B ขึ้นไป)")

    st.subheader("Higher Low / Price vs X")
    sel_tfs = st.multiselect("TF ที่ใช้คัด", TFS, default=TFS)
    cond = st.radio("ต้องเป็น ✅", ["Higher Low ✅ + Price vs X ✅", "Higher Low ✅ อย่างเดียว"])
    match_mode = st.radio("ใน", ["อย่างน้อย 1 TF", "ทุก TF ที่เลือก"], horizontal=True)
    fresh_only = st.checkbox("เฉพาะที่เพิ่งเกิด Alert (HL ✅ + X ✅) ภายใน N แท่ง", False)
    fresh = st.number_input("N แท่ง (ของแต่ละ TF)", 0, 100, 3, disabled=not fresh_only)
    confirmed_only = st.checkbox("ใช้เฉพาะแท่งที่ปิดแล้ว (No Repaint)", True)

    with st.expander("Stochastic / State machine (ตรงกับ input ใน Pine)"):
        iK = st.number_input("%K Length", 2, 100, 9)
        iSm = st.number_input("Smooth", 1, 20, 3)
        iOS = st.number_input("Oversold Level", 1.0, 50.0, 20.0)
        iOB = st.number_input("Overbought Level", 50.0, 99.0, 80.0)
        iReset = st.number_input("Max Scan Bars", 5, 500, 80)
        iTrail = st.number_input("Trail Bars after exitOS", 0, 50, 5)
        iLb = st.number_input("SW Low Lookback Bars (fix lag)", 0, 10, 3)
        iTimeout = st.checkbox("Close Cycle if Max Scan Bars Timeout", True)

    st.subheader("📏 MTF EMA89 4H")
    use_ema = st.checkbox("ราคาต้องอยู่เหนือ EMA 4H", True)
    ema_len = st.number_input("EMA Length", 1, 400, 89)
    ema_conf = st.checkbox("ใช้แท่ง 4H ที่ปิดแล้ว (No Repaint)", True,
                           help="เหมือน iEmaConfirmed ใน Pine: เปิด = ema[1] + lookahead_on")

    with st.expander("📐 Fibo Support (ไม่บังคับ)"):
        show_fibo = st.checkbox("แสดงคอลัมน์ Fibo ในผลสแกน", False)
        fibo_src = st.selectbox("Fibo Source", list(FIBO_TF), index=0)
        fib_names = [n for n, _ in FIB_LEVELS] + ["SW Low"]
        fib_use = st.multiselect("ระดับที่นับเป็นแนวรับ", fib_names, default=fib_names)
        fib_filter = st.checkbox("กรองเฉพาะราคาใกล้แนวรับ Fibo", False, disabled=not show_fibo)
        fib_maxd = st.number_input("ห่างจากแนวรับใกล้สุดไม่เกิน (%)", 0.1, 50.0, 3.0, step=0.5)

p = Params(stoch_k=int(iK), stoch_smooth=int(iSm), os_level=float(iOS), ob_level=float(iOB),
           max_scan_bars=int(iReset), trail_bars=int(iTrail), sw_lookback=int(iLb),
           close_on_timeout=bool(iTimeout), max_bars_since_signal=int(fresh), ema_len=int(ema_len))
fib_kw = dict(ios=float(iOS), iob=float(iOB), reset=int(iReset), trail=int(iTrail), lookback=int(iLb))


def latest_price(fr: dict) -> float:
    return scanner.latest_price(fr, confirmed_only)


def fmt_dt(ts, tf: str):
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%d/%m/%Y %H:%M" if tf in ("4H", "1H") else "%d/%m/%Y")


def tick(v) -> str:
    return "—" if v is None else ("✅" if v else "❌")


def tf_ok(s: dict) -> bool:
    """TF นี้เข้าเงื่อนไขตามที่เลือกในแถบซ้ายไหม"""
    if s["hl"] is not True:
        return False
    if cond.startswith("Higher Low ✅ + ") and s["px"] is not True:
        return False
    if fresh_only and not (s["px"] is True and s["bars_since"] is not None and s["bars_since"] <= fresh):
        return False
    return True


def tf_frame(fr: dict, tf: str):
    return scanner.tf_frame(fr, tf, confirmed_only)


def fibo_for(fr: dict, price: float) -> dict:
    tf = FIBO_TF[fibo_src]
    df = fr.get(tf)
    if df is None:
        return fibo_levels(None, fibo_src, price)
    closed = True if confirmed_only else last_bar_closed(df, tf)
    if confirmed_only:
        df = drop_unclosed(df, tf)
    f = fibo_levels(df, fibo_src, price, closed, **fib_kw)
    below = {n: v for n, v in f["levels"].items() if n in fib_use and v <= price}
    if below:
        nm = max(below, key=below.get)
        f.update(support_name=nm, support=below[nm], support_dist=(below[nm] / price - 1) * 100)
    else:
        f.update(support_name=None, support=NAN, support_dist=NAN)
    return f


def tf_rows(sym: str, fr: dict) -> list[dict]:
    """ค่าแบบ Dashboard (W / D / 4H) ของหุ้น 1 ตัว สำหรับเทียบกับ TradingView"""
    price = latest_price(fr)
    e4 = mtf_ema(fr.get("4H"), "4H", p.ema_len, ema_conf)
    rows = []
    for tf in ["W", "D", "4H"]:
        d = tf_frame(fr, tf)
        s = tf_state(d, p)
        rows.append({
            "Symbol": sym, "TF": tf,
            "แท่งล่าสุด": None if d is None else d.index[-1].strftime("%d/%m/%Y %H:%M" if tf == "4H" else "%d/%m/%Y"),
            "Previous SW": r2(s["prev"]), "SW Low": r2(s["sw"]), "Higher Low": tick(s["hl"]),
            "X": r2(s["x"]), "Price vs X": tick(s["px"]),
            "Close (TF)": None if d is None else r2(d["Close"].iloc[-1]),
            "Alert ล่าสุด": fmt_dt(s["alert_date"], tf),
            f"EMA{p.ema_len} 4H": (None if e4 != e4 else f"{'✅' if price > e4 else '❌'} {e4:,.2f}") if tf == "4H" else "",
        })
    return rows


AUTO_DIR = Path(__file__).parent / "results"
AUTO_CSV, AUTO_META = AUTO_DIR / "scan_latest.csv", AUTO_DIR / "meta.json"


@st.cache_data(show_spinner=False)
def load_auto(mtime: float):
    return scanner.load_raw(AUTO_CSV, AUTO_META)


def _nn(v):
    return v is None or bool(pd.isna(v))


def fibo_extra(fr: dict, price: float) -> dict:
    fb = fibo_for(fr, price)
    return {"fibo_label": fb["label"], "fibo_name": fb["support_name"],
            "fibo_support": fb["support"], "fibo_dist": fb["support_dist"]}


def present(raw: pd.DataFrame) -> pd.DataFrame:
    """กรองสถานะดิบตามเงื่อนไขในแถบซ้าย -> ตารางผล"""
    rows = []
    for rec in raw.to_dict("records"):
        price, e4 = rec["Price"], rec["EMA4H"]
        above = (not _nn(e4)) and price > e4
        if use_ema and not above:
            continue
        sts = {tf: {"hl": rec[f"{tf}_hl"], "px": rec[f"{tf}_px"], "x": rec[f"{tf}_x"], "sw": rec[f"{tf}_sw"],
                    "prev": rec[f"{tf}_prev"], "alert_date": rec[f"{tf}_alert"],
                    "bars_since": None if _nn(rec[f"{tf}_bs"]) else int(rec[f"{tf}_bs"])} for tf in TFS}
        hits = [tf for tf in sel_tfs if tf_ok(sts[tf])]
        ok = bool(hits) if match_mode == "อย่างน้อย 1 TF" else len(hits) == len(sel_tfs)
        if not ok:
            continue
        row = {
            "Symbol": rec["Symbol"], "Name": rec["Name"], "Index": rec["Index"], "Sector": rec["Sector"],
            "MktCap (B)": r2(rec["MarketCapB"]), "Price": r2(price),
            f"EMA{p.ema_len} 4H": None if _nn(e4) else f"{'✅' if above else '❌'} {e4:,.2f}",
            "% จาก EMA": None if _nn(e4) else r2((price / e4 - 1) * 100),
            "TF เข้า": len(hits),
        }
        for tf in TFS:
            s_ = sts[tf]
            row[f"{tf} Higher Low"] = tick(s_["hl"])
            row[f"{tf} Price vs X"] = tick(s_["px"])
            row[f"{tf} Alert"] = fmt_dt(s_["alert_date"], tf)
            row[f"{tf} X"] = r2(s_["x"])
            row[f"{tf} % ถึง X"] = None if _nn(s_["x"]) else r2((s_["x"] / price - 1) * 100)
            row[f"{tf} SW Low"] = r2(s_["sw"])
            row[f"{tf} Previous SW"] = r2(s_["prev"])
            row[f"_bs_{tf}"] = s_["bars_since"] if tf in hits else None
        if show_fibo and "fibo_label" in rec:
            d = rec["fibo_dist"]
            if fib_filter and (_nn(d) or -d > fib_maxd):
                continue
            row.update({"Fibo": rec["fibo_label"],
                        "แนวรับใกล้สุด": None if not rec["fibo_name"] else f"{rec['fibo_name']} @ {rec['fibo_support']:,.2f}",
                        "% ถึงแนวรับ": r2(d)})
        row["Chart"] = f"https://www.tradingview.com/chart/?symbol={rec['Symbol'].replace('.', '-')}"
        rows.append(row)
    return pd.DataFrame(rows)


def in_universe(raw: pd.DataFrame) -> pd.DataFrame:
    """ใช้กับผลสแกนอัตโนมัติ: กรองตามดัชนี + market cap ที่เลือกในแถบซ้าย"""
    if raw.empty:
        return raw
    idx_ok = raw["Index"].fillna("").apply(lambda s: any(ix in s for ix in sel_idx))
    cap = raw["MarketCapB"]
    major = raw["Major"] if "Major" in raw.columns else False
    cap_ok = ((cap >= cap_min) & (cap <= cap_max)) | (cap.isna() & major & cap_unknown)
    return raw[idx_ok & cap_ok]


DEFAULT_P = Params()
custom_params = any(getattr(p, f) != getattr(DEFAULT_P, f) for f in
                    ("stoch_k", "stoch_smooth", "os_level", "ob_level", "max_scan_bars", "trail_bars",
                     "sw_lookback", "close_on_timeout", "ema_len"))

tab_scan, tab_check, tab_cmp = st.tabs(["🔍 สแกนหุ้น", "🧪 ตรวจสอบรายตัว", "📋 เทียบกับ TradingView"])

# =====================================================================
with tab_scan:
    auto_ok = AUTO_CSV.exists() and AUTO_META.exists()
    AUTO, LIVE = "📦 ผลสแกนอัตโนมัติล่าสุด (เปิดดูได้ทันที)", "🔍 สแกนสดตอนนี้"
    mode_sel = st.radio("ที่มาของผล", [AUTO, LIVE], index=0 if auto_ok else 1, horizontal=True,
                        label_visibility="collapsed")
    raw_view, meta_view = None, None

    if mode_sel == AUTO:
        if not auto_ok:
            st.info("ยังไม่มีผลสแกนอัตโนมัติ — ตั้งค่า GitHub Actions ตาม README (ขั้นที่ 5) "
                    "หรือเลือก **สแกนสดตอนนี้**")
        else:
            raw_all, meta_auto = load_auto(AUTO_CSV.stat().st_mtime)
            raw_view = in_universe(raw_all)
            when = pd.Timestamp(meta_auto["scanned_at"]).tz_convert("Asia/Bangkok")
            age_h = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(meta_auto["scanned_at"])).total_seconds() / 3600
            st.caption(f"📦 สแกนอัตโนมัติเมื่อ **{when:%d %b %Y %H:%M}** (เวลาไทย, {age_h:.0f} ชม.ที่แล้ว) · "
                       f"แท่ง 4H ล่าสุด {meta_auto.get('last_4h_bar', '—')} (เวลา NY) · "
                       f"หุ้นในไฟล์ {meta_auto.get('n_symbols', len(raw_all)):,} ตัว "
                       f"({meta_auto.get('universe', '')}) → ตรงดัชนี/market cap ที่เลือก {len(raw_view):,} ตัว")
            if custom_params or not confirmed_only or not ema_conf:
                st.warning("ผลอัตโนมัติคำนวณด้วยค่า Pine ตั้งต้น + แท่งที่ปิดแล้ว "
                           "ถ้าเปลี่ยนค่า Stochastic / EMA / No Repaint ให้ใช้ **สแกนสดตอนนี้**")
            if show_fibo:
                st.caption("Fibo ใช้ได้เฉพาะ **สแกนสดตอนนี้**")
            meta_view = {"n": len(raw_view), "when": when, "src": "สแกนอัตโนมัติ (Alpaca)",
                         "load_rep": {}, "idx": list(sel_idx)}
    else:
        if need_keys:
            st.warning("ใส่ Alpaca API Key / Secret ที่แถบซ้ายก่อน (หรือเลือก Yahoo Finance)")
        if not sel_idx:
            st.warning("เลือกดัชนีอย่างน้อย 1 ตัวที่แถบซ้าย")
        run = st.button(f"🔍 สแกน {len(uni_sel):,} ตัว", type="primary",
                        disabled=not sel_tfs or need_keys or uni_sel.empty)
        st.caption("⏱️ สแกนสดครั้งแรกใช้ราว 3 นาที (ติดลิมิต Alpaca แผนฟรี) · เปิดหน้านี้ค้างไว้ อย่าให้จอมือถือดับ · "
                   "ถ้าสแกนหยุดกลางทาง กดสแกนใหม่ จะทำต่อจากชุดที่โหลดเสร็จแล้ว")
        if run:
            with st.status("กำลังสแกน...", expanded=True) as status:
                uni = uni_sel
                tickers = tuple(uni["Yahoo"])
                st.write(f"ดัชนี: {', '.join(sel_idx)} · ผ่านเงื่อนไข market cap: {len(tickers):,} ตัว")
                need = {"1h", "1d", "1wk"}
                if show_fibo and FIBO_TF[fibo_src] == "M":
                    need.add("1mo")
                store, load_rep = {}, {}
                for itv in ["1h", "1d", "1wk", "1mo"]:
                    if itv in need:
                        t0 = time.time()
                        label = f"โหลดราคา {itv} จาก {source}"
                        pb = st.progress(0.0, text=label)

                        def _prog(done, total, pb=pb, label=label, t0=t0):
                            el = time.time() - t0
                            eta = el / done * (total - done)
                            pb.progress(done / total, text=f"{label} · {done}/{total} กลุ่ม · "
                                                           f"ผ่านไป {el:.0f} วิ · เหลือราว {eta:.0f} วิ")

                        if source == ALPACA and itv in ("1h", "1d"):
                            # ทีละ 100 ตัว: แต่ละชุดเก็บใน cache ทันที ถ้าหยุดกลางทาง กดสแกนใหม่จะทำต่อจากเดิม
                            batches = [tickers[i:i + BATCH] for i in range(0, len(tickers), BATCH)]
                            store[itv] = {}
                            for bi, bt in enumerate(batches):
                                def _bprog(done, total, bi=bi, nb=len(batches), pb=pb, label=label, t0=t0):
                                    frac = (bi + done / total) / nb
                                    el = time.time() - t0
                                    eta = el / frac * (1 - frac) if frac > 0 else 0
                                    pb.progress(min(frac, 1.0), text=f"{label} · ชุด {bi + 1}/{nb} · "
                                                                     f"ผ่านไป {el:.0f} วิ · เหลือราว {eta:.0f} วิ")
                                store[itv].update(get_prices(bt, itv, _bprog))
                                _bprog(1, 1)
                        else:
                            store[itv] = get_prices(tickers, itv, _prog)
                        pb.progress(1.0, text=f"{label} · เสร็จใน {time.time() - t0:.0f} วิ")
                        load_rep[itv] = (len(store[itv]), sorted(set(tickers) - set(store[itv])))
                        st.write(f"→ ได้ {load_rep[itv][0]}/{len(tickers)} ตัว")
                st.write("รัน Higher Low บน 4H / D / W + EMA89 4H")
                raw = scanner.raw_states(uni, store, p, confirmed_only, ema_conf,
                                         extra=fibo_extra if show_fibo else None)
                st.session_state["raw_live"] = raw
                st.session_state["meta_live"] = {"n": len(tickers), "when": pd.Timestamp.now(tz="Asia/Bangkok"),
                                                 "src": source, "load_rep": load_rep, "idx": list(sel_idx)}
                status.update(label="สแกนเสร็จ", state="complete", expanded=False)
        if "raw_live" in st.session_state:
            raw_view, meta_view = st.session_state["raw_live"], st.session_state["meta_live"]
            st.caption("💡 เปลี่ยนเงื่อนไข Higher Low / EMA / Sector ทางซ้ายได้เลย ตารางอัปเดตทันทีโดยไม่ต้องสแกนใหม่")
        else:
            st.info("เลือกดัชนีและตั้งเงื่อนไขทางซ้าย แล้วกดปุ่ม **สแกน**")

    if raw_view is not None:
        res = present(raw_view)
        m = st.columns(5)
        m[0].metric("สแกนทั้งหมด", f"{meta_view['n']:,}")
        m[1].metric("เข้าเงื่อนไข", len(res))
        for col, tf in zip(m[2:], TFS):
            both = ((res[f"{tf} Higher Low"] == "✅") & (res[f"{tf} Price vs X"] == "✅")).sum() if not res.empty else 0
            col.metric(f"{tf}  HL ✅ X ✅", int(both))
        st.caption(f"ดัชนี: {', '.join(meta_view['idx'])} · TF: {', '.join(sel_tfs)} ({match_mode}) · "
                   f"ราคาจาก {meta_view['src']} · ข้อมูลเมื่อ {meta_view['when']:%d %b %Y %H:%M} (เวลาไทย)")
        missing = {itv: miss for itv, (_, miss) in meta_view["load_rep"].items() if miss}
        if missing:
            with st.expander("⚠️ ข้อมูลไม่ครบ: " + " · ".join(f"{itv} ขาด {len(m_)} ตัว" for itv, m_ in missing.items())):
                for itv, m_ in missing.items():
                    st.write(f"**{itv}**: {', '.join(m_)}")
        if res.empty:
            st.warning("ไม่มีหุ้นเข้าเงื่อนไข")
        else:
            f1, f2 = st.columns([3, 1])
            secs = sorted(res["Sector"].astype(str).unique())
            pick = f1.multiselect("กรอง Sector", secs, default=secs)
            detail = f2.toggle("แสดงรายละเอียด X / SW Low", False)
            view = res[res["Sector"].astype(str).isin(pick)].copy()
            bs = [c for c in view.columns if c.startswith("_bs_")]
            view["_s"] = view[bs].min(axis=1, skipna=True)
            view = view.sort_values(["TF เข้า", "_s"], ascending=[False, True], na_position="last")
            view = view.drop(columns=bs + ["_s"])
            keep = ("Higher Low", "Price vs X", "Alert")
            if not detail:
                view = view.drop(columns=[c for c in view.columns
                                          if any(c.startswith(f"{tf} ") for tf in TFS) and not c.endswith(keep)])
            st.dataframe(view, hide_index=True, width="stretch",
                         column_config={"Chart": st.column_config.LinkColumn("Chart", display_text="TradingView")})
            st.download_button("⬇️ ดาวน์โหลด CSV",
                               res.drop(columns=[c for c in res.columns if c.startswith("_bs_")])
                               .to_csv(index=False).encode("utf-8-sig"),
                               "scan_result.csv", "text/csv")

# =====================================================================
with tab_check:
    st.markdown("กรอก symbol แล้วเทียบกับ Dashboard (W / D / 4H) และ label บน TradingView")
    cc1, cc2, cc3 = st.columns([2, 1, 1])
    sym = cc1.text_input("Symbol", "NVDA").strip().upper()
    ctf = cc2.selectbox("TF ของกราฟ", ["4H", "D", "W"], key="ctf")
    go_btn = cc3.button("ตรวจสอบ", type="primary", disabled=need_keys)
    if go_btn and sym:
        y = sym.replace(".", "-")
        itvs = ["1h", "1d", "1wk"] + (["1mo"] if FIBO_TF[fibo_src] == "M" else [])
        fr = frames_for(y, {itv: get_prices((y,), itv) for itv in itvs})
        if tf_frame(fr, ctf) is None:
            st.error("โหลดข้อมูลไม่ได้ ตรวจสอบ symbol")
        else:
            price = latest_price(fr)
            e4 = mtf_ema(fr.get("4H"), "4H", p.ema_len, ema_conf)
            st.subheader(f"Dashboard  ·  Price {price:,.2f}  ·  EMA{p.ema_len} 4H "
                         + ("—" if e4 != e4 else f"{'✅' if price > e4 else '❌'} {e4:,.2f}"))
            dash, engines = [], {}
            for tf in ["W", "D", "4H"]:
                d = tf_frame(fr, tf)
                if d is None:
                    continue
                rr_ = hl_engine(d, p)
                engines[tf] = (d, rr_)
                last = rr_.iloc[-1]
                s = tf_state(d, p)
                dash.append({
                    "TF": tf, "Previous SW": r2(s["prev"]), "SW Low": r2(s["sw"]),
                    "Higher Low": tick(s["hl"]), "X": r2(s["x"]), "Price vs X": tick(s["px"]),
                    "Close (TF)": r2(last["Close"]), "Alert ล่าสุด": fmt_dt(s["alert_date"], tf),
                    "%K": r2(last["k"]), "Oversold": "🔴" if last["isOS"] else "",
                })
            st.dataframe(pd.DataFrame(dash), hide_index=True, width="stretch")

            if show_fibo:
                fb = fibo_for(fr, price)
                st.subheader(f"📐 Fibo Sup [{fb['label']}]")
                if fb["levels"]:
                    lv = [("🔺 SW High (0%)", fb["sw_high"])] + [(f"📊 {nm}", fb["levels"][nm]) for nm, _ in FIB_LEVELS] \
                         + [("🔻 SW Low (100%)", fb["sw_low"])]
                    st.dataframe(pd.DataFrame([{"Level": nm, "Price": r2(v),
                                                "Dist%": f"{(v - price) / price * 100:+.2f}%",
                                                "Status": "✅ Above" if price > v else "🔴 Below"} for nm, v in lv]),
                                 hide_index=True)
                else:
                    st.info("ยังไม่มีชุด Fibo")

            df, r = engines[ctf]
            ev = r[r["alert"]].copy()
            ev["SW Low date"] = [fmt_dt(df.index[int(b)], ctf) if b >= 0 else None for b in ev["sw_bar"]]
            st.subheader(f"ประวัติ Alert {ctf} 'Higher Low ✅ + Price > X ✅' ({len(ev)} ครั้ง)")
            tbl = ev[["Close", "prev_sw", "sw", "SW Low date", "x_live"]].rename(
                columns={"prev_sw": "Previous SW", "sw": "SW Low", "x_live": "X"})
            num = ["Close", "Previous SW", "SW Low", "X"]
            tbl[num] = tbl[num].round(2)
            tbl = tbl.sort_index(ascending=False).head(30)
            tbl.insert(0, "Alert เมื่อ", [fmt_dt(t, ctf) for t in tbl.index])
            st.dataframe(tbl, hide_index=True, width="stretch")

            show = df.tail(250)
            rr = r.loc[show.index]
            fig = go.Figure()
            fig.add_candlestick(x=show.index, open=show["Open"], high=show["High"], low=show["Low"],
                                close=show["Close"], name=sym)
            fig.add_scatter(x=show.index, y=rr["x_live"], mode="lines", line=dict(shape="hv", width=1.5),
                            name="X (xLive)")
            fig.add_scatter(x=show.index, y=rr["sw"], mode="lines", line=dict(shape="hv", width=1, dash="dot"),
                            name="SW Low")
            if ctf == "4H":
                fig.add_scatter(x=show.index, y=ema(df["Close"], p.ema_len).loc[show.index], mode="lines",
                                line=dict(width=2), name=f"EMA{p.ema_len} 4H")
            elif e4 == e4:
                fig.add_hline(y=e4, line=dict(width=2, dash="dash"),
                              annotation_text=f"4H EMA{p.ema_len} {e4:,.2f}", annotation_position="right")
            a = rr[rr["alert"]]
            fig.add_scatter(x=a.index, y=show.loc[a.index, "Low"] * 0.985, mode="markers",
                            marker=dict(symbol="triangle-up", size=12), name="Alert")
            fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                              legend=dict(orientation="h"))
            if ctf == "4H":
                fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[16, 9.5], pattern="hour")])
            elif ctf == "D":
                fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
            st.plotly_chart(fig, width="stretch")

# =====================================================================
with tab_cmp:
    st.markdown(
        "กรอกได้สูงสุด 10 symbol แอปจะออกตารางค่า **W / D / 4H** แบบเดียวกับ Dashboard บน TradingView "
        "ให้เปิดกราฟแต่ละตัวแล้วเทียบทีละแถว\n\n"
        "- เกณฑ์แนะนำ: ติ๊ก ✅/❌ ตรงกันอย่างน้อย 9/10 ตัว และค่า X / SW Low ต่างไม่เกินราว 0.1%\n"
        "- ดาวน์โหลด CSV แล้วกรอกค่าจาก TradingView ลงคอลัมน์ `TV ...` ได้ ถ้าเจอตัวไม่ตรงส่งไฟล์นั้นมาให้ไล่แก้")
    raw_syms = st.text_area("Symbols (คั่นด้วยจุลภาค, เว้นวรรค หรือขึ้นบรรทัดใหม่)",
                            "NVDA, AMD, AVGO, MU, AMAT, LRCX, KLAC, QCOM, TXN, ADI")
    syms = []
    for s in raw_syms.replace(",", " ").split():
        s = s.strip().upper()
        if s and s not in syms:
            syms.append(s)
    if len(syms) > 10:
        st.warning(f"ใช้ 10 ตัวแรก (กรอกมา {len(syms)} ตัว)")
        syms = syms[:10]
    cmp_btn = st.button("📋 สร้างตารางเทียบ", type="primary", disabled=not syms or need_keys)
    if cmp_btn:
        ys = tuple(s.replace(".", "-") for s in syms)
        with st.spinner(f"โหลดราคา {len(ys)} ตัวจาก {source}..."):
            store = {itv: get_prices(ys, itv) for itv in ("1h", "1d", "1wk")}
        rows, bad = [], []
        for sym, y in zip(syms, ys):
            fr = frames_for(y, store)
            if not fr:
                bad.append(sym)
                continue
            rows += tf_rows(sym, fr)
        if bad:
            st.warning(f"โหลดข้อมูลไม่ได้: {', '.join(bad)}")
        if rows:
            cmp = pd.DataFrame(rows)
            st.session_state["cmp"] = cmp
            st.session_state["cmp_meta"] = (source, pd.Timestamp.now(tz="Asia/Bangkok"))
    if "cmp" in st.session_state:
        cmp = st.session_state["cmp"]
        src_used, when = st.session_state["cmp_meta"]
        st.caption(f"ราคาจาก {src_used} · สร้างเมื่อ {when:%d %b %Y %H:%M} (เวลาไทย) · "
                   + ("ใช้เฉพาะแท่งที่ปิดแล้ว" if confirmed_only else "รวมแท่งที่ยังไม่ปิด"))
        st.dataframe(cmp, hide_index=True, width="stretch", height=min(40 + 35 * len(cmp), 1100))
        out = cmp.copy()
        for c in ["TV Previous SW", "TV SW Low", "TV Higher Low", "TV X", "TV Price vs X", "ตรงไหม (✓/✗)"]:
            out[c] = ""
        st.download_button("⬇️ ดาวน์โหลด CSV (มีคอลัมน์ว่างไว้กรอกค่า TradingView)",
                           out.to_csv(index=False).encode("utf-8-sig"), "compare_tradingview.csv", "text/csv")
