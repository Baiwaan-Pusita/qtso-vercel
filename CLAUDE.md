# CLAUDE.md — Project Rules & Operating Guide

> **Project:** QT/SO Generator (Vercel + Lark Base)
> **Repo:** `github.com/Baiwaan-Pusita/qtso-vercel`
> **Live:** `https://qtso-vercel.vercel.app/`
> **Latest stable commit:** `ab51ce6` (bridge architecture verified)

This file tells future Claude sessions what to do (and not do) in this project.
Read this BEFORE making changes.

---

## 🚨 Critical Safety Rules (read first)

1. **NEVER delete any data in Lark Base** — user explicitly stated:
   > "เดียวจะให้ไป sync กับ database ของจริงแล้ว รบกวนระมัดระวังอย่าลบข้อมูลใดๆ"

   - No `_delete_existing_lines()`-style helpers — they were intentionally removed.
   - No mass `DELETE /records/{id}` calls without explicit user confirmation.
   - Be cautious with field config changes — Lookup re-points can return wrong values silently.

2. **Don't toggle "Reference options" on Lark Base fields without explicit permission.**
   - Admin manages those via Lark UI, not via API.
   - The Lark Update Field API doesn't support Lookup/Reference config changes anyway.

3. **Always commit + push.** User expects code on GitHub repo after each change. Don't leave staged or uncommitted changes for them to discover.

4. **Use HEREDOC for commit messages.** Include `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` (no other co-authors).

---

## 🏗️ Architecture (Current Working State)

```
Browser (frontend index.html)
     ↓ /api/qt-phase2  → create QT Management parent
     ↓ /api/qt-phase3  → create QT&SO Detail line items
     ↓
Vercel Flask (api/index.py)
     ↓ Lark OAuth user_access_token (auto-refresh on expiry)
     ↓ Falls back to tenant_access_token
     ↓
Lark Base (one of two):
     • DEV/TEST: app_token = Gr9jbWRZFa9zQVsn1jflt0zWgaf
     • PROD:     app_token = HZwsbAdIHabtqXspWLDlNryUg1e
```

### Bridge field pattern (the key trick — keep this)

Vercel writes BOTH on every line:
- `Item for Selection` (canonical SingleSelect) — may fail with 1254062 if Reference+conditions are on
- `_pending_item_selection` (plain Text, buffer) — always writable

In Lark Base, a Formula field reconciles:
```
_effective_item_selection = IF(LEN([Item for Selection]) > 0,
                              [Item for Selection],
                              [_pending_item_selection])
```

Downstream Lookups (`Item Name`, `Item Code`) derive through `_effective_item_selection`. Other Lookups (`BU`, `Department`, `Account Code`, `Business Model`, `Business Model Type`, etc.) chain through `Item Name`.

**Result:** All item-related fields populate regardless of whether the record came from Lark Form (sets Item for Selection directly) or Vercel API (sets buffer when SingleSelect is locked).

---

## 🔐 Credentials

```
APP_ID:     cli_aa9fd13c9b799eef
APP_SECRET: qaRpTqTt8euDmkpRFJM5QemA3FbBswjr
ORG_ID:     LEBVBZA7XA4
REGION:     SG (p42u8dv87zi.sg.larksuite.com / open.larksuite.com)
```

User has re-confirmed these multiple times. If they don't work, ask user — don't substitute different values.

---

## 🗂️ Lark Base Table Reference

### DEV/TEST base (`Gr9jbWRZFa9zQVsn1jflt0zWgaf`)
```python
TABLES = {
    "qt_mgmt":        "tblwxmRty8lfo31m",
    "qtso_detail":    "tblYofJYQSxJGkaF",
    "brands_company": "tblZ7Eszq9aQV8ab",
    "customer":       "tblJZQW0zdG2buhF",
    "item_code":      "tblWV3aGS52fZp8P",
    "employee":       "tble8RRCdlvCBBdU",
    "project":        "tblCMlP2zPG2PaCy",
}
```

### PROD base (`HZwsbAdIHabtqXspWLDlNryUg1e`)
```python
TABLES = {
    "qt_mgmt":        "tbllrHviruBy5ltH",
    "qtso_detail":    "tbl01rn7UYG0Gl7w",
    "brands_company": "tblB5r3geCkdI8YH",
    "customer":       "tblnpZe52qwky9U2",
    "item_code":      "tblUTOBmBulfRriq",
    "employee":       "tbl2ReQCQvCc7rg1",
    "project":        "tbl7Vd0aRxXXCx1l",
}
```

Both bases have `_pending_item_selection` (Text) + `_effective_item_selection` (Formula). To switch, edit `BASE_APP_TOKEN` + `TABLES` in `api/index.py` lines 35–60.

---

## 📋 Known Locked Fields (don't waste cycles testing again)

These return `1254062 SingleSelectFieldConvFail` on direct API write — confirmed via isolated PUT tests:

| Field | Status | Workaround |
|---|---|---|
| `Item for Selection` (PROD) | Reference + 2 conditions | Bridge via `_pending_item_selection` (works) |
| `BU Detail` | Reference-locked | No workaround currently; field is rarely needed |
| `ท่านต้องการเขียน Description เพิ่มเติมหรือไม่` (desc_mode) | Reference-locked | No workaround currently |
| `Item (Not Used)` | Deprecated, locked | Don't write |
| `Item` (PROD, type=3) | Deprecated SingleSelect | Don't write. In DEV it's a Lookup (type=19) — populates via Item Code link |

These return `code=0` (writable) — no special handling needed:

```
BU with Description, Period Type, Starting month, Working Year,
เก็บเงินได้เลยไหม, Last item, Date Working (Month),
ข้าพเจ้ายืนยันว่าข้อมุลครบถ้วน, Quantity, Unit Price,
Description Input, Remark, Project Detail
```

---

## 🛠️ Common Operations

### Probe field writability (when user asks "field X writable?")

```python
# Run via /Users/pusita/qtso-vercel; standalone Python script.
# Use a small isolated PUT against an existing record's ID.
# Don't use real production data values — use known-existing option names.
```

### Add a new Text field via API

```python
POST /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields
body: {"field_name": "...", "type": 1}  # 1=Text, 2=Number, 3=SingleSelect, 5=Date, 7=Checkbox
# This DOES work. Created _pending_item_selection in prod this way.
```

### Cannot do via API (Lark restrictions)

- Update Lookup field config (type=19) — must use Lark UI
- Create automation/workflow rule — POST /workflows = 404
- Trigger/run existing workflow — /run = 404
- Read Formula field's `formula` property — Lark returns empty
- Toggle Reference options on SingleSelect — restricted

### Fetch a record with text-resolved values (not option_id)

Use POST `/records/search` with filter on `ID` formula field — NOT GET `/records/{id}`. Search returns names; GET returns opt_ids for Lookup-to-SingleSelect.

```python
res = lark_request("POST",
    f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{table_id}/records/search?page_size=1",
    {"filter": {"conjunction": "and",
                "conditions": [{"field_name": "ID", "operator": "is", "value": [record_id]}]}},
    token=token)
```

`_search_record()` helper in `api/index.py` does this + flattens the `{type, value: [...]}` wrapper.

---

## 🚫 Things to AVOID

| Don't | Why |
|---|---|
| Try to programmatically uncheck Reference options | Lark API doesn't support Lookup/Reference config updates |
| Create Lark Automation rules via API | POST workflows = 404 |
| Write to `Item` / `Item (Not Used)` / `BU Detail` | Locked. Test base has Item as Lookup which auto-fills via link |
| Use GET /records/{id} for user-facing display data | Returns option_ids for Lookups; use search endpoint |
| Re-add `enrich_desc_input()` prepending into Description Input | User rejected: "อันนี้ควรกระจายไปใส่ให้ถูกช่อง" — metadata belongs in own columns (Lookups) |
| Skip Stage 3 adaptive strip | Lark intermittently locks/unlocks fields; strip is the safety net |
| Mix `position`/`url` query params on the search endpoint | Use `field_names` (URL-encoded JSON array) |
| Use bash `cat` / `sed` / `awk` for code reading/editing | Use Read/Edit/Write tools instead |

---

## 🎯 User Preferences (observed across sessions)

1. **Language:** Thai. Default responses in Thai unless user switches.
2. **Conciseness:** When the user says short things like "C" or "ทำเลย", they want action, not explanation. Skip the long preamble.
3. **Single source of truth:** Production code lives in this repo. User commits everything to GitHub. Don't leave drafts in working tree.
4. **Pragmatism over perfection:** When stuck on a hard Lark restriction, propose 3 options with trade-offs and let user pick.
5. **Admin actions are OK:** User accepts that some changes (Lark UI field configs, automations) require admin in Lark Base UI. Don't insist on full automation when Lark blocks the API path.
6. **Test base for risky experiments:** When in doubt, switch to DEV/TEST base, iterate, verify, then switch back to PROD.
7. **Hate redundant questions:** Don't ask "are you sure?" — read the request carefully and act. Use AskUserQuestion only when truly blocked.

---

## ⏰ Token & Session Reference

| Token | TTL | Refresh path |
|---|---|---|
| `tenant_access_token` (bot) | 2h | `get_token()` cache auto-refreshes |
| `user_access_token` (OAuth) | 2h | `refresh_user_token()` proactive + reactive |
| `refresh_token` | ~30 days | Manual re-login when expired (session cookie cleared automatically) |
| Session cookie (itsdangerous) | 7 days | Renewed on each refresh |

**Don't** assume tokens are valid. Refresh proactively in `qt-phase3`. Fall back to tenant_token if user_token fails with 99991677/99991679/HTTP 401.

---

## 📂 File Reference

| Path | Purpose |
|---|---|
| `api/index.py` | Flask backend (~1170 lines) |
| `index.html` | Frontend SPA (~3400 lines, no build step) |
| `vercel.json` | Routes `/api/*` and `/preview/*` to `api/index` |
| `requirements.txt` | Python deps |
| `SESSION_LOG_2026-06-12.md` | Detailed log of that day's work |
| `CLAUDE.md` | ← This file (rules for future Claude) |

---

## 🔄 Standard Workflow for a New Session

1. **Read this file** (CLAUDE.md) first.
2. **Check current base** via `grep BASE_APP_TOKEN api/index.py` — confirm which base is active.
3. **Read the most recent SESSION_LOG_*.md** if user references recent work.
4. **Verify working state** before making changes — `git log -5` to see latest commits.
5. **Before code changes:** ask if user wants test base or prod base (default: keep current).
6. **After code changes:** commit + push, then verify deploy URL works.
7. **If user reports a bug:** probe Lark API state first (writability tests) before assuming code bug.

---

## ✅ "Working" Markers in Code

Search for `⭐ WORKING` in code comments to find function-level "this is verified to work" tags. The big one is `_create_lines()` in `api/index.py` ~line 654.

When a function is updated and verified working, update its docstring with:
```python
⭐ WORKING — last verified commit <hash> (YYYY-MM-DD).
```

---

## 🆘 If something breaks

1. Don't panic. Check `git log` — every change is committed.
2. Roll back to `ab51ce6` if needed: `git checkout ab51ce6` (this is the verified bridge architecture).
3. Run writability probes to identify if Lark Base config changed (admin may have toggled something).
4. Check Vercel deploy logs — issues often show up there.
5. Tell user what you found before fixing.

---

_End of CLAUDE.md — keep this file updated as the project evolves._
