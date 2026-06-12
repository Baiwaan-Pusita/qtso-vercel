# 📋 Session Log — 2026-06-12

> Vercel app: **https://qtso-vercel.vercel.app/**
> Repo: **github.com/Baiwaan-Pusita/qtso-vercel**
> ⭐ **Latest working commit:** `ab51ce6` (DEV base) + bridge field architecture

---

## ✅ Final Working Architecture (the one to keep)

```
                         ┌────────────────────────┐
  Vercel /api/qt-phase3 │  เขียน 2 fields:        │
                         │   • Item for Selection │ ← อาจ fail (Reference+conditions)
                         │   • _pending_item_     │ ← เขียนผ่านเสมอ (text, ไม่มี Reference)
                         │     selection           │
                         └──────────┬─────────────┘
                                    ↓
                         ┌──────────▼─────────────┐
                         │  Lark Base             │
                         │                        │
                         │  _effective_item_      │  Formula:
                         │   selection (Formula)  │   IF(LEN([Item for Selection])>0,
                         │                        │      [Item for Selection],
                         │                        │      [_pending_item_selection])
                         └──────────┬─────────────┘
                                    ↓
                         ┌──────────▼─────────────┐
                         │  Re-pointed Lookups:   │
                         │   • Item Name   ──┐    │  filter Item Code where
                         │   • Item Code   ──┤────│  Item for selection = _effective_item_selection
                         │                   │    │
                         │  Chained Lookups: │    │
                         │   • BU            │    │  filter via Item Name (fldKEDNhRD)
                         │   • Department    │    │
                         │   • Account Code  │    │
                         │   • Business Model│    │
                         │   • Business Model Type
                         │   • etc.          │    │
                         └───────────────────┘────┘

Result: ALL data populates regardless of whether record came from:
  • Lark Form submission (Item for Selection filled directly) ✅
  • Vercel API submission (_pending_item_selection filled, formula resolves) ✅

No Automation needed. Reference + filter conditions preserved on Item for Selection
(Lark Forms still use dropdown filtered by Status=Active + BU matching).
```

---

## 🗂️ สิ่งที่ทำวันนี้ — ตามเวลา

### 13:00–14:00 — Reference SingleSelect investigation
- ทดสอบ 8+ formats เพื่อเขียน Item for Selection ผ่าน API ทั้งหมด → 1254062 SingleSelectFieldConvFail
- ค้นพบว่า Reference options ที่มี filter conditions block API writes
- ลอง user_token via OAuth → ยัง block

### 14:00–15:00 — UX cleanups + token refresh flow
- ลบ banner "ไปเติม 4 fields" จาก Done page (commit `6f81f0a`)
- Auto-redirect ไป Lark login บน bootstrap
- เพิ่ม Token refresh: เมื่อ user_access_token หมดอายุ (~2h) ใช้ refresh_token mint ใหม่อัตโนมัติ (commits `617fd26`, `8e003a0`)
- Auto-recover stale_parent_record (commit `aeff5bd`) — ไม่ต้องเปิด DevTools เคลียร์ localStorage เอง

### 15:00–15:30 — Field unlocking discovery
- Admin unlocked Last item + Date Working (Month) → เลิก strip 2 field นี้ (commit `4b06328`)
- เพิ่มการเขียน checkbox "ข้าพเจ้ายืนยันว่าข้อมุลครบถ้วน"
- ลองเขียน Item field (separate SingleSelect with same options) → fail 1254062 → revert (commit `3e0ab1f`)

### 16:00–16:30 — Lookup audit + fixes
- Audit 65 Lookups ทั้ง QT Mgmt + QT&SO Detail
- เจอ 2 broken lookups (target field IDs ตาย): QT Created Time Stamp, SO Requested Time Stamp
- เจอ 1 lookup ที่ดึงจาก deprecated table: Credit term Special
- แก้ Business Model Type lookup ให้ใช้ BU (New) แทน BU (Old) — สำเร็จที่ admin UI
- Refactor /api/qt/{id} → ใช้ POST /records/search แทน GET (returns text แทน option_id)  (commit `d2b57f6`)

### 16:30–17:30 — record_id internal flow
- Switch ไป TEST base (commit `42b673d`)
- เพิ่ม `item_record_id` flow ใน frontend + backend (commits `896bc64`, `fd9fe8c`, `9c2f985`)
- Vercel internal ใช้ record_id ของ Item Code เป็น canonical handle (audit log, validation)
- ไม่เขียนเข้า Lark — เก็บใน Vercel state เท่านั้น

### 17:30–18:30 — Bridge field architecture (FINAL SOLUTION) ⭐
- Switch กลับ PROD เพื่อ test (commit `a3fa6cb`)
- ค้นพบสำคัญ: **มันไม่ใช่ Reference ที่ block, แต่เป็น filter conditions** (Status=Active + BU matching)
- **สร้าง buffer field `_pending_item_selection`** (text, no Reference) ผ่าน Lark Field Create API ใน PROD
- Vercel เขียนทั้ง Item for Selection (อาจ fail) + buffer (เขียนผ่านเสมอ) (commit `2808867`)
- ปฏิเสธ Automation, เลือกใช้ Formula bridge
- Admin สร้าง:
  - **Formula field `_effective_item_selection`** = `IF(LEN([Item for Selection])>0, [Item for Selection], [_pending_item_selection])`
  - Re-point Item Name + Item Code Lookups ใช้ `_effective_item_selection`
- Switch กลับ TEST base verify chain (commit `ab51ce6`) ← **HEAD ปัจจุบัน**

---

## 📁 ไฟล์สำคัญ

| File | Purpose |
|---|---|
| `/Users/pusita/qtso-vercel/api/index.py` | Flask backend (~1170 lines) |
| `/Users/pusita/qtso-vercel/index.html` | Frontend SPA (~3400 lines) |
| `/Users/pusita/qtso-vercel/vercel.json` | Vercel routing config |
| `/Users/pusita/qtso-vercel/requirements.txt` | Python deps (Flask, itsdangerous) |
| `/Users/pusita/qtso-vercel/SESSION_LOG_2026-06-12.md` | ← เอกสารนี้ |

---

## 🔑 Functions ล่าสุดที่ใช้งานได้ (commit `ab51ce6`)

### `api/index.py`

```python
# Lines 35–60 — Base config (currently DEV/TEST)
BASE_APP_TOKEN = "Gr9jbWRZFa9zQVsn1jflt0zWgaf"  # DEV/TEST
TABLES = {
    "qt_mgmt":        "tblwxmRty8lfo31m",
    "qtso_detail":    "tblYofJYQSxJGkaF",
    "brands_company": "tblZ7Eszq9aQV8ab",
    "customer":       "tblJZQW0zdG2buhF",
    "item_code":      "tblWV3aGS52fZp8P",
    "employee":       "tble8RRCdlvCBBdU",
    "project":        "tblCMlP2zPG2PaCy",
}

# Lines 165–202 — get_item_by_record_id() — Vercel-internal item resolution
# Lines 199–242 — refresh_user_token() — OAuth refresh
# Lines 469–504 — get_session() / set_session_cookie()
# Lines 654–895 — _create_lines() — เขียน line items + adaptive Stage 3 strip
# Lines 924–966 — _search_record() — fetch via POST /records/search (returns text)
# Lines 967–1010 — _get_qt_full() — fetch parent + lines
# Lines 1040–1115 — /api/qt-phase3 — proactive refresh + write + cookie update
```

**Critical lines เขียน 2 fields แบบ bridge:**
```python
# ~line 744 in _create_lines:
if line.get("item_selection"):
    lf["Item for Selection"] = line["item_selection"]        # canonical (may fail)
    lf["_pending_item_selection"] = line["item_selection"]   # buffer (always succeeds)
```

### `index.html`

```javascript
// Line 1016 — bootstrap() — auto-redirect to Lark login on first visit
// Line 1808 — updateLine() — stamp item_record_id when user picks item
// Line 1880 — renderLines() preview — uses record_id for stable item resolution
// Line 2454 — buildPayload() — includes item_record_id per line
// Line 2877 — resumeQtFromLark() — clears stale recordId on 404
// Line 3257–3275 — submit handler — auto-recover from stale_parent_record
```

---

## 🏗️ Lark Base config ที่ admin ตั้งไว้ (ทั้ง PROD + DEV)

### Required fields ใน QT&SO Detail:

| Field | Type | Purpose |
|---|---|---|
| `Item for Selection` | SingleSelect (Reference + conditions) | Lark Form input; Vercel may fail to write |
| `_pending_item_selection` | Text | Vercel buffer (always writable) |
| `_effective_item_selection` | Formula | `IF(LEN([Item for Selection])>0, [Item for Selection], [_pending_item_selection])` |
| `Item Name` | Lookup → `_effective_item_selection` | derives item English name |
| `Item Code` | Lookup → `_effective_item_selection` | derives item code (AFF-001 etc.) |
| `BU` / `Department` / `Account Code` / `Business Model` / `Business Model Type` / etc. | Lookup → chain via Item Name | auto-fill ตาม |

---

## 🚀 วิธีใช้งาน (Final)

1. เปิด https://qtso-vercel.vercel.app/
2. ครั้งแรก → Lark login (auto-redirect)
3. กรอก Phase 1 (Contract/Non-Contract) → Phase 2 (Brand/Customer/Period) → Phase 3 (Items)
4. กด Submit → Vercel สร้าง record ใน DEV base
5. Lark resolve formula chain → data populate ครบ

---

## 🔄 วิธี Switch base ระหว่าง DEV/PROD

แก้ใน `api/index.py` lines 35–60:

```python
# DEV/TEST:
BASE_APP_TOKEN = "Gr9jbWRZFa9zQVsn1jflt0zWgaf"
TABLES = {"qt_mgmt": "tblwxmRty8lfo31m", "qtso_detail": "tblYofJYQSxJGkaF", ...}

# PROD:
BASE_APP_TOKEN = "HZwsbAdIHabtqXspWLDlNryUg1e"
TABLES = {"qt_mgmt": "tbllrHviruBy5ltH", "qtso_detail": "tbl01rn7UYG0Gl7w", ...}
```

(ทั้งสอง base มี bridge fields ครบแล้ว ใช้สลับได้ทันที)

---

## ⏰ Token TTLs (สำคัญ)

| Token | TTL | Refresh |
|---|---|---|
| tenant_access_token (bot) | 2 ชม. | auto via get_token() cache |
| user_access_token (OAuth) | 2 ชม. | auto via refresh_user_token() |
| refresh_token | ~30 วัน | manual re-login when expired |
| Session cookie (itsdangerous) | 7 วัน | renewed on each refresh |

---

## 🧪 Test base credentials

- App ID: `cli_aa9fd13c9b799eef`
- App Secret: `qaRpTqTt8euDmkpRFJM5QemA3FbBswjr`
- Org ID: `LEBVBZA7XA4`
- DEV wiki: https://p42u8dv87zi.sg.larksuite.com/wiki/UL6KwpQ2oiEWOCkqn8pluCEZgIf
- PROD wiki: https://p42u8dv87zi.sg.larksuite.com/wiki/DxaywzCmjiDhb2kKU5hl5QOwgLd

---

## 📝 Known Issues (ไม่กระทบ submit หลัก)

| Issue | Status |
|---|---|
| `QT Created Time Stamp` lookup ตาย (target field ID `fldSgVP3h3` ไม่มีอยู่) | Optional fix in Lark UI |
| `SO Requested Time Stamp` lookup ตาย (`fldsi8rTzg`) | Optional fix |
| `Credit term Special` ดึงจาก deprecated table | Optional fix |
| `Item` field ใน PROD ยังเป็น SingleSelect type=3 (TEST เป็น Lookup type=19) | Cosmetic — 0/2042 records have it filled |
| `BU Detail` ยัง Reference-locked | Workaround ผ่าน buffer field ก็ใช้แทนได้ |
| `desc_mode (ท่านต้องการเขียน Description)` ยัง Reference-locked | Workaround ผ่าน buffer field ก็ใช้แทนได้ |

---

## 🎯 Commit Hash ของ working version: `ab51ce6`

ถ้าจะ revert/rollback → `git checkout ab51ce6`

---

_End of session log — 2026-06-12_
