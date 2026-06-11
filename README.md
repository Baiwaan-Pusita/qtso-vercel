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
| `POST /api/qt-phase2` | ⏳ TODO |
| `POST /api/qt-phase2-update` | ⏳ TODO |
| `POST /api/qt-phase3` | ⏳ TODO |
| `POST /api/qt-lookup` | ⏳ TODO |
| `POST /api/draft-render` | ⏳ TODO |
| `GET /api/qt/<record_id>` | ⏳ TODO |
| PDF generation | ❌ skipped — Lark already auto-fills Detail Short Cut |

Phase 1 (read + auth + UI) is deploy-ready. Submit flow (writes) needs
porting from the original Python server in `/Users/pusita/App QT&SO/server.py`
(`_qt_phase2`, `_qt_phase2_update`, `_qt_phase3`, `_create_lines_only`).
