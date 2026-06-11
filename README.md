# QT/SO Generator — Vercel

Vercel-deployable version of the Ripples QT/SO Generator. Writes directly
to Lark Bitable "QT&SO Base" (`HZwsbAdIHabtqXspWLDlNryUg1e`).

## Stack

- **Frontend**: vanilla HTML/JS in `public/index.html` (3385 lines, no build step)
- **Backend**: Flask serverless function at `api/index.py`
- **Sessions**: signed cookies via `itsdangerous` (no external store)
- **Routing**: `vercel.json` proxies `/api/*` and `/preview/*` to the Python function

## Deploy to Vercel

1. **Push this repo to GitHub** (your fork).
2. **Connect** the repo to your Vercel project (`baiwaan-s-projects`).
3. **Set these env vars** in Vercel project settings:
   - `LARK_APP_ID` — `cli_aa9fd13c9b799eef`
   - `LARK_APP_SECRET` — (from Lark Developer Console)
   - `SESSION_SECRET` — a random 32+ char string (generate with `openssl rand -hex 32`)
4. **Deploy.** Vercel auto-detects `@vercel/python` for `api/index.py`.
5. **Register the live redirect URL in Lark Developer Console**:
   - `https://<your-project>.vercel.app/api/auth/lark/callback`
   - Without this, OAuth login fails with Lark error 20029.

## Local dev

```bash
pip install -r requirements.txt
PORT=3000 python3 api/index.py
# open http://localhost:3000
```

## Endpoint status

| Path | Status |
|---|---|
| `GET /` → `public/index.html` | ✅ |
| `GET /api/brand-companies` | ✅ |
| `GET /api/customers` | ✅ |
| `GET /api/items` | ✅ |
| `GET /api/approvers` | ✅ |
| `GET /api/all-employees` | ✅ |
| `GET /api/projects` | ✅ |
| `GET /api/exchange-rates` | ✅ |
| `GET /api/me` | ✅ |
| `GET /api/auth/login` | ✅ |
| `GET /api/auth/lark/callback` | ✅ |
| `GET /api/auth/logout` | ✅ |
| `POST /api/qt-phase2` | ✅ |
| `POST /api/qt-phase2-update` | ✅ |
| `POST /api/qt-phase3` | ✅ (with safety guard + adaptive SS strip) |
| `POST /api/qt-lookup` | ✅ |
| `POST /api/draft-render` | ⚠️ placeholder (no PDF on serverless) |
| `GET /api/qt/<record_id>` | ✅ |
| PDF generation | ❌ skipped — preview via Lark Base directly |

All endpoints ported. The submit flow:
- Phase 2 creates the QT Management parent (≤3s poll for QT ID / Request No.)
- Phase 3 PATCHes the parent + creates Detail lines
- If parent already has lines → returns `phase3_safety_check` (no destructive ops)
- Each line tries POST as user → falls back through adaptive SingleSelect strip
  if the prod base's field protection rejects it
- PDF generation is removed (no headless Chrome on Vercel); the user previews
  the final QT inside Lark Base instead
