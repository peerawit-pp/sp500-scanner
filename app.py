"""
US Stock Multi-TF Scanner (S&P 500 / NASDAQ 100 / Dow 30 / NASDAQ Composite) — Higher Low + Price > X บน 4H / D / W  +  MTF EMA89 4H
ลอจิกแปลงจาก Pine:
  - "Reversal Candle [Stoch Oversold]"                      -> logic.py  (Higher Low + Price > X)
  - "Reversal Candle [Stoch Oversold] Fibo New ver7 + EMA89" -> fibo.py   (EMA89 MTF + Fibo Support)
รัน:  streamlit run app.py
"""
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
from fibo import FIB_LEVELS, drop_unclosed, fibo_levels, last_bar_closed, mtf_ema
from logic import Params, ema, hl_engine, resample_4h, tf_state

st.set_page_config(page_title="US Stock Multi-TF Scanner", page_icon="📈", layout="wide")
st.title("📈 US Stock Scanner — Higher Low (4H / D / W) + EMA89 4H")
st.caption("ลอจิกจาก Reversal Candle [Stoch Oversold] + MTF EMA89 4H · ราคาจาก Alpaca (SIP ดีเลย์ 15 นาที) "
           "หรือ Yahoo Finance · ใช้ประกอบการตัดสินใจเท่านั้น ยืนยันกราฟบน TradingView ก่อนเข้า order")

TFS = ["4H", "D", "W"]
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


def _alpaca(tickers: tuple, interval: str, key_id: str, secret_key: str, progress=None):
    cache, now = _price_cache(), time.time()
    for k in [k for k, (t0, _) in cache.items() if now - t0 > PRICE_TTL]:
        cache.pop(k, None)                                   # ล้างของหมดอายุ
    hit = cache.get((interval, tickers, key_id))
    if hit:
        return hit[1]
    want = set(tickers)                                      # ใช้ข้อมูลจากการสแกนที่ครอบคลุมอยู่แล้ว
    for (itv, tks, kid), (_, res) in list(cache.items()):
        if itv == interval and kid == key_id and want <= set(tks):
            return {t: res[t] for t in tickers if t in res}
    if interval in ("1h", "1d"):
        res = data.alpaca_download(list(tickers), interval, key_id, secret_key, progress=progress)
    else:
        d = _alpaca(tickers, "1d", key_id, secret_key, progress)
        f = data.daily_to_weekly if interval == "1wk" else data.daily_to_monthly
        res = {t: f(df) for t, df in d.items()}
    for k in [k for k in cache if k[0] == interval and k[2] == key_id and set(k[1]) <= want]:
        cache.pop(k, None)                                   # ชุดเล็กที่อยู่ในชุดใหม่แล้ว ไม่ต้องเก็บซ้ำ (ประหยัด RAM)
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


def frames_for(t: str, store: dict) -> dict:
    out = {}
    if t in store.get("1h", {}):
        out["1H"] = store["1h"][t]
        out["4H"] = resample_4h(store["1h"][t])
    for tf, itv in (("D", "1d"), ("W", "1wk"), ("M", "1mo")):
        if t in store.get(itv, {}):
            out[tf] = store[itv][t]
    return out


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
    for tf in ("1H", "D", "W"):
        if tf in fr and not fr[tf].empty:
            d = drop_unclosed(fr[tf], tf) if confirmed_only else fr[tf]
            if not d.empty:
                return float(d["Close"].iloc[-1])
    return NAN


def fmt_dt(ts, tf: str):
    if ts is None or (isinstance(ts, float) and ts != ts):
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
    d = fr.get(tf)
    if d is None or d.empty:
        return None
    return drop_unclosed(d, tf) if confirmed_only else d


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


tab_scan, tab_check, tab_cmp = st.tabs(["🔍 สแกนหุ้น", "🧪 ตรวจสอบรายตัว", "📋 เทียบกับ TradingView"])

# =====================================================================
with tab_scan:
    if need_keys:
        st.warning("ใส่ Alpaca API Key / Secret ที่แถบซ้ายก่อน (หรือเลือก Yahoo Finance)")
    if not sel_idx:
        st.warning("เลือกดัชนีอย่างน้อย 1 ตัวที่แถบซ้าย")
    run = st.button(f"🔍 สแกน {len(uni_sel):,} ตัว", type="primary",
                    disabled=not sel_tfs or need_keys or uni_sel.empty)
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

                    store[itv] = get_prices(tickers, itv, _prog)
                    pb.progress(1.0, text=f"{label} · เสร็จใน {time.time() - t0:.0f} วิ")
                    load_rep[itv] = (len(store[itv]), sorted(set(tickers) - set(store[itv])))
                    st.write(f"→ ได้ {load_rep[itv][0]}/{len(tickers)} ตัว")

            st.write("รัน Higher Low บน 4H / D / W + EMA89 4H")
            rows = []
            for _, r in uni.iterrows():
                fr = frames_for(r["Yahoo"], store)
                price = latest_price(fr)
                if price != price:
                    continue
                e4 = mtf_ema(fr.get("4H"), "4H", p.ema_len, ema_conf)
                above = price > e4                                   # NaN -> False
                if use_ema and not above:
                    continue
                sts = {tf: tf_state(tf_frame(fr, tf), p) for tf in TFS}
                hits = [tf for tf in sel_tfs if tf_ok(sts[tf])]
                ok = bool(hits) if match_mode == "อย่างน้อย 1 TF" else len(hits) == len(sel_tfs)
                if not ok:
                    continue
                row = {
                    "Symbol": r["Symbol"], "Name": r["Name"], "Index": r["Index"], "Sector": r["Sector"],
                    "MktCap (B)": r2(r["MarketCapB"]), "Price": r2(price),
                    f"EMA{p.ema_len} 4H": None if e4 != e4 else f"{'✅' if above else '❌'} {e4:,.2f}",
                    "% จาก EMA": r2((price / e4 - 1) * 100) if e4 == e4 else None,
                    "TF เข้า": len(hits),
                }
                for tf in TFS:
                    s = sts[tf]
                    row[f"{tf} Higher Low"] = tick(s["hl"])
                    row[f"{tf} Price vs X"] = tick(s["px"])
                    row[f"{tf} Alert"] = fmt_dt(s["alert_date"], tf)
                    row[f"{tf} X"] = r2(s["x"])
                    row[f"{tf} % ถึง X"] = r2((s["x"] / price - 1) * 100)
                    row[f"{tf} SW Low"] = r2(s["sw"])
                    row[f"{tf} Previous SW"] = r2(s["prev"])
                    row[f"_bs_{tf}"] = s["bars_since"] if tf in hits else None
                if show_fibo:
                    fb = fibo_for(fr, price)
                    if fib_filter and not (fb["support_dist"] == fb["support_dist"]
                                           and -fb["support_dist"] <= fib_maxd):
                        continue
                    row.update({"Fibo": fb["label"],
                                "แนวรับใกล้สุด": None if not fb["support_name"]
                                else f"{fb['support_name']} @ {fb['support']:,.2f}",
                                "% ถึงแนวรับ": r2(fb["support_dist"])})
                row["Chart"] = f"https://www.tradingview.com/chart/?symbol={r['Symbol'].replace('.', '-')}"
                rows.append(row)
            st.session_state["results"] = pd.DataFrame(rows)
            st.session_state["meta"] = (len(tickers), sel_tfs, match_mode, pd.Timestamp.now(tz="Asia/Bangkok"),
                                        source, load_rep, list(sel_idx))
            status.update(label="สแกนเสร็จ", state="complete", expanded=False)

    if "results" not in st.session_state:
        st.info("เลือกดัชนีและตั้งเงื่อนไขทางซ้าย แล้วกดปุ่ม **สแกน**")
    else:
        res = st.session_state["results"]
        n, tfs_used, mode, when, src_used, load_rep, idx_used = st.session_state["meta"]
        m = st.columns(5)
        m[0].metric("สแกนทั้งหมด", n)
        m[1].metric("เข้าเงื่อนไข", len(res))
        for col, tf in zip(m[2:], TFS):
            both = ((res[f"{tf} Higher Low"] == "✅") & (res[f"{tf} Price vs X"] == "✅")).sum() if not res.empty else 0
            col.metric(f"{tf}  HL ✅ X ✅", int(both))
        st.caption(f"ดัชนี: {', '.join(idx_used)} · TF: {', '.join(tfs_used)} ({mode}) · ราคาจาก {src_used} · "
                   f"สแกนเมื่อ {when:%d %b %Y %H:%M} (เวลาไทย)")
        missing = {itv: miss for itv, (_, miss) in load_rep.items() if miss}
        if missing:
            with st.expander(f"⚠️ ข้อมูลไม่ครบ: " + " · ".join(
                    f"{itv} ขาด {len(m)} ตัว" for itv, m in missing.items())):
                for itv, m in missing.items():
                    st.write(f"**{itv}**: {', '.join(m)}")
        else:
            st.caption("✅ โหลดข้อมูลราคาครบทุกตัว")
        if res.empty:
            st.warning("ไม่มีหุ้นเข้าเงื่อนไข")
        else:
            f1, f2 = st.columns([3, 1])
            secs = sorted(res["Sector"].unique())
            pick = f1.multiselect("กรอง Sector", secs, default=secs)
            detail = f2.toggle("แสดงรายละเอียด X / SW Low", False)
            view = res[res["Sector"].isin(pick)].copy()
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
                               "sp500_scan.csv", "text/csv")

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
