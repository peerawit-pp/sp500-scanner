# US Stock Scanner — Higher Low ✅ + Price vs X ✅ (4H / D / W) + MTF EMA89 4H

เว็บแอปสแกนหุ้น US (ฟรี) จาก **S&P 500 / NASDAQ 100 / Dow Jones 30 / NASDAQ Composite** กรอง Market cap ได้เอง (ค่าเริ่มต้น ≥ 10B) แล้วคัดตัวที่เข้าเงื่อนไขจาก Pine ของคุณ

## ไฟล์
| ไฟล์ | แปลงจาก Pine | หน้าที่ |
|---|---|---|
| `logic.py` | Reversal Candle [Stoch Oversold] — `f_lookbackLow`, `f_htfTrigger`/`f_ltfSwLow`, alert | Higher Low + Price vs X |
| `fibo.py` | Fibo New ver7 + EMA89 MTF — `f_htfTrigger` (ver7), Sticky Fallback, Weekly OB, EMA conf/live | EMA89 MTF + Fibo Support |
| `app.py` | — | หน้าเว็บ: สแกน / ตรวจสอบรายตัว / เทียบกับ TradingView |
| `scanner.py` | — | แกนการสแกน ใช้ร่วมกันระหว่างแอปและสแกนอัตโนมัติ (ผลตรงกัน) |
| `scan_job.py` | — | สแกนอัตโนมัติ (รันบน GitHub Actions) → `results/scan_latest.csv` |
| `.github/workflows/scan.yml` | — | ตารางเวลาสแกนอัตโนมัติ |
| `data.py` | — | รายชื่อหุ้นตามดัชนี + market cap, ราคาจาก Alpaca หรือ Yahoo |
| `indices.csv` | — | สมาชิก NASDAQ 100 (102 ตัว) และ Dow Jones 30 — แก้ไฟล์นี้เมื่อดัชนีเปลี่ยนสมาชิก |
| `us_listings_fallback.csv` | — | รายชื่อหุ้น NASDAQ/NYSE/AMEX + market cap สำรอง (ใช้เมื่อโหลดออนไลน์ไม่ได้) |
| `sp500_fallback.csv` | — | รายชื่อ S&P 500 สำรอง |

---

## ขั้นที่ 1 — สมัคร Alpaca เพื่อเอา API Key (ฟรี, ประมาณ 5 นาที)
1. เข้า https://alpaca.markets → **Sign Up** (ใช้แค่บัญชี Paper Trading ก็พอ ไม่ต้องฝากเงิน)
2. ล็อกอินเข้า Dashboard → เลือกบัญชี **Paper** → มองหากล่อง **API Keys** → กด **Generate New Keys**
3. คัดลอก **API Key** และ **Secret Key** เก็บไว้ (Secret จะแสดงครั้งเดียว ถ้าหายต้องสร้างใหม่)

> ⚠️ ห้ามใส่ Key ลงในไฟล์ที่อัปโหลดขึ้น GitHub — ใส่ใน Secrets ของ Streamlit ตามขั้นที่ 3 เท่านั้น

## ขั้นที่ 2 — Deploy ฟรีบน Streamlit Community Cloud (เปิดจากมือถือได้)
1. สร้าง repo ใหม่บน GitHub แล้วอัปโหลดไฟล์ทั้งหมดในโฟลเดอร์นี้ (ลากตัวไฟล์ ให้ `app.py` อยู่ชั้นนอกสุด)
2. เข้า https://share.streamlit.io → Sign in ด้วย GitHub → **Create app**
3. เลือก repo, branch `main`, Main file = `app.py` → **Deploy**

## ขั้นที่ 3 — ใส่ API Key ใน Secrets
ในหน้าแอปบน Streamlit Cloud → **⋮ / Settings → Secrets** → วางข้อความนี้ (แทนค่าด้วย Key ของคุณ) → **Save**
```toml
ALPACA_KEY = "PKxxxxxxxxxxxxxxxx"
ALPACA_SECRET = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```
แอปจะรีสตาร์ทเอง แถบซ้ายจะขึ้น "🔑 ใช้ API Key จาก Secrets"
(ถ้าไม่ใส่ Secrets ก็กรอก Key ในแถบซ้ายได้ แต่ต้องกรอกใหม่ทุกครั้งที่เปิดแอป)

**รันบนคอมตัวเอง:** `pip install -r requirements.txt` → สร้างไฟล์ `.streamlit/secrets.toml` ใส่ 2 บรรทัดด้านบน → `streamlit run app.py`

## ขั้นที่ 4 — ตรวจความแม่นยำก่อนใช้จริง
แท็บ **📋 เทียบกับ TradingView** → ใส่ 10 symbol → **สร้างตารางเทียบ** → เปิดกราฟแต่ละตัวบน TradingView เทียบ
Previous SW / SW Low / Higher Low / X / Price vs X ทั้ง W / D / 4H
- ผ่านเกณฑ์: ติ๊ก ✅/❌ ตรงกันอย่างน้อย 9/10 ตัว และ X / SW Low ต่างไม่เกินราว 0.1%
- ไม่ผ่าน: ดาวน์โหลด CSV กรอกค่าจาก TradingView ลงคอลัมน์ `TV ...` แล้วส่งกลับมาให้ไล่แก้

## ขั้นที่ 5 — เปิดสแกนอัตโนมัติ (GitHub Actions, ฟรี) → เปิดแอปแล้วเห็นผลทันที
**5.1 ใส่ Key ใน GitHub** (คนละที่กับ Streamlit)
repo บน GitHub → **Settings → Secrets and variables → Actions → New repository secret** เพิ่ม 2 ตัว
- Name `ALPACA_KEY` → Secret = API Key
- Name `ALPACA_SECRET` → Secret = Secret Key

**5.2 สร้างไฟล์ตารางเวลา** (โฟลเดอร์ที่ขึ้นต้นด้วยจุดอัปโหลดแบบลากไฟล์มักไม่ติด ให้สร้างแบบนี้)
repo → **Add file → Create new file** → ช่องชื่อพิมพ์ `.github/workflows/scan.yml` (พิมพ์ `/` แล้ว GitHub จะสร้างโฟลเดอร์ให้)
→ คัดลอกเนื้อหาจากไฟล์ `.github/workflows/scan.yml` ใน zip มาวาง → **Commit changes**

**5.3 สั่งรันครั้งแรกเอง** แท็บ **Actions** → **Auto scan** → **Run workflow**
- ครั้งแรกดึงข้อมูลเต็มประมาณ 3–5 นาที เสร็จแล้วจะมีโฟลเดอร์ `results/` โผล่ใน repo และ Streamlit จะอัปเดตเอง
- รอบถัดไปดึงเฉพาะแท่งใหม่ (cache ราคาเก็บใน GitHub) ใช้ราว 1–2 นาทีต่อรอบ

**ตารางเวลา (วันจันทร์–ศุกร์)** — หลังแท่ง 4H ปิด + 20 นาที (ข้อมูลฟรีดีเลย์ 15 นาที)
| แท่ง 4H (เวลา NY) | เวลาไทย (ช่วงหนาว พ.ย.–มี.ค.) | เวลาไทย (ช่วงร้อน มี.ค.–พ.ย.) |
|---|---|---|
| 09:30–13:30 | ~01:50 | ~00:50 |
| 13:30–16:00 (ปิดตลาด) | ~04:20 | ~03:20 |

- ตั้งไว้ทั้ง 2 ช่วงเวลา รอบที่ยังไม่มีแท่งใหม่จะข้ามเองโดยไม่บันทึก
- GitHub อาจเลื่อนเวลารันช้ากว่าที่ตั้ง 5–30 นาทีในช่วงคนใช้เยอะ
- repo แบบ Private ได้ Actions ฟรี 2,000 นาที/เดือน ใช้จริงราว 150–300 นาที/เดือน
- อยากเปลี่ยน market cap ขั้นต่ำของสแกนอัตโนมัติ: แก้ `MIN_CAP_B: "10"` ใน scan.yml

**ในแอป** แท็บสแกนจะเปิดที่ **📦 ผลสแกนอัตโนมัติล่าสุด** — เห็นผลทันที เลือกดัชนี / market cap (ไม่ต่ำกว่าที่ตั้งใน scan.yml) /
เงื่อนไข Higher Low / EMA ได้ทันทีโดยไม่ต้องรอ ถ้าอยากได้ผลสดกว่านั้นกด **🔍 สแกนสดตอนนี้**

---

## ความเร็วในการสแกน
| สถานการณ์ | เวลาโดยประมาณ (617 ตัว) |
|---|---|
| เปิดผลสแกนอัตโนมัติ | ไม่กี่วินาที |
| เปลี่ยนเงื่อนไขกรองหลังสแกน (ทั้ง 2 โหมด) | ทันที ไม่ต้องสแกนใหม่ |
| สแกนสดครั้งแรกหลังแอปรีสตาร์ท/หลับ | ~3 นาที (ติดลิมิต Alpaca ฟรี 200 คำขอ/นาที) — โหลดทีละ 100 ตัว ถ้าหยุดกลางทางกดใหม่จะทำต่อ |
| สแกนสดซ้ำภายใน 15 นาที | ~15 วินาที (ใช้ cache) |
| สแกนสดซ้ำหลัง 15 นาที (ภายใน 3 วัน) | ~20–40 วินาที (ดึงเฉพาะแท่งใหม่) |

## แหล่งข้อมูลราคา
| | Alpaca (แนะนำ) | Yahoo Finance (สำรอง) |
|---|---|---|
| ประเภท | API ทางการ ฟรี (ต้องมี Key) | ไม่ใช่ API ทางการ ไม่ต้องสมัคร |
| ข้อมูล | SIP รวมทุกตลาด ดีเลย์ 15 นาที | ดีเลย์ ~15 นาที |
| 1H / 4H | ดึง 30Min → ตัดเหลือ 09:30–16:00 → รวมเป็น 1H/4H แบบ TradingView (ย้อนหลัง 250 วัน) | 1H ย้อนหลัง 730 วัน |
| D / W | 1Day ย้อนหลัง 6 ปี (OHLC ไม่รวม pre/post) → รวมเป็น W (เริ่มวันจันทร์) | 1d / 1wk 10 ปี |
| ความเสี่ยง | จำกัด 200 คำขอ/นาที (แอปคุมไว้ที่ 180 + ลองซ้ำอัตโนมัติ) | อาจโดนบล็อกเมื่อดึงหลายร้อยตัว |

- Market cap มาจากรายชื่อหุ้นของ Nasdaq (อัปเดตรายวัน) ไม่ได้มาจาก Alpaca
- หลังสแกน แอปแจ้งว่าโหลดได้กี่ตัว ถ้าขาดจะมีรายชื่อในกล่อง "⚠️ ข้อมูลไม่ครบ"

## เงื่อนไขการคัดกรอง (ทำงานตามลำดับ)
0. **ดัชนี** เลือกได้หลายตัว (ค่าเริ่มต้นเลือกทั้ง 4) — รวมรายชื่อแบบไม่ซ้ำ ตารางผลมีคอลัมน์ Index บอกว่าหุ้นอยู่ดัชนีไหน
   - S&P 500: ดึงจาก GitHub (datasets/s-and-p-500-companies)
   - NASDAQ Composite + market cap + sector: ดึงจาก GitHub (rreichel3/US-Stock-Symbols ข้อมูลจาก Nasdaq อัปเดตรายวัน)
   - NASDAQ 100 / Dow 30: จาก `indices.csv` (อัปเดต พ.ย. 2026)
   - ทั้ง 4 ดัชนีที่ ≥ 10B ≈ 617 ตัว → สแกนครั้งแรกด้วย Alpaca ประมาณ 3 นาที
1. **Market cap** ขั้นต่ำ–สูงสุด (พันล้าน USD) ค่าเริ่มต้น ≥ 10B
2. **MTF EMA89 4H** — ราคาต้อง `>` EMA89 ของ 4H (เหมือนคอลัมน์ EMA89 ใน Dashboard)
   - "ใช้แท่ง 4H ที่ปิดแล้ว" = `ta.ema()[1]` + `lookahead_on` (No Repaint)
3. **Higher Low / Price vs X** รันพร้อมกันบน **4H / D / W** แสดง ✅ / ❌ / — แบบ Dashboard บน TradingView
   - เลือกได้: "Higher Low ✅ + Price vs X ✅" หรือ "Higher Low ✅ อย่างเดียว"
     ใน "อย่างน้อย 1 TF" หรือ "ทุก TF ที่เลือก" และกรองเฉพาะ Alert ที่เพิ่งเกิดภายใน N แท่งได้
4. **Fibo Support** (ไม่บังคับ, ปิดไว้เป็นค่าเริ่มต้น)

## ความต่างจาก TradingView ที่ยังเหลือ
- ราคาจาก Alpaca / Yahoo อาจต่างจาก feed ของ TradingView ระดับเซนต์ → หุ้นที่ราคาชิด X หรือ SW Low สองตัวใกล้กันมาก ติ๊กอาจพลิกได้
- ประวัติข้อมูลสั้นกว่าบน TradingView → state machine เริ่มคนละจุด แต่ลู่เข้าหากันหลัง cycle reset ที่ OB
- Price vs X ใช้ราคาปิดของแท่ง TF นั้นเอง (บน TradingView ถ้าเปิดกราฟ 4H จะเทียบ X ของ D/W กับราคาปิด 4H)
- EMA ใช้ source = close
