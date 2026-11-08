"""
US Stock Scanner (S&P 500 / NASDAQ 100 / Dow 30 / NASDAQ Composite)
  หัว Fibo ขาขึ้น / ขาลง (Auto) บน 4H / 1H  +  MTF EMA89 4H
ลอจิกแปลงจาก Pine "Reversal Candle [Stoch Oversold]":
  - f_htfTriggerLowM + f_headLatchLow   -> heads.py  (หัว Fibo ขาขึ้น)
  - f_htfTriggerHigh + f_headLatchHigh  -> heads.py  (หัว Fibo ขาลง)
  - MTF EMA89 4H (ema[1] + lookahead_on) -> fibo.mtf_ema
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
from fibo import mtf_ema
from heads import HeadParams, head_latch_high, head_latch_low, trigger_high, trigger_low_m
from logic import ema

st.set_page_config(page_title="US Stock Fibo Head Scanner", page_icon="📈", layout="wide")
st.title("📈 US Stock Scanner — หัว Fibo ขาขึ้น / ขาลง (4H / 1H) + EMA89 4H")
st.caption("ลอจิกจาก Reversal Candle [Stoch Oversold] (Combined Mode: หัว Fibo Auto) + MTF EMA89 4H · "
           "ราคาจาก Alpaca (SIP ดีเลย์ 15 นาที) หรือ Yahoo Finance · ใช้ประกอบการตัดสินใจเท่านั้น "
           "ยืนยันกราฟบน TradingView ก่อนเข้า order")

TFS = scanner.TFS
SRC = {"1h": "730d"}
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


@st.cache_resource
def _last_live() -> dict:
    """ผลสแกนสดล่าสุด เก็บในตัวแอป -> รีเฟรชหน้าเว็บแล้วไม่หาย (หายเมื่อแอปรีสตาร์ท)"""
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
    est_min = len(uni_sel) / 617 * 2.0
    st.caption(f"รวม {len(uni_all):,} ตัว → ผ่าน market cap **{len(uni_sel):,} ตัว** "
               f"· สแกนสดครั้งแรกประมาณ {max(est_min, 0.3):.0f} นาที")
    if len(uni_sel) > 1200:
        st.warning("หุ้นเยอะมาก จะใช้เวลานานและอาจใช้ RAM เกินแผนฟรีของ Streamlit "
                   "แนะนำเพิ่ม market cap ขั้นต่ำ (เช่น 2B ขึ้นไป)")

    st.subheader("📏 MTF EMA89 4H")
    use_ema = st.checkbox("ราคาต้องอยู่เหนือ EMA 4H", True)
    ema_len = st.number_input("EMA Length", 1, 400, 89)
    ema_conf = st.checkbox("ใช้แท่ง 4H ที่ปิดแล้ว (No Repaint)", True,
                           help="เหมือน iEmaConfirmed ใน Pine: เปิด = ema[1] + lookahead_on")

    st.subheader("📐 หัว Fibo (Auto)")
    up_req = st.multiselect("ต้องมีหัว Fibo ขาขึ้นใน", TFS, default=[],
                            help="ว่าง = ไม่กรอง (แสดงทุกตัวที่ผ่าน EMA)")
    up_mode = st.radio("เงื่อนไข", ["อย่างน้อย 1 TF", "ทุก TF ที่เลือก"], horizontal=True,
                       disabled=len(up_req) < 2)
    dn_mode = st.radio("กลุ่มที่มีหัว Fibo ขาลง (4H หรือ 1H)",
                       ["แยกเป็นตารางของตัวเอง", "ซ่อน", "แสดงรวมในตารางเดียว"])
    confirmed_only = st.checkbox("ใช้เฉพาะแท่งที่ปิดแล้ว (No Repaint)", True)

    with st.expander("Stochastic / State machine (ตรงกับ input ใน Pine)"):
        iK = st.number_input("%K Length", 2, 100, 9)
        iSm = st.number_input("Smooth", 1, 20, 3)
        iOS = st.number_input("Oversold Level", 1.0, 50.0, 20.0)
        iOB = st.number_input("Overbought Level", 50.0, 99.0, 80.0)
        st.caption("📈 หัว Fibo ขาขึ้น (กลุ่ม Stochastic)")
        iReset = st.number_input("Max Scan Bars", 5, 500, 80)
        iTrail = st.number_input("Trail Bars after exitOS", 0, 50, 5)
        iLb = st.number_input("SW Low Lookback Bars (fix lag)", 0, 10, 3)
        iTimeout = st.checkbox("Close Cycle if Max Scan Bars Timeout", True)
        st.caption("📉 หัว Fibo ขาลง (กลุ่ม Lower High Reversal)")
        iResetH = st.number_input("Max Scan Bars ", 5, 500, 80)
        iTrailH = st.number_input("Trail Bars after exitOB", 0, 50, 5)
        iLbH = st.number_input("SW High Lookback Bars (fix lag)", 0, 10, 3)
        iTimeoutH = st.checkbox("Close Cycle if Max Scan Bars Timeout ", True)

hp = HeadParams(k_len=int(iK), smooth=int(iSm), os_level=float(iOS), ob_level=float(iOB),
                reset_low=int(iReset), trail_low=int(iTrail), lookback_low=int(iLb), timeout_low=bool(iTimeout),
                reset_high=int(iResetH), trail_high=int(iTrailH), lookback_high=int(iLbH),
                timeout_high=bool(iTimeoutH))
EMA_COL = f"EMA{int(ema_len)} 4H"
UP_COLS = {tf: f"หัว Fibo ขาขึ้น {tf}" for tf in TFS}
DN_COLS = {tf: f"หัว Fibo ขาลง {tf}" for tf in TFS}


def _nn(v):
    return v is None or bool(pd.isna(v))


def latest_price(fr: dict) -> float:
    return scanner.latest_price(fr, confirmed_only)


def tf_frame(fr: dict, tf: str):
    return scanner.tf_frame(fr, tf, confirmed_only)


IDX_SHORT = {"S&P 500": "SPX", "NASDAQ 100": "NDX", "Dow Jones 30": "DJI", "NASDAQ Composite": "IXIC"}


def short_idx(s) -> str:
    """ย่อชื่อดัชนีให้ตารางแคบลง (SPX · NDX · DJI · IXIC)"""
    if _nn(s):
        return ""
    out = str(s)
    for k, v in IDX_SHORT.items():
        out = out.replace(k, v)
    return out.replace(", ", " · ")


def hv(v) -> str:
    """ค่าหัว Fibo แบบเดียวกับ Dashboard บน TradingView ("#.####" หรือ "-")"""
    return "-" if _nn(v) else f"{float(v):.4f}".rstrip("0").rstrip(".")


def head_cell(v, show_val: bool) -> str:
    if _nn(v):
        return "-"
    return f"✅ {v:,.2f}" if show_val else "✅"


def stat_text(stats) -> str:
    """สถานะการดึงข้อมูลบนแถบความคืบหน้า"""
    if not stats or not stats.get("req"):
        return ""
    t = f" · คำขอ {stats['req']} (เฉลี่ย {stats['sec'] / stats['req']:.1f} วิ/ครั้ง)"
    if stats["r429"]:
        t += f" · ⚠️ โดน Alpaca จำกัด {stats['r429']} ครั้ง (มีการสแกนอื่นใช้ Key เดียวกันอยู่หรือเปล่า?)"
    if stats["timeouts"] or stats["errors"]:
        t += f" · หมดเวลา {stats['timeouts']} · ผิดพลาด {stats['errors']}"
    return t


AUTO_DIR = Path(__file__).parent / "results"
AUTO_CSV, AUTO_META = AUTO_DIR / "scan_latest.csv", AUTO_DIR / "meta.json"


@st.cache_data(show_spinner=False)
def load_auto(mtime: float):
    return scanner.load_raw(AUTO_CSV, AUTO_META)


def present(raw: pd.DataFrame, show_val: bool) -> pd.DataFrame:
    """กรองสถานะดิบตามเงื่อนไขในแถบซ้าย -> ตารางผล (คอลัมน์ตามที่กำหนด) + คอลัมน์ช่วยจัดกลุ่ม"""
    rows = []
    for rec in raw.to_dict("records"):
        price, e4 = rec["Price"], rec["EMA4H"]
        above = (not _nn(e4)) and price > e4
        if use_ema and not above:
            continue
        ups = {tf: rec[f"{tf}_up"] for tf in TFS}
        dns = {tf: rec[f"{tf}_dn"] for tf in TFS}
        if up_req:
            hit = [tf for tf in up_req if not _nn(ups[tf])]
            if (up_mode == "ทุก TF ที่เลือก" and len(hit) < len(up_req)) or not hit:
                continue
        row = {
            "Symbol": rec["Symbol"], "Name": rec["Name"], "Index": short_idx(rec["Index"]), "Sector": rec["Sector"],
            "MktCap (B)": r2(rec["MarketCapB"]), "Price": r2(price),
            EMA_COL: None if _nn(e4) else f"{'✅' if above else '❌'} {e4:,.2f}",
            f"% จาก {EMA_COL}": None if _nn(e4) else r2((price / e4 - 1) * 100),
        }
        for tf in TFS:
            row[UP_COLS[tf]] = head_cell(ups[tf], show_val)
        for tf in TFS:
            row[DN_COLS[tf]] = head_cell(dns[tf], show_val)
        row["Chart"] = f"https://www.tradingview.com/chart/?symbol={rec['Symbol'].replace('.', '-')}"
        row["_n_up"] = sum(not _nn(v) for v in ups.values())
        row["_has_dn"] = any(not _nn(v) for v in dns.values())
        row["_clear_at"] = rec.get("dn_clear_at")
        row["_prev4"], row["_prev1"] = rec.get("dn_prev_4H"), rec.get("dn_prev_1H")
        row["_os4"], row["_os1"] = rec.get("dn_os_4H"), rec.get("dn_os_1H")
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


def visible(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=[c for c in df.columns if c.startswith("_")])


def show_table(df: pd.DataFrame, key: str):
    st.dataframe(visible(df), hide_index=True, width="stretch", key=key,
                 column_config={"Chart": st.column_config.LinkColumn("Chart", display_text="TradingView"),
                                "Name": st.column_config.TextColumn("Name", width="medium"),
                                "Index": st.column_config.TextColumn("Index", width="small",
                                                                     help="SPX = S&P 500 · NDX = NASDAQ 100 · "
                                                                          "DJI = Dow Jones 30 · IXIC = NASDAQ Composite")})


custom_params = (hp != HeadParams()) or int(ema_len) != 89

tab_scan, tab_watch, tab_check, tab_cmp = st.tabs(
    ["🔍 สแกนหุ้น", "⭐ Watchlist", "🧪 ตรวจสอบรายตัว", "📋 เทียบกับ TradingView"])

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
            if not scanner.is_current_format(raw_all):
                st.info("ผลสแกนอัตโนมัติในไฟล์ยังเป็นรูปแบบเก่า (Higher Low W/D/4H) — "
                        "ไปที่ GitHub แท็บ **Actions → Auto scan → Run workflow** เพื่อสแกนใหม่ด้วยหัว Fibo "
                        "หรือเลือก **สแกนสดตอนนี้**")
            else:
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
                meta_view = {"n": len(raw_view), "when": when, "src": "สแกนอัตโนมัติ (Alpaca)",
                             "load_rep": {}, "idx": list(sel_idx)}
    else:
        if need_keys:
            st.warning("ใส่ Alpaca API Key / Secret ที่แถบซ้ายก่อน (หรือเลือก Yahoo Finance)")
        if not sel_idx:
            st.warning("เลือกดัชนีอย่างน้อย 1 ตัวที่แถบซ้าย")
        run = st.button(f"🔍 สแกน {len(uni_sel):,} ตัว", type="primary", disabled=need_keys or uni_sel.empty)
        st.caption("⏱️ สแกนสดครั้งแรกใช้ราว 2 นาที (ติดลิมิต Alpaca แผนฟรี) · เปิดหน้านี้ค้างไว้ อย่าให้จอมือถือดับ · "
                   "ถ้าสแกนหยุดกลางทาง กดสแกนใหม่ จะทำต่อจากชุดที่โหลดเสร็จแล้ว")
        if run:
            with st.status("กำลังสแกน...", expanded=True) as status:
                uni = uni_sel
                tickers = tuple(uni["Yahoo"])
                st.write(f"ดัชนี: {', '.join(sel_idx)} · ผ่านเงื่อนไข market cap: {len(tickers):,} ตัว")
                store, load_rep = {}, {}
                itv = "1h"
                t0 = time.time()
                label = f"โหลดราคา 1H (สร้าง 4H จาก 1H) จาก {source}"
                pb = st.progress(0.0, text=label)

                def _prog(done, total, pb=pb, label=label, t0=t0, stats=None):
                    el = time.time() - t0
                    eta = el / done * (total - done) if done else 0
                    pb.progress(done / total, text=f"{label} · {done}/{total} กลุ่ม · "
                                                   f"ผ่านไป {el:.0f} วิ · เหลือราว {eta:.0f} วิ{stat_text(stats)}")

                if source == ALPACA:
                    # ทีละ 100 ตัว: แต่ละชุดเก็บใน cache ทันที ถ้าหยุดกลางทาง กดสแกนใหม่จะทำต่อจากเดิม
                    batches = [tickers[i:i + BATCH] for i in range(0, len(tickers), BATCH)]
                    store[itv] = {}
                    for bi, bt in enumerate(batches):
                        def _bprog(done, total, bi=bi, nb=len(batches), pb=pb, label=label, t0=t0, stats=None):
                            frac = (bi + done / total) / nb
                            el = time.time() - t0
                            eta = el / frac * (1 - frac) if frac > 0 else 0
                            pb.progress(min(frac, 1.0), text=f"{label} · ชุด {bi + 1}/{nb} · "
                                                             f"ผ่านไป {el:.0f} วิ · เหลือราว {eta:.0f} วิ"
                                                             f"{stat_text(stats)}")
                        store[itv].update(get_prices(bt, itv, _bprog))
                        _bprog(1, 1)
                else:
                    store[itv] = get_prices(tickers, itv, _prog)
                pb.progress(1.0, text=f"{label} · เสร็จใน {time.time() - t0:.0f} วิ")
                load_rep[itv] = (len(store[itv]), sorted(set(tickers) - set(store[itv])))
                st.write(f"→ ได้ {load_rep[itv][0]}/{len(tickers)} ตัว")
                st.write("คำนวณหัว Fibo ขาขึ้น / ขาลง (4H / 1H) + EMA89 4H")
                raw = scanner.raw_states(uni, store, hp, int(ema_len), confirmed_only, ema_conf)
                st.session_state["raw_live"] = raw
                st.session_state["meta_live"] = {"n": len(tickers), "when": pd.Timestamp.now(tz="Asia/Bangkok"),
                                                 "src": source, "load_rep": load_rep, "idx": list(sel_idx)}
                _last_live().update(raw=raw, meta=st.session_state["meta_live"])
                status.update(label="สแกนเสร็จ", state="complete", expanded=False)
        if "raw_live" not in st.session_state and _last_live().get("raw") is not None:
            st.session_state["raw_live"] = _last_live()["raw"]              # กลับมาหลังรีเฟรชหน้าเว็บ
            st.session_state["meta_live"] = _last_live()["meta"]
        if "raw_live" in st.session_state and scanner.is_current_format(st.session_state["raw_live"]):
            raw_view, meta_view = st.session_state["raw_live"], st.session_state["meta_live"]
            st.caption("💡 เปลี่ยนเงื่อนไข EMA / หัว Fibo / Sector ทางซ้ายได้เลย ตารางอัปเดตทันทีโดยไม่ต้องสแกนใหม่")
        else:
            st.info("เลือกดัชนีและตั้งเงื่อนไขทางซ้าย แล้วกดปุ่ม **สแกน**")

    if raw_view is not None:
        f1, f2 = st.columns([3, 1])
        show_val = f2.toggle("แสดงราคาหัว Fibo", False)
        res = present(raw_view, show_val)
        if not res.empty:
            secs = sorted(res["Sector"].astype(str).unique())
            pick = f1.multiselect("กรอง Sector", secs, default=secs)
            res = res[res["Sector"].astype(str).isin(pick)]
        up_any = res["_n_up"].gt(0) if not res.empty else pd.Series(dtype=bool)
        has_dn = res["_has_dn"] if not res.empty else pd.Series(dtype=bool)
        m = st.columns(5)
        m[0].metric("สแกนทั้งหมด", f"{meta_view['n']:,}")
        m[1].metric("ผ่านเงื่อนไข", len(res))
        m[2].metric("📈 หัวขาขึ้น 4H", int((res[UP_COLS['4H']] != "-").sum()) if not res.empty else 0)
        m[3].metric("📈 หัวขาขึ้น 1H", int((res[UP_COLS['1H']] != "-").sum()) if not res.empty else 0)
        m[4].metric("📉 มีหัวขาลง", int(has_dn.sum()) if not res.empty else 0)
        st.caption(f"ดัชนี: {', '.join(meta_view['idx'])} · "
                   f"{'ราคา > ' + EMA_COL if use_ema else 'ไม่กรอง EMA'} · "
                   f"ราคาจาก {meta_view['src']} · ข้อมูลเมื่อ {meta_view['when']:%d %b %Y %H:%M} (เวลาไทย)")
        missing = {itv: miss for itv, (_, miss) in meta_view["load_rep"].items() if miss}
        if missing:
            with st.expander("⚠️ ข้อมูลไม่ครบ: " + " · ".join(f"{itv} ขาด {len(m_)} ตัว" for itv, m_ in missing.items())):
                for itv, m_ in missing.items():
                    st.write(f"**{itv}**: {', '.join(m_)}")
        if res.empty:
            st.warning("ไม่มีหุ้นเข้าเงื่อนไข")
        else:
            res = res.sort_values(["_n_up", f"% จาก {EMA_COL}"], ascending=[False, True], na_position="last")
            if dn_mode == "แสดงรวมในตารางเดียว":
                show_table(res, "t_all")
            else:
                clean, dn = res[~res["_has_dn"]], res[res["_has_dn"]]
                st.markdown(f"#### 🟢 ไม่มีหัว Fibo ขาลง — {len(clean):,} ตัว")
                if clean.empty:
                    st.caption("ไม่มี")
                else:
                    show_table(clean, "t_clean")
                if dn_mode == "แยกเป็นตารางของตัวเอง":
                    st.markdown(f"#### 🔴 มีหัว Fibo ขาลง (4H หรือ 1H) — {len(dn):,} ตัว")
                    if dn.empty:
                        st.caption("ไม่มี")
                    else:
                        show_table(dn, "t_dn")
                else:
                    st.caption(f"ซ่อนหุ้นที่มีหัว Fibo ขาลง {len(dn):,} ตัว")
            out = res.copy()
            out.insert(0, "กลุ่ม", np.where(out["_has_dn"], "มีหัว Fibo ขาลง", "ไม่มีหัว Fibo ขาลง"))
            st.download_button("⬇️ ดาวน์โหลด CSV", visible(out)
                               .to_csv(index=False).encode("utf-8-sig"), "scan_result.csv", "text/csv")


def ago(t: pd.Timestamp, now: pd.Timestamp) -> str:
    sec = max((now - t).total_seconds(), 0)
    d, h = int(sec // 86400), int(sec % 86400 // 3600)
    return f"{d} วัน {h} ชม." if d else (f"{h} ชม." if h else f"{int(sec // 60)} นาที")


OS_RULES = {"ทุก TF ที่มีหัวขาลง (แนะนำ)": "all", "อย่างน้อย 1 TF": "any", "ไม่กรอง": "none"}


def os_ok(os4, os1) -> bool:
    """หัวขาลงที่หายไปผ่าน Oversold ตามกติกาที่เลือก (None = TF นั้นไม่มีหัวก่อนหาย)"""
    flags = [bool(v) for v in (os4, os1) if v is not None and not _nn(v)]
    rule = OS_RULES[os_rule]
    if rule == "none":
        return True
    if not flags:
        return False
    return all(flags) if rule == "all" else any(flags)


WL_MAX = 100
WITHIN = {"ทั้งหมด": None, "24 ชม.": 24, "3 วัน": 72, "7 วัน": 168, "30 วัน": 720}

# =====================================================================
with tab_watch:
    st.markdown("หุ้นที่ **เพิ่งเปลี่ยนกลุ่ม** จาก 🔴 มีหัว Fibo ขาลง (4H หรือ 1H) → 🟢 ไม่มีหัว Fibo ขาลง "
                f"เรียงจากล่าสุด (สูงสุด {WL_MAX} ตัว) · ใช้ผลสแกนและเงื่อนไข (ดัชนี / market cap / EMA / หัวขาขึ้น) "
                "เดียวกับหน้า 🔍 สแกนหุ้น")
    if raw_view is None:
        st.info("ยังไม่มีผลสแกน — เปิดผลสแกนอัตโนมัติ หรือกดสแกนสดที่หน้า 🔍 สแกนหุ้น ก่อน")
    elif not scanner.has_watchlist(raw_view):
        st.info("ผลสแกนนี้ยังไม่มีข้อมูลเวลาเปลี่ยนกลุ่ม (รูปแบบเก่า) — ไปที่ GitHub **Actions → Auto scan → "
                "Run workflow** หรือกดสแกนสดใหม่")
    else:
        w1, w2, w3 = st.columns([1, 1.4, 0.8])
        within = w1.selectbox("เปลี่ยนกลุ่มภายใน", list(WITHIN), index=0)
        os_rule = w2.selectbox("หัว Fibo ขาลงที่หายไป ต้องผ่าน Oversold", list(OS_RULES), index=0,
                               help="ตัดกรณีหัวขาลงเกิดจาก Lower High + ราคาต่ำกว่า X แล้วราคาเด้งกลับทะลุหัวทันที "
                                    "โดยที่ %K ยังไม่เคยลงถึง Oversold (เช่น HUM) · เก็บเฉพาะกรณีที่ลงไป Oversold "
                                    "ก่อนแล้วค่อยกลับขึ้นมาทะลุหัว (เช่น VMRK)")
        show_val_w = w3.toggle("แสดงราคาหัว Fibo", False, key="wl_val")
        res_w = present(raw_view, show_val_w)
        if res_w.empty:
            wl = res_w
        else:
            wl = res_w[~res_w["_has_dn"] & res_w["_clear_at"].notna()].copy()
            wl = wl[[os_ok(a, b) for a, b in zip(wl["_os4"], wl["_os1"])]] if not wl.empty else wl
        now_utc = pd.Timestamp.now(tz="UTC")
        if not wl.empty:
            wl["_t"] = pd.to_datetime(wl["_clear_at"], utc=True)
            if WITHIN[within]:
                wl = wl[wl["_t"] >= now_utc - pd.Timedelta(hours=WITHIN[within])]
            wl = wl.sort_values("_t", ascending=False).head(WL_MAX)
        m = st.columns(3)
        m[0].metric("ใน Watchlist", f"{len(wl):,}")
        if not wl.empty:
            m[1].metric("เปลี่ยนล่าสุด", wl["_t"].iloc[0].tz_convert("Asia/Bangkok").strftime("%d/%m %H:%M"))
            m[2].metric("ภายใน 24 ชม.", int((wl["_t"] >= now_utc - pd.Timedelta(hours=24)).sum()))
        st.caption(f"ข้อมูลจาก {meta_view['src']} เมื่อ {meta_view['when']:%d %b %Y %H:%M} (เวลาไทย) · "
                   "เวลาเปลี่ยนกลุ่ม = เวลาปิดแท่ง (4H หรือ 1H) แรกที่ไม่มีหัว Fibo ขาลงเหลือทั้งสอง TF · "
                   "ผ่าน Oversold = %K ของ TF นั้นลงต่ำกว่า OS อย่างน้อย 1 แท่ง ระหว่างที่หัวขาลงยังแสดงอยู่")
        if wl.empty:
            st.warning("ยังไม่มีหุ้นที่เปลี่ยนกลุ่มตามเงื่อนไขนี้")
        else:
            disp = pd.DataFrame({
                "เปลี่ยนกลุ่มเมื่อ (เวลาไทย)": wl["_t"].dt.tz_convert("Asia/Bangkok").dt.strftime("%d/%m/%Y %H:%M"),
                "ผ่านมา": [ago(t, now_utc) for t in wl["_t"]],
            })
            for c in ["Symbol", "Name", "Index", "Sector", "MktCap (B)", "Price", EMA_COL, f"% จาก {EMA_COL}",
                      UP_COLS["4H"], UP_COLS["1H"]]:
                disp[c] = wl[c].values
            disp["หัว Fibo ขาลงที่หายไป"] = [
                " · ".join(f"{tf} {v:,.2f}" for tf, v in (("4H", a), ("1H", b)) if not _nn(v)) or "-"
                for a, b in zip(wl["_prev4"], wl["_prev1"])]
            disp["ผ่าน Oversold"] = [
                " · ".join(f"{tf} {'✅' if v else '❌'}" for tf, v in (("4H", a), ("1H", b)) if v is not None and not _nn(v))
                or "-" for a, b in zip(wl["_os4"], wl["_os1"])]
            disp["Chart"] = wl["Chart"].values
            st.dataframe(disp, hide_index=True, width="stretch", key="t_watch",
                         column_config={"Chart": st.column_config.LinkColumn("Chart", display_text="TradingView"),
                                        "Name": st.column_config.TextColumn("Name", width="medium"),
                                        "Index": st.column_config.TextColumn("Index", width="small")})
            st.download_button("⬇️ ดาวน์โหลด Watchlist (CSV)", disp.to_csv(index=False).encode("utf-8-sig"),
                               "watchlist.csv", "text/csv")


def head_detail(df: pd.DataFrame) -> dict:
    """ค่าภายในของ state machine (แท่งล่าสุด) + หัว Fibo ทั้งสองฝั่ง"""
    lo, hi = trigger_low_m(df, hp), trigger_high(df, hp)
    up, dn = head_latch_low(df, hp), head_latch_high(df, hp)
    return {"lo": lo, "hi": hi, "up": up, "dn": dn}


def tf_rows(sym: str, fr: dict) -> list[dict]:
    """ค่าแบบ Combined Dashboard (หัว Fibo 4H / 1H) ของหุ้น 1 ตัว สำหรับเทียบกับ TradingView"""
    price = latest_price(fr)
    e4 = mtf_ema(fr.get("4H"), "4H", int(ema_len), ema_conf)
    rows = []
    for tf in TFS:
        d = tf_frame(fr, tf)
        if d is None:
            continue
        hd = head_detail(d)
        rows.append({
            "Symbol": sym, "TF": tf, "แท่งล่าสุด (NY)": d.index[-1].strftime("%d/%m/%Y %H:%M"),
            "📈 หัว Fibo ขาขึ้น": hv(hd["up"][-1]),
            "📉 หัว Fibo ขาลง": hv(hd["dn"][-1]),
            "Close (TF)": r2(d["Close"].iloc[-1]),
            EMA_COL: (None if _nn(e4) else f"{'✅' if price > e4 else '❌'} {e4:,.2f}") if tf == "4H" else "",
        })
    return rows


# =====================================================================
with tab_check:
    st.markdown("กรอก symbol แล้วเทียบกับ **Combined Mode** (แถว 📈 หัว Fibo ขาขึ้น / 📉 หัว Fibo ขาลง) บน TradingView")
    cc1, cc2, cc3 = st.columns([2, 1, 1])
    sym = cc1.text_input("Symbol", "NVDA").strip().upper()
    ctf = cc2.selectbox("TF ของกราฟ", TFS, key="ctf")
    go_btn = cc3.button("ตรวจสอบ", type="primary", disabled=need_keys)
    if go_btn and sym:
        y = sym.replace(".", "-")
        fr = frames_for(y, {"1h": get_prices((y,), "1h")})
        if tf_frame(fr, ctf) is None:
            st.error("โหลดข้อมูลไม่ได้ ตรวจสอบ symbol")
        else:
            price = latest_price(fr)
            e4 = mtf_ema(fr.get("4H"), "4H", int(ema_len), ema_conf)
            st.subheader(f"{sym}  ·  Price {price:,.2f}  ·  {EMA_COL} "
                         + ("—" if _nn(e4) else f"{'✅' if price > e4 else '❌'} {e4:,.2f}"))
            dash, engines = [], {}
            for tf in TFS:
                d = tf_frame(fr, tf)
                if d is None:
                    continue
                hd = head_detail(d)
                engines[tf] = (d, hd)
                lo, hi = hd["lo"], hd["hi"]
                dash.append({
                    "TF": tf, "แท่งล่าสุด (NY)": d.index[-1].strftime("%d/%m/%Y %H:%M"),
                    "📈 หัว Fibo ขาขึ้น": hv(hd["up"][-1]),
                    "📉 หัว Fibo ขาลง": hv(hd["dn"][-1]),
                    "SW Low": r2(lo["sw"][-1]), "Prev SW Low": r2(lo["prev"][-1]), "X (ขาขึ้น)": r2(lo["x"][-1]),
                    "SW High": r2(hi["sw"][-1]), "Prev SW High": r2(hi["prev"][-1]), "X (ขาลง)": r2(hi["x"][-1]),
                    "%K": r2(lo["k"][-1]), "Close (TF)": r2(d["Close"].iloc[-1]),
                })
            st.dataframe(pd.DataFrame(dash), hide_index=True, width="stretch")
            st.caption("SW / Prev / X ด้านขวาคือค่าภายในของ state machine (f_htfTriggerLowM / f_htfTriggerHigh) "
                       "ไว้ช่วยไล่หาสาเหตุถ้าหัว Fibo ไม่ตรงกับ TradingView")

            df, hd = engines[ctf]
            show = df.tail(250)
            n0 = len(df) - len(show)
            fig = go.Figure()
            fig.add_candlestick(x=show.index, open=show["Open"], high=show["High"], low=show["Low"],
                                close=show["Close"], name=sym)
            fig.add_scatter(x=show.index, y=hd["up"][n0:], mode="lines", line=dict(shape="hv", width=2, color="#26a69a"),
                            name="📈 หัว Fibo ขาขึ้น")
            fig.add_scatter(x=show.index, y=hd["dn"][n0:], mode="lines", line=dict(shape="hv", width=2, color="#ef5350"),
                            name="📉 หัว Fibo ขาลง")
            if ctf == "4H":
                fig.add_scatter(x=show.index, y=ema(df["Close"], int(ema_len)).iloc[n0:], mode="lines",
                                line=dict(width=1.5, color="#2196f3"), name=EMA_COL)
            elif not _nn(e4):
                fig.add_hline(y=e4, line=dict(width=1.5, dash="dash", color="#2196f3"),
                              annotation_text=f"{EMA_COL} {e4:,.2f}", annotation_position="right")
            fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                              legend=dict(orientation="h"))
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[16, 9.5], pattern="hour")])
            st.plotly_chart(fig, width="stretch")

# =====================================================================
with tab_cmp:
    st.markdown(
        "กรอกได้สูงสุด 10 symbol แอปจะออกตารางหัว Fibo **4H / 1H** แบบเดียวกับ Combined Mode บน TradingView "
        "ให้เปิดกราฟแต่ละตัวแล้วเทียบทีละแถว\n\n"
        "- ค่าที่ควรตรง: มี/ไม่มีหัว (✅ / -) และราคาหัว (ต่างได้ระดับเซนต์จากแหล่งข้อมูล)\n"
        "- ดาวน์โหลด CSV แล้วกรอกค่าจาก TradingView ลงคอลัมน์ `TV ...` ได้ ถ้าเจอตัวไม่ตรงส่งไฟล์นั้นมาให้ไล่แก้")
    raw_syms = st.text_area("Symbols (คั่นด้วยจุลภาค, เว้นวรรค หรือขึ้นบรรทัดใหม่)",
                            "NVDA, AMD, AVGO, MU, AMAT, LRCX, KLAC, QCOM, TXN, ADI")
    syms = []
    for s_ in raw_syms.replace(",", " ").split():
        s_ = s_.strip().upper()
        if s_ and s_ not in syms:
            syms.append(s_)
    if len(syms) > 10:
        st.warning(f"ใช้ 10 ตัวแรก (กรอกมา {len(syms)} ตัว)")
        syms = syms[:10]
    cmp_btn = st.button("📋 สร้างตารางเทียบ", type="primary", disabled=not syms or need_keys)
    if cmp_btn:
        ys = tuple(s_.replace(".", "-") for s_ in syms)
        with st.spinner(f"โหลดราคา {len(ys)} ตัวจาก {source}..."):
            store = {"1h": get_prices(ys, "1h")}
        rows, bad = [], []
        for sym_, y in zip(syms, ys):
            fr = frames_for(y, store)
            if not fr:
                bad.append(sym_)
                continue
            rows += tf_rows(sym_, fr)
        if bad:
            st.warning(f"โหลดข้อมูลไม่ได้: {', '.join(bad)}")
        if rows:
            st.session_state["cmp"] = pd.DataFrame(rows)
            st.session_state["cmp_meta"] = (source, pd.Timestamp.now(tz="Asia/Bangkok"))
    if "cmp" in st.session_state and "📈 หัว Fibo ขาขึ้น" in st.session_state["cmp"].columns:
        cmp = st.session_state["cmp"]
        src_used, when = st.session_state["cmp_meta"]
        st.caption(f"ราคาจาก {src_used} · สร้างเมื่อ {when:%d %b %Y %H:%M} (เวลาไทย) · "
                   + ("ใช้เฉพาะแท่งที่ปิดแล้ว" if confirmed_only else "รวมแท่งที่ยังไม่ปิด"))
        st.dataframe(cmp, hide_index=True, width="stretch", height=min(40 + 35 * len(cmp), 900))
        out = cmp.copy()
        for c in ["TV หัว Fibo ขาขึ้น", "TV หัว Fibo ขาลง", "ตรงไหม (✓/✗)"]:
            out[c] = ""
        st.download_button("⬇️ ดาวน์โหลด CSV (มีคอลัมน์ว่างไว้กรอกค่า TradingView)",
                           out.to_csv(index=False).encode("utf-8-sig"), "compare_tradingview.csv", "text/csv")
