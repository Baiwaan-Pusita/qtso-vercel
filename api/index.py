"""
QT/SO Generator — Vercel Python Serverless deploy.

This is a Flask wrapper that exposes the same endpoints as the original
http.server-based /Users/pusita/App QT&SO/server.py, refactored for Vercel:

  • In-memory _sessions dict  →  signed-cookie session via itsdangerous
  • Headless-Chrome PDF       →  removed (record creation only; PDF deferred)
  • Polling loops (~10s)      →  removed (Vercel function timeout)
  • Single Handler class      →  Flask routes calling module-level helpers

The Lark business logic (lark_request, fetch_all_records, get_field_option_index,
text_val) is ported in place at the top of this file — it's almost identical
to the Python original, just no http.server-specific code.
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Any

from flask import Flask, request, jsonify, send_from_directory, redirect, make_response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# ─── Config ──────────────────────────────────────────────────────────────────

APP_ID = os.environ.get("LARK_APP_ID", "cli_aa9fd13c9b799eef")
APP_SECRET = os.environ.get("LARK_APP_SECRET", "qaRpTqTt8euDmkpRFJM5QemA3FbBswjr")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-change-me-in-vercel")

## Switched back to TEST base (wiki UL6KwpQ2oiEWOCkqn8pluCEZgIf) per user
## request — safer for iterating on field-level fixes without touching prod
## data. Production base IDs preserved in comments below for quick rollback.
##
## Production base (paused):
##   BASE_APP_TOKEN = "HZwsbAdIHabtqXspWLDlNryUg1e"
##   qt_mgmt        = "tbllrHviruBy5ltH"
##   qtso_detail    = "tbl01rn7UYG0Gl7w"
##   brands_company = "tblB5r3geCkdI8YH"
##   customer       = "tblnpZe52qwky9U2"
##   item_code      = "tblUTOBmBulfRriq"
##   employee       = "tbl2ReQCQvCc7rg1"
##   project        = "tbl7Vd0aRxXXCx1l"
BASE_APP_TOKEN = "Gr9jbWRZFa9zQVsn1jflt0zWgaf"  # TEST base
LARK_HOST = "https://open.larksuite.com"
LARK_AUTH_HOST = "https://accounts.larksuite.com"

TABLES = {
    "qt_mgmt":        "tblwxmRty8lfo31m",
    "qtso_detail":    "tblYofJYQSxJGkaF",
    "brands_company": "tblZ7Eszq9aQV8ab",
    "customer":       "tblJZQW0zdG2buhF",
    "item_code":      "tblWV3aGS52fZp8P",
    "employee":       "tble8RRCdlvCBBdU",
    "project":        "tblCMlP2zPG2PaCy",
}

def _redirect_url() -> str:
    """Lark redirect URI — MUST exactly match what's registered in the Lark
    Developer Console (Security Settings → Redirect URLs). Misconfiguration
    yields Lark error 20029 "Invalid redirect URL".

    Resolution order:
      1) LARK_OAUTH_REDIRECT env var (explicit override — for any custom domain).
      2) Hardcoded production URL — stable across Vercel preview hashes.
      3) Localhost fallback for `python3 api/index.py` dev runs.

    DO NOT use VERCEL_URL — Vercel mints a fresh preview-hash hostname per
    deploy (e.g. qtso-vercel-hgbo3ayhu-…), so Lark can never have that exact
    URL pre-registered and OAuth fails on every preview."""
    explicit = os.environ.get("LARK_OAUTH_REDIRECT", "").strip()
    if explicit:
        return explicit
    if os.environ.get("VERCEL"):
        return "https://qtso-vercel.vercel.app/api/auth/lark/callback"
    return "http://localhost:3000/api/auth/lark/callback"

# ─── Lark API helpers (ported from server.py) ────────────────────────────────

_token_cache: dict = {"token": None, "expires_at": 0}

def lark_request(method: str, path: str, body=None, token: str | None = None) -> dict:
    url = f"{LARK_HOST}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"code": -1, "msg": f"HTTPError {e.code}", "body": e.read().decode(errors="ignore")}
    except Exception as e:
        return {"code": -1, "msg": str(e)}

def get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    res = lark_request("POST", "/open-apis/auth/v3/app_access_token/internal",
                       {"app_id": APP_ID, "app_secret": APP_SECRET})
    if res.get("code") != 0:
        raise RuntimeError(f"Auth failed: {res}")
    _token_cache["token"] = res["tenant_access_token"]
    _token_cache["expires_at"] = now + res.get("expire", 3600)
    return _token_cache["token"]

def fetch_all_records(table_id: str, field_names: list[str] | None = None) -> list[dict]:
    token = get_token()
    records: list[dict] = []
    page_token = None
    while True:
        q = "page_size=500"
        if page_token: q += f"&page_token={page_token}"
        if field_names: q += "&field_names=" + urllib.parse.quote(json.dumps(field_names))
        res = lark_request("GET",
                           f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{table_id}/records?{q}",
                           token=token)
        if res.get("code") != 0:
            raise RuntimeError(f"Fetch failed: {res}")
        records.extend(res["data"].get("items", []))
        if not res["data"].get("has_more"): break
        page_token = res["data"].get("page_token")
    return records

def text_val(v: Any) -> str:
    if v is None: return ""
    if isinstance(v, bool): return "Yes" if v else ""
    if isinstance(v, (int, float)): return str(v)
    if isinstance(v, str): return v
    if isinstance(v, list):
        parts = []
        for x in v:
            if isinstance(x, dict):
                parts.append(x.get("text") or x.get("en_name") or x.get("name") or "")
            else:
                parts.append(text_val(x))
        return " ".join(p for p in parts if p)
    if isinstance(v, dict):
        return v.get("text") or v.get("en_name") or v.get("name") or ""
    return ""

_item_info_cache: dict | None = None

def get_item_info_lookup() -> dict:
    """Build a {item_for_selection_label: item_info_dict} map from the Item
    Code table. Used to enrich Description Input with Item Code / BU /
    Department text whenever the user picks an item in the form — since the
    prod base locks the Item for Selection / BU / Department SingleSelects
    and we can't write them via API, this is how we surface the picked
    item's metadata in Lark Base in a way that's still searchable + visible
    (Description formula reflects Description Input)."""
    global _item_info_cache
    if _item_info_cache is not None:
        return _item_info_cache
    lookup: dict = {}
    try:
        for r in fetch_all_records(TABLES["item_code"], [
            "Item for selection", "Item Code", "Item", "Item Name",
            "BU", "BU (New)", "Department", "Account Code",
        ]):
            f = r.get("fields", {})
            key = text_val(f.get("Item for selection"))
            if not key: continue
            lookup[key] = {
                "code":   text_val(f.get("Item Code")),
                "item":   text_val(f.get("Item")) or text_val(f.get("Item Name")),
                "bu":     text_val(f.get("BU (New)")) or text_val(f.get("BU")),
                "dept":   text_val(f.get("Department")),
                "acct":   text_val(f.get("Account Code")),
            }
    except Exception as e:
        print(f"[item-lookup] failed to build cache: {e}")
    _item_info_cache = lookup
    return lookup


def enrich_desc_input(line: dict, current_desc: str) -> str:
    """Prepend Item Code / Item Name / BU / Department / Account Code info to
    the user's Description Input. Output format reads cleanly in Lark Base's
    Description column even when Item for Selection / BU / Department lookup
    columns (which Lark blocks API writes to) stay empty.

    Format (single line, pipe-separated):
        [AFF-001] Affiliates Commission | BU: AFF — Affiliates | Dept: 7410 | Acct: 412320
        <user's typed description>
    """
    item_sel = line.get("item_selection") or ""
    if not item_sel: return current_desc
    info = get_item_info_lookup().get(item_sel) or {}
    if not info: return current_desc
    parts = []
    if info.get("code"): parts.append(f"[{info['code']}]")
    if info.get("item"): parts.append(info["item"])
    extras = []
    if info.get("bu"):   extras.append(f"BU: {info['bu']}")
    if info.get("dept"): extras.append(f"Dept: {info['dept']}")
    if info.get("acct"): extras.append(f"Acct: {info['acct']}")
    if extras: parts.append("| " + " | ".join(extras))
    prefix = " ".join(parts).strip()
    if not prefix: return current_desc
    if current_desc:
        return f"{prefix}\n{current_desc}"
    return prefix


_field_option_cache: dict = {}

def get_field_option_index(table_id: str, field_name: str) -> dict:
    key = (table_id, field_name)
    if key in _field_option_cache:
        return _field_option_cache[key]
    res = lark_request("GET",
        f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{table_id}/fields?page_size=500",
        token=get_token())
    idx = {}
    if res.get("code") == 0:
        for f in res["data"].get("items", []):
            if f.get("field_name") == field_name:
                for o in (f.get("property") or {}).get("options", []):
                    n = o.get("name") or ""
                    idx.setdefault(n, n)
                break
    _field_option_cache[key] = idx
    return idx

# ─── Session helpers (signed cookies) ────────────────────────────────────────

signer = URLSafeTimedSerializer(SESSION_SECRET, salt="qtso-session")
SESSION_MAX_AGE = 7 * 24 * 3600

def get_session() -> dict | None:
    cookie = request.cookies.get("sid")
    if not cookie: return None
    try:
        return signer.loads(cookie, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

def set_session_cookie(resp, user: dict):
    sid = signer.dumps(user)
    resp.set_cookie("sid", sid, max_age=SESSION_MAX_AGE, httponly=True,
                    samesite="Lax", secure=os.environ.get("VERCEL", "") == "1")
    return resp

# ─── Flask app ───────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)

@app.errorhandler(Exception)
def _handle_uncaught(e):
    import traceback
    return jsonify({"error": str(e), "trace": traceback.format_exc()[-800:]}), 500

# ─── Read endpoints (port of _list_* functions) ──────────────────────────────

@app.route("/api/brand-companies", methods=["GET"])
def api_brand_companies():
    recs = fetch_all_records(TABLES["brands_company"],
                             ["Brands - Company", "Brand", "Company", "Company Address",
                              "Company tax ID", "Company tel", "Credit Term", "Credit Term Special",
                              "Status"])
    out = []
    for r in recs:
        f = r.get("fields", {})
        name = text_val(f.get("Brands - Company"))
        if not name: continue
        status_raw = f.get("Status") or []
        status_list = []
        if isinstance(status_raw, list):
            for s in status_raw:
                if isinstance(s, dict): status_list.append(s.get("name") or s.get("text") or "")
                else: status_list.append(str(s))
        elif isinstance(status_raw, str): status_list = [status_raw]
        if any("inactive" in (s or "").lower() for s in status_list): continue
        ct = f.get("Credit Term")
        credit_terms = []
        if isinstance(ct, list):
            for x in ct:
                if isinstance(x, dict): credit_terms.append(x.get("name") or x.get("text") or "")
                else: credit_terms.append(str(x))
        out.append({
            "record_id": r["record_id"], "name": name,
            "brand": text_val(f.get("Brand")), "company": text_val(f.get("Company")),
            "address": text_val(f.get("Company Address")),
            "tax_id": text_val(f.get("Company tax ID")), "tel": text_val(f.get("Company tel")),
            "credit_terms": [c for c in credit_terms if c],
            "credit_term_special": text_val(f.get("Credit Term Special")),
        })
    out.sort(key=lambda x: x["name"].lower())
    return jsonify(out)

@app.route("/api/customers", methods=["GET"])
def api_customers():
    recs = fetch_all_records(TABLES["customer"],
                             ["Customer Detail", "PIC Name", "Brand", "Email", "Tel.", "Status"])
    out = []
    for r in recs:
        f = r.get("fields", {})
        name = text_val(f.get("Customer Detail"))
        if not name: continue
        out.append({
            "record_id": r["record_id"], "name": name,
            "pic": text_val(f.get("PIC Name")), "brand": text_val(f.get("Brand")),
            "email": text_val(f.get("Email")), "tel": text_val(f.get("Tel.")),
            "status": text_val(f.get("Status")),
        })
    out.sort(key=lambda x: x["name"].lower())
    return jsonify(out)

@app.route("/api/items", methods=["GET"])
def api_items():
    # Pull BU (New) too — this is the SAME column QT&SO Detail's BU with
    # Description references. Its values look like 'AFF — Affiliates' (em-dash
    # full label) instead of just 'AFF'. The frontend's BU dropdown uses this
    # so what the user picks IS exactly what gets written to Lark — no
    # mapping/transformation gap that could yield 1254062 from a stale value.
    recs = fetch_all_records(TABLES["item_code"],
                             ["Item Name", "Item", "Item Code", "Item for selection",
                              "Document Type", "BU", "BU (New)", "Description TH",
                              "Combined Description", "Special Description", "Status",
                              "Multiple Options"])
    out = []
    for r in recs:
        f = r.get("fields", {})
        status = (text_val(f.get("Status")) or "").lower()
        if status and status != "active": continue
        item = text_val(f.get("Item")) or text_val(f.get("Item Name"))
        if not item: continue
        mo_raw = f.get("Multiple Options") or []
        multiple_options = []
        if isinstance(mo_raw, list):
            for x in mo_raw:
                n = x.get("name") or x.get("text") or "" if isinstance(x, dict) else str(x)
                if n and n != "New": multiple_options.append(n)
        # bu_full = the em-dash format ('AFF — Affiliates') used by BU with
        # Description in QT&SO Detail. bu kept as the short code ('AFF') for
        # backward compat with existing per-line logic + filter dropdown.
        bu_short = text_val(f.get("BU"))
        bu_full  = text_val(f.get("BU (New)"))
        out.append({
            "record_id": r["record_id"], "item": item,
            "item_code": text_val(f.get("Item Code")),
            "selection": text_val(f.get("Item for selection")),
            "doc_type": text_val(f.get("Document Type")),
            "bu": bu_short,
            "bu_full": bu_full,            # ← NEW: 'AFF — Affiliates'-style label
            "desc": text_val(f.get("Description TH")),
            "combined_desc": text_val(f.get("Combined Description")),
            "special_desc": text_val(f.get("Special Description")),
            "multiple_options": multiple_options,
        })
    out.sort(key=lambda x: x["item"].lower())
    return jsonify(out)

@app.route("/api/all-employees", methods=["GET"])
def api_all_employees():
    recs = fetch_all_records(TABLES["employee"],
                             ["Name", "ชื่อ-นามสกุล", "Full name", "Job Title",
                              "Department", "Contact Email"])
    out = []
    for r in recs:
        f = r.get("fields", {})
        name_field = f.get("Name") or []
        if not isinstance(name_field, list) or not name_field: continue
        u = name_field[0] if isinstance(name_field[0], dict) else {}
        oid = u.get("id")
        if not oid: continue
        out.append({
            "open_id": oid,
            "name": text_val(f.get("ชื่อ-นามสกุล")) or text_val(f.get("Full name"))
                    or u.get("name") or u.get("en_name") or "",
            "en_name": u.get("en_name") or u.get("name") or "",
            "lark_name": u.get("name") or "",
            "avatar_url": u.get("avatar_url") or "",
            "title": text_val(f.get("Job Title")),
            "dept": text_val(f.get("Department")),
            "email": text_val(f.get("Contact Email")) or u.get("email") or "",
        })
    out.sort(key=lambda x: (x.get("en_name") or x.get("name") or "").lower())
    return jsonify(out)

@app.route("/api/approvers", methods=["GET"])
def api_approvers():
    APPROVER_TITLE_KEYWORDS = ("cluster manager", "bd manager", "bu manager")
    out = []
    for r in fetch_all_records(TABLES["employee"],
                               ["Name", "ชื่อ-นามสกุล", "Full name", "Job Title",
                                "Department", "Contact Email", "Tel."]):
        f = r.get("fields", {})
        name_field = f.get("Name") or []
        if not (isinstance(name_field, list) and name_field and isinstance(name_field[0], dict)):
            continue
        oid = name_field[0].get("id")
        if not oid: continue
        title = text_val(f.get("Job Title"))
        if not any(k in (title or "").lower() for k in APPROVER_TITLE_KEYWORDS):
            continue
        u = name_field[0]
        thai_name = text_val(f.get("ชื่อ-นามสกุล")) or ""
        full_name = text_val(f.get("Full name")) or ""
        en_name = u.get("en_name") or ""
        out.append({
            "open_id": oid,
            "name": en_name or full_name or thai_name or u.get("name") or "",
            "en_name": en_name or full_name or thai_name,
            "full_name": full_name,
            "thai_name": thai_name,
            "avatar_url": u.get("avatar_url") or "",
            "title": title,
            "dept": text_val(f.get("Department")),
            "email": text_val(f.get("Contact Email")) or u.get("email") or "",
            "tel": text_val(f.get("Tel.")),
        })
    out.sort(key=lambda x: (x.get("en_name") or x.get("name") or "").lower())
    return jsonify(out)

@app.route("/api/projects", methods=["GET"])
def api_projects():
    recs = fetch_all_records(TABLES["project"],
                             ["Project name", "Brand", "Period", "Item Code",
                              "Item Description", "QT No.", "Date Created"])
    out = []
    for r in recs:
        f = r.get("fields", {})
        name = (text_val(f.get("Project name")) or "").strip()
        brand = (text_val(f.get("Brand")) or "").strip()
        if not name or not brand: continue
        out.append({
            "record_id": r["record_id"], "name": name, "brand": brand,
            "period": (text_val(f.get("Period")) or "").strip(),
            "item_code": text_val(f.get("Item Code")),
            "item_description": text_val(f.get("Item Description")),
            "qt_no": text_val(f.get("QT No.")),
            "_created": f.get("Date Created") or 0,
        })
    out.sort(key=lambda x: -(x.get("_created") or 0))
    for p in out: p.pop("_created", None)
    return jsonify(out)

@app.route("/api/exchange-rates", methods=["GET"])
def api_exchange_rates():
    FALLBACK = {"USD": 35.0, "CNY": 4.85, "VND": 0.0014}
    try:
        req = urllib.request.Request(
            "https://api.frankfurter.app/latest?from=THB&to=USD,CNY,VND",
            headers={"User-Agent": "qtso-app/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read())
        out = {"THB": 1}
        for c in ("USD", "CNY", "VND"):
            rate = (d.get("rates") or {}).get(c)
            out[c] = round(1 / float(rate), 4) if rate else FALLBACK[c]
        return jsonify(out)
    except Exception:
        return jsonify({"THB": 1, **FALLBACK})

# ─── Session endpoints ───────────────────────────────────────────────────────

@app.route("/api/me", methods=["GET"])
def api_me():
    sess = get_session()
    if not sess:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True,
        "open_id": sess.get("open_id"),
        "name": sess.get("name"),
        "en_name": sess.get("en_name"),
        "avatar_url": sess.get("avatar_url"),
        "has_user_token": bool(sess.get("user_access_token")),
    })

@app.route("/api/auth/login", methods=["GET"])
def api_auth_login():
    state = os.urandom(16).hex()
    url = (f"{LARK_AUTH_HOST}/open-apis/authen/v1/authorize"
           f"?app_id={APP_ID}"
           f"&redirect_uri={urllib.parse.quote(_redirect_url(), safe='')}"
           f"&state={state}")
    return redirect(url)

def refresh_user_token(refresh_token: str) -> dict | None:
    """Use a stored refresh_token to mint a fresh user_access_token.
    Returns updated session dict (open_id/name/access_token/refresh_token/
    token_expires_at) or None if refresh failed (refresh_token itself
    expired — TTL ~30 days). Caller should fall back to tenant_token or
    prompt re-login if this returns None."""
    if not refresh_token:
        return None
    try:
        res = lark_request("POST", "/open-apis/authen/v1/refresh_access_token",
                           {"grant_type": "refresh_token",
                            "refresh_token": refresh_token},
                           token=get_token())
    except Exception as e:
        print(f"[refresh-token] HTTP error: {e}")
        return None
    if res.get("code") != 0:
        print(f"[refresh-token] Lark returned: {res}")
        return None
    d = res.get("data", {})
    if not d.get("access_token"):
        return None
    return {
        "open_id": d.get("open_id"),
        "name": d.get("name"),
        "en_name": d.get("en_name") or d.get("name"),
        "avatar_url": d.get("avatar_url") or d.get("avatar_thumb") or "",
        "user_id": d.get("user_id"),
        "user_access_token": d.get("access_token"),
        "refresh_token": d.get("refresh_token") or refresh_token,
        "token_expires_at": int(time.time()) + int(d.get("expires_in") or 7200),
    }


@app.route("/api/auth/callback", methods=["GET"])
@app.route("/api/auth/lark/callback", methods=["GET"])
def api_auth_callback():
    code = request.args.get("code")
    if not code:
        return redirect("/?auth_error=missing_code")
    res = lark_request("POST", "/open-apis/authen/v1/access_token",
                       {"grant_type": "authorization_code", "code": code},
                       token=get_token())
    if res.get("code") != 0:
        return redirect("/?auth_error=exchange_failed")
    d = res.get("data", {})
    user = {
        "open_id": d.get("open_id"),
        "name": d.get("name"),
        "en_name": d.get("en_name") or d.get("name"),
        "avatar_url": d.get("avatar_url") or d.get("avatar_thumb") or "",
        "user_id": d.get("user_id"),
        "user_access_token": d.get("access_token"),
        "refresh_token": d.get("refresh_token"),
        "token_expires_at": int(time.time()) + int(d.get("expires_in") or 7200),
    }
    if not user["open_id"]:
        return redirect("/?auth_error=no_open_id")
    resp = make_response(redirect("/"))
    return set_session_cookie(resp, user)

@app.route("/api/auth/logout", methods=["GET"])
def api_auth_logout():
    resp = make_response(redirect("/"))
    resp.set_cookie("sid", "", max_age=0, httponly=True, samesite="Lax")
    return resp


@app.route("/api/auth/lark/code-login", methods=["POST"])
def api_auth_lark_code_login():
    """Exchange a Lark auth code (obtained client-side via tt.requestAuthCode
    when embedded inside Lark Suite) for a user_access_token + session cookie.
    No browser redirect involved — used by the JS SDK silent-login path."""
    payload = request.get_json(silent=True) or {}
    code = (payload.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "code required"}), 400
    res = lark_request("POST", "/open-apis/authen/v1/access_token",
                       {"grant_type": "authorization_code", "code": code},
                       token=get_token())
    if res.get("code") != 0:
        return jsonify({"ok": False, "error": "exchange_failed", "detail": res}), 401
    d = res.get("data", {})
    if not d.get("open_id"):
        return jsonify({"ok": False, "error": "no_open_id"}), 401
    user = {
        "open_id": d.get("open_id"),
        "name": d.get("name"),
        "en_name": d.get("en_name") or d.get("name"),
        "avatar_url": d.get("avatar_url") or d.get("avatar_thumb") or "",
        "user_id": d.get("user_id"),
        "user_access_token": d.get("access_token"),
        "refresh_token": d.get("refresh_token"),
        "token_expires_at": int(time.time()) + int(d.get("expires_in") or 7200),
    }
    resp = make_response(jsonify({"ok": True, "user": {
        "open_id": user["open_id"], "name": user["name"],
        "en_name": user["en_name"], "avatar_url": user["avatar_url"],
    }}))
    return set_session_cookie(resp, user)

# ─── Write helpers (ported from server.py) ───────────────────────────────────

def _build_parent_fields(payload: dict, scope: str = "all") -> tuple[dict, list]:
    """Build QT Management field dict from payload + drop unknown SingleSelect values.
    scope='phase2'  → booking fields only.
    scope='phase3'  → finalize-add fields (currency, type_of_work, status).
    scope='all'     → both (used on PATCH at submit).
    Returns (fields, skipped_invalid_options)."""
    fields: dict = {}
    is_p2 = scope in ("phase2", "all")
    is_p3 = scope in ("phase3", "all")

    def setk(k: str, v):
        if v not in (None, "", []):
            fields[k] = v

    if is_p2:
        setk("Company", payload.get("company") or "RPL : RIPPLES COMMERCE")
        setk("Document Type", payload.get("doc_type") or "Quotation")
        setk("Brand Company", payload.get("brand_company"))
        setk("Customer Name ID", payload.get("customer_pic"))
        if scope == "phase2":
            setk("Status", payload.get("status") or "QT Booked")
        if payload.get("start_date"):
            fields["Start Date"] = int(payload["start_date"])
        if payload.get("end_date"):
            fields["End date"] = int(payload["end_date"])
        if payload.get("approver_open_id"):
            fields["Approver"] = [{"id": payload["approver_open_id"]}]
        if payload.get("created_by_open_id"):
            fields["QT Confirm Create by"] = [{"id": payload["created_by_open_id"]}]
        att = payload.get("attachment_tokens") or []
        if att:
            fields["Brand's Confirm"] = [{"file_token": t} for t in att if t]
        if payload.get("credit_term"):
            fields["Credit term"] = str(payload["credit_term"]).strip()

    if is_p3:
        setk("Type of Work", payload.get("type_of_work"))
        setk("Currency", payload.get("currency") or "THB")
        if payload.get("exchange_rate"):
            fields["Exchange Rate"] = float(payload["exchange_rate"])
        if scope in ("phase3", "all"):
            fields["Status"] = payload.get("status") or "QT requested"

    # Strip SingleSelect values not present in the field's option list
    ss_fields = ["Company", "Brand Company", "Customer Name ID",
                 "Type of Work", "Currency", "Document Type", "Status", "Credit term"]
    skipped = []
    for fname in ss_fields:
        if fname not in fields: continue
        idx = get_field_option_index(TABLES["qt_mgmt"], fname)
        val = fields[fname]
        if not isinstance(val, str) or not val: continue
        if val in idx: continue
        v_norm = val.strip().lower()
        canonical = next((n for n in idx if n.strip().lower() == v_norm), None)
        if canonical:
            fields[fname] = canonical
        else:
            fields.pop(fname)
            skipped.append({"field": fname, "value": val})
    return fields, skipped


def _write_parent_with_retry(parent_fields: dict, record_id: str | None = None,
                              method: str = "POST", token: str | None = None,
                              fallback_to_tenant: bool = True) -> tuple[dict, dict]:
    """POST (create) or PUT (update) parent fields with progressive drop-retry.

    Token strategy: callers normally pass the user's OAuth user_access_token so
    Lark's system "Created by" field gets stamped with the human's identity
    (avatar + Thai name visible in Lark UI, not "bot"). If the user_token POST
    fails with 99991679 (missing scope), automatically fall back to tenant_token
    so the record still gets created — just attributed to the app instead.
    Set fallback_to_tenant=False to disable the fallback (e.g. for endpoints
    where authorship matters more than success)."""
    tok = token or get_token()
    base = f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{TABLES['qt_mgmt']}/records"
    url = f"{base}/{record_id}" if record_id else base
    res = lark_request(method, url, {"fields": parent_fields}, token=tok)
    # Fallback: user_token lacks bitable scope → retry as tenant
    if (res.get("code") == 99991679 or "99991679" in (res.get("body") or "")) \
            and fallback_to_tenant and token and token != get_token():
        res = lark_request(method, url, {"fields": parent_fields}, token=get_token())
    for drop in ("Credit term", "Brand's Confirm", "QT Confirm Create by", "Approver"):
        if res.get("code") == 0: break
        if drop in parent_fields:
            parent_fields = {k: v for k, v in parent_fields.items() if k != drop}
            res = lark_request(method, url, {"fields": parent_fields}, token=tok)
    return res, parent_fields


def _count_existing_lines(parent_id: str) -> list[str]:
    """Returns the line record_ids that link back to this parent. SAFE — read-only.
    Phase 3 uses this to refuse re-submits (no destructive ops on prod base)."""
    all_lines = fetch_all_records(TABLES["qtso_detail"], ["QT&SO Management"])
    existing = []
    for r in all_lines:
        link = r.get("fields", {}).get("QT&SO Management") or []
        ids = []
        if isinstance(link, list):
            for x in link:
                if isinstance(x, dict):
                    ids.append(x.get("record_id") or x.get("id"))
                else:
                    ids.append(x)
        if parent_id in ids:
            existing.append(r["record_id"])
    return existing


def _create_lines(payload: dict, parent_id: str, user_token: str | None = None) -> dict:
    """Create QT&SO Detail lines. Strategy:
      1) If user_token is supplied, try it FIRST (bypasses field-level protection
         on the 4 locked SingleSelects).
      2) If user_token returns 99991679 ("user lacks bitable scope"), automatically
         fall back to tenant_token — the strip will drop the 4 protected fields
         but at least the row gets created with the rest.
    Adaptive strip on 1254062 — drops one SS field at a time to find the culprit."""
    primary_token = user_token or get_token()
    fallback_token = get_token() if user_token else None
    token = primary_token
    created_lines: list[str] = []
    line_errors: list[dict] = []
    warnings: list[dict] = []
    remark_text = payload.get("remark") or ""
    LAST_YES = "ใช่ record นี้เป็นบรรทัดสุดท้าย พร้อมส่งข้อมูลทั้งหมดให้ ทีม Sale-co create document แล้ว"
    LAST_NO = "ไม่ใช่ ฉันยังต้องการเพิ่มบรรทัดอยู่"
    MONTH_NUM = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                 "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
    VALID_DESC_MODES = {
        "ต้องการเขียน Description ด้วยตนเอง",
        "ต้องการเขียน Description เพิ่มเติม",
        "ไม่ต้องการเขียน Description เพิ่มเติม",
    }
    SS_FIELDS = {
        "Item for Selection", "Item", "BU with Description", "BU Detail",
        "เหตุผลที่ AM Outsource", "Period Type", "Starting month",
        "Working Year", "เก็บเงินได้เลยไหม",
        "ท่านต้องการเขียน Description เพิ่มเติมหรือไม่",
    }
    lines = payload.get("lines", []) or []
    total = len(lines)

    for idx, line in enumerate(lines):
        lf: dict = {"QT&SO Management": [parent_id]}
        # Match local server.py's behavior — send ALL fields the user picked
        # (including the 3 Reference-locked ones). Adaptive Stage 3 strip
        # below drops whichever ones Lark currently rejects, so:
        #   • Fields admin has unlocked (today: BU with Description) → land in Lark
        #   • Fields still locked (today: Item for Selection / BU Detail /
        #     desc_mode) → adaptively dropped, record still created
        #   • When admin unlocks more later → those auto-start populating
        #     with zero code change required
        if line.get("item_selection"):
            lf["Item for Selection"] = line["item_selection"]
            # NOTE: There's a second SingleSelect column 'Item' with the
            # identical option list. Admin probably keeps them in parallel.
            # BUT direct API write to 'Item' returns 1254062 (verified via
            # isolated PUT test with tenant_token — even with no other
            # fields in the payload). 'Item' is Reference-locked at the
            # Lark Base level just like Item for Selection used to be.
            # If admin unchecks 'Reference options' on Item too, we can
            # re-enable the parallel write here.
        if line.get("bu"):
            bu_short = line["bu"].strip().lower()
            bu_idx = get_field_option_index(TABLES["qtso_detail"], "BU with Description")
            matches = [n for n in bu_idx
                       if re.split(r"[:—\-]", n.lower(), maxsplit=1)[0].strip() == bu_short]
            em = next((n for n in matches if "—" in n), None)
            bu_full = em or (matches[0] if matches else None)
            if bu_full: lf["BU with Description"] = bu_full
        if line.get("bu_detail"):
            lf["BU Detail"] = line["bu_detail"]
        if line.get("quantity") is not None:
            lf["Quantity"] = float(line["quantity"])
        if line.get("unit_price") is not None:
            lf["Unit Price"] = float(line["unit_price"])
        if line.get("am_outsourced"):
            lf["AM Outsourced"] = True
            if line.get("am_reason"): lf["เหตุผลที่ AM Outsource"] = line["am_reason"]
            if line.get("am_reason_detail"): lf["อธิบายเหตุผลที่ AM Outsource"] = line["am_reason_detail"]
        if line.get("period_type"): lf["Period Type"] = line["period_type"]
        if line.get("starting_month"): lf["Starting month"] = line["starting_month"]
        if line.get("working_year"): lf["Working Year"] = line["working_year"]
        if line.get("can_bill_now"): lf["เก็บเงินได้เลยไหม"] = line["can_bill_now"]
        if line.get("memo_done"): lf["สั่งงานยัง"] = True
        if line.get("project_link_id"): lf["Project Link"] = [line["project_link_id"]]
        if line.get("project_detail"): lf["Project Detail"] = line["project_detail"]

        # desc_mode (Reference-locked today) — send anyway. Adaptive strip
        # drops it if Lark rejects.
        desc_mode = line.get("desc_mode") or ""
        if desc_mode in {"ต้องการเขียน Description ด้วยตนเอง",
                          "ต้องการเขียน Description เพิ่มเติม",
                          "ไม่ต้องการเขียน Description เพิ่มเติม"}:
            lf["ท่านต้องการเขียน Description เพิ่มเติมหรือไม่"] = desc_mode
        # Description Input — user's typed text ONLY. No more prepending
        # of "[CODE] Name | BU: ... | Dept: ..." metadata. User feedback:
        # "อันนี้ควรกระจายไปใส่ให้ถูกช่อง" — item metadata belongs in its
        # own column (Item Code / Item Name / BU / Department / Account
        # Code), which Lark Base populates AUTOMATICALLY via Lookup as
        # soon as Item for Selection is set. We just need to write the
        # source SingleSelect (Item for Selection + BU with Description)
        # successfully and Lark cascades the rest.
        user_input = (line.get("desc_input") or "").strip().strip(",").strip()
        if user_input:
            lf["Description Input"] = user_input

        sm, wy = line.get("starting_month"), line.get("working_year")
        if sm in MONTH_NUM and wy:
            try:
                epoch_ms = int(time.mktime(time.strptime(f"{wy}-{MONTH_NUM[sm]:02d}-01", "%Y-%m-%d")) * 1000)
                lf["Date Working (Month)"] = epoch_ms
            except Exception:
                pass
        lf["Last item"] = LAST_YES if idx == total - 1 else LAST_NO
        # Confirmation checkbox — match Lark Form behavior. Records created
        # via the official form always have this ticked True. Without it,
        # downstream automations / dashboards may filter the row out.
        lf["ข้าพเจ้ายืนยันว่าข้อมุลครบถ้วน"] = True
        if idx == 0 and remark_text: lf["Remark"] = remark_text

        post_url = f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{TABLES['qtso_detail']}/records"

        # Updated 2026-06-12 after admin unlocked several fields. Tested
        # writability via isolated PUT on each field:
        #   ✅ Last item              → writable (admin unlocked)
        #   ✅ Date Working (Month)   → writable (admin unlocked)
        #   ❌ ท่านต้องการเขียน Description → still locked
        #   ❌ Item (Not Used)        → still locked (deprecated field)
        #   ❌ BU Detail              → still locked
        #   ❌ Item                   → still locked
        # Stage 1 only pre-strips fields we KNOW are locked. As admin
        # unlocks more, Stage 3 adaptive strip handles them dynamically.
        ALWAYS_LOCKED = {
            "ท่านต้องการเขียน Description เพิ่มเติมหรือไม่",
            "Item (Not Used)",
        }

        res = lark_request("POST", post_url, {"fields": lf}, token=token)

        # Fallback path: user_token is bad (missing scope / expired / HTTP 401)
        #   • 99991679 — user lacks bitable scope
        #   • 99991677 — user_access_token expired (TTL ~2h, no auto-refresh yet)
        #   • -1 + HTTPError 401 — generic auth failure from lark_request wrapper
        # In all three cases, switch to tenant_token and let the adaptive
        # Stage 1-3 strip below handle Reference-locked field rejections.
        body_str = res.get("body") or ""
        is_user_token_bad = (
            res.get("code") in (99991677, 99991679)
            or "99991677" in body_str
            or "99991679" in body_str
            or (res.get("code") == -1 and "401" in (res.get("msg") or ""))
        )
        if is_user_token_bad and fallback_token:
            warnings.append({
                "line_index": idx,
                "fallback_to_tenant": True,
                "user_token_error": res.get("code"),
                "hint": "user_access_token expired or lacks scope. "
                        "Switched to tenant_token — Reference-locked fields "
                        "will be dropped by adaptive strip below if needed. "
                        "Logout/login on the app to refresh OAuth token.",
            })
            token = fallback_token
            fallback_token = None  # only switch once per submit
            # Reset to clean slate and retry with tenant token
            res = lark_request("POST", post_url, {"fields": lf}, token=token)
        stripped: list[str] = []
        initial_snapshot = None
        if res.get("code") != 0:
            initial_snapshot = {k: v for k, v in lf.items() if k != "QT&SO Management"}
            # Stage 1: drop ALWAYS_LOCKED auto-owned fields (Lark blocks manual
            # writes to these — Item (Not Used) / Last item / Date Working).
            stage1_drop = [k for k in lf if k in ALWAYS_LOCKED]
            if stage1_drop:
                stripped.extend(stage1_drop)
                lf = {k: v for k, v in lf.items() if k not in stage1_drop}
                res = lark_request("POST", post_url, {"fields": lf}, token=token)
            # Stage 2: drop desc_mode if Lark still rejects (Reference-locked).
            if res.get("code") == 1254062 and "ท่านต้องการเขียน Description เพิ่มเติมหรือไม่" in lf:
                lf = {k: v for k, v in lf.items() if k != "ท่านต้องการเขียน Description เพิ่มเติมหรือไม่"}
                stripped.append("ท่านต้องการเขียน Description เพิ่มเติมหรือไม่")
                res = lark_request("POST", post_url, {"fields": lf}, token=token)
            # Stage 3 (adaptive): if STILL failing with 1254062, drop each
            # remaining SingleSelect one at a time to find the rejecter.
            # This is how local server.py handles it — and it lets each field
            # auto-recover when admin unchecks 'Reference options' on it.
            if res.get("code") == 1254062:
                remaining_ss = [k for k in list(lf.keys()) if k in SS_FIELDS]
                for candidate in remaining_ss:
                    trial_lf = {k: v for k, v in lf.items() if k != candidate}
                    trial_res = lark_request("POST", post_url, {"fields": trial_lf}, token=token)
                    if trial_res.get("code") == 0:
                        stripped.append(candidate)
                        warnings.append({
                            "line_index": idx,
                            "ss_dropped": candidate,
                            "hint": f"Lark rejected {candidate!r} (Reference-locked). "
                                    f"Other SingleSelect fields landed OK.",
                        })
                        lf, res = trial_lf, trial_res
                        break
        if res.get("code") != 0:
            line_errors.append({
                "index": idx, "error": res, "stripped": stripped,
                "sent_fields": initial_snapshot,
            })
        else:
            created_lines.append(res["data"]["record"]["record_id"])
            if stripped:
                warnings.append({"line_index": idx, "fields_dropped": stripped})
    return {"created_lines": created_lines, "line_errors": line_errors, "warnings": warnings}


def _search_record(table_id: str, record_id: str, token: str) -> dict | None:
    """Fetch ONE record via POST /records/search (filter by record_id).

    Search endpoint resolves Lookup-to-SingleSelect values to the option NAME
    (text) — whereas GET /records/{id} returns raw option_id strings like
    'optPDdxW3Z'. Using search keeps all returned data human-readable.

    Returns the record dict (with fields keyed by name) or None on failure.
    """
    res = lark_request("POST",
        f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{table_id}/records/search?page_size=1",
        {"filter": {"conjunction": "and",
                    "conditions": [{"field_name": "ID", "operator": "is", "value": [record_id]}]}},
        token=token)
    if res.get("code") != 0:
        return None
    items = (res.get("data") or {}).get("items") or []
    if not items:
        return None
    rec = items[0]
    # Search wraps every cell as {type, value: [...]}; flatten back to plain
    # values so callers can use record.fields[name] directly like GET response.
    flat: dict = {}
    for k, v in (rec.get("fields") or {}).items():
        if isinstance(v, dict) and "value" in v:
            vals = v.get("value") or []
            if len(vals) == 1:
                vv = vals[0]
                # Text cells come as [{text: "...", type: "text"}]
                if isinstance(vv, dict) and "text" in vv:
                    flat[k] = vv["text"]
                else:
                    flat[k] = vv
            else:
                flat[k] = vals
        else:
            flat[k] = v
    rec["fields"] = flat
    return rec


def _get_qt_full(record_id: str) -> dict:
    """Fetch parent QT Mgmt record + its linked detail rows.

    Uses POST /records/search instead of GET /records/{id} so Lookup-to-
    SingleSelect fields return their option NAME (text) — not the raw
    option_id. Without this, fields like 'Business Model', 'Item Name',
    'BU' in QT&SO Detail would surface as 'optPDdxW3Z' / 'optnQYsv7B'
    in the QT Preview rendering.
    """
    token = get_token()
    rec = _search_record(TABLES['qt_mgmt'], record_id, token)
    if not rec:
        # Fall back to direct GET in case search filter on 'ID' formula misbehaves
        pr = lark_request("GET",
            f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{TABLES['qt_mgmt']}/records/{record_id}",
            token=token)
        if pr.get("code") != 0:
            return {"error": pr}
        rec = pr["data"]["record"]

    f = rec.get("fields") or {}
    detail = f.get("Detail") or []
    line_ids = []
    if isinstance(detail, list):
        for d in detail:
            if isinstance(d, dict):
                line_ids.extend(d.get("record_ids") or d.get("link_record_ids") or [])
    items = []
    for lid in line_ids:
        line_rec = _search_record(TABLES['qtso_detail'], lid, token)
        if line_rec:
            items.append(line_rec)
        else:
            # Fall back to GET if search misses
            lr = lark_request("GET",
                f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{TABLES['qtso_detail']}/records/{lid}",
                token=token)
            if lr.get("code") == 0:
                items.append(lr["data"]["record"])
    return {"parent": rec, "lines": items}

# ─── Write endpoints ─────────────────────────────────────────────────────────

@app.route("/api/qt-phase2", methods=["POST"])
def api_qt_phase2():
    # Lark OAuth was removed from the UI — Created by comes from the in-form
    # user picker (open_id passed in payload). We don't use user_access_token
    # for writes anymore; tenant_token has full Bitable scope on the app side
    # and works for every field the prod base doesn't protect.
    payload = request.get_json(silent=True) or {}
    fields, skipped = _build_parent_fields(payload, scope="phase2")
    res, _ = _write_parent_with_retry(fields)
    if res.get("code") != 0:
        return jsonify({"ok": False, "step": "phase2_create", "error": res,
                        "skipped_invalid_options": skipped,
                        "fields_attempted": list(fields.keys())})
    rec = res["data"]["record"]
    record_id = rec["record_id"]
    qt_id = text_val(rec["fields"].get("QT ID"))
    request_no = text_val(rec["fields"].get("Request No."))
    # Short poll (~3s max) — Vercel function timeout caps at 10s on Hobby
    if not qt_id or not request_no:
        for _ in range(6):
            time.sleep(0.5)
            chk = lark_request("GET",
                f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{TABLES['qt_mgmt']}/records/{record_id}",
                token=get_token())
            if chk.get("code") == 0:
                f2 = chk["data"]["record"]["fields"]
                qt_id = qt_id or text_val(f2.get("QT ID"))
                request_no = request_no or text_val(f2.get("Request No."))
                if qt_id and request_no: break
    return jsonify({"ok": True, "record_id": record_id,
                    "qt_id": qt_id, "request_no": request_no,
                    "skipped_invalid_options": skipped})


@app.route("/api/qt-phase2-update", methods=["POST"])
def api_qt_phase2_update():
    payload = request.get_json(silent=True) or {}
    rid = payload.get("record_id")
    if not rid:
        return jsonify({"ok": False, "error": "record_id required"}), 400
    fields, skipped = _build_parent_fields(payload, scope="phase2")
    fields.pop("Status", None)  # preserve existing Lark status on re-edit
    res, _ = _write_parent_with_retry(fields, record_id=rid, method="PUT")
    return jsonify({
        "ok": res.get("code") == 0, "record_id": rid,
        "error": res if res.get("code") != 0 else None,
        "skipped_invalid_options": skipped,
    })


@app.route("/api/qt-phase3", methods=["POST"])
def api_qt_phase3():
    # All writes use tenant_token now — Created by SYSTEM column shows the
    # bot; the human-picked Created by goes into the CUSTOM 'QT Confirm
    # Create by' field via payload.created_by_open_id (set client-side).
    payload = request.get_json(silent=True) or {}
    record_id = payload.get("record_id")
    if not record_id:
        return jsonify({"ok": False, "error": "record_id required"}), 400

    existing = _count_existing_lines(record_id)
    if existing:
        return jsonify({
            "ok": False,
            "step": "phase3_safety_check",
            "error": "already_has_lines",
            "message": (f"QT นี้มี Line Items อยู่แล้ว {len(existing)} แถว — "
                        "ระบบไม่อนุญาตให้แก้ไขซ้ำ (ป้องกันการลบข้อมูลโดยไม่ตั้งใจ). "
                        "กรุณาแก้ไขใน Lark Base โดยตรง หรือสร้าง QT ใหม่."),
            "existing_line_count": len(existing),
        })

    all_fields, skipped = _build_parent_fields(payload, scope="all")
    upd_res, _ = _write_parent_with_retry(all_fields, record_id=record_id, method="PUT")
    if upd_res.get("code") != 0:
        # 1254043 = RecordIdNotFound — usually a stale localStorage draft
        # pointing at a parent record that no longer exists in Lark (deleted,
        # base swapped, or Phase 2 never actually persisted). Surface a clear
        # actionable message so the user can recover without confusion.
        if upd_res.get("code") == 1254043:
            return jsonify({
                "ok": False,
                "step": "phase3_patch_parent",
                "error": "stale_parent_record",
                "message": ("QT parent record ไม่พบใน Lark Base (อาจถูกลบ "
                            "หรือ draft เก่าใน localStorage ค้างอยู่). "
                            "กรุณาเคลียร์ draft แล้วเริ่มสร้าง QT ใหม่: "
                            "ใน Console (F12) พิมพ์ "
                            "`localStorage.clear(); location.reload()`."),
                "stale_record_id": record_id,
                "lark_response": upd_res,
            })
        return jsonify({"ok": False, "step": "phase3_patch_parent",
                        "error": upd_res, "skipped_invalid_options": skipped})

    # Use the logged-in user's Lark OAuth token if they have a session — user_token
    # may bypass field-level protections that tenant_token can't touch. Falls back
    # to tenant_token inside _create_lines if user_token lacks bitable scope.
    sess = get_session() or {}
    user_tok = sess.get("user_access_token")
    refresh_tok = sess.get("refresh_token")
    refreshed_session = None  # set if we minted a new token; written to cookie below
    clear_session_on_response = False  # set if refresh fails → user needs re-login
    # ALWAYS refresh proactively if we have a refresh_token. Lark
    # user_access_token TTL is ~2h but legacy sessions don't store
    # token_expires_at, so we can't trust the timestamp check alone.
    # Refresh is fast (~100ms) and safely idempotent; the saved
    # round-trip on the expired-token path is worth it.
    if refresh_tok:
        new_sess = refresh_user_token(refresh_tok)
        if new_sess:
            user_tok = new_sess["user_access_token"]
            # Preserve identity fields refresh response may omit
            new_sess["open_id"] = new_sess.get("open_id") or sess.get("open_id")
            new_sess["name"] = new_sess.get("name") or sess.get("name")
            new_sess["en_name"] = new_sess.get("en_name") or sess.get("en_name")
            new_sess["avatar_url"] = new_sess.get("avatar_url") or sess.get("avatar_url")
            refreshed_session = new_sess
            print(f"[token-refresh] ok — new exp={new_sess.get('token_expires_at')}")
        else:
            # refresh_token itself expired (TTL ~30 days) → drop user_tok so
            # _create_lines goes straight to tenant_token. Session cookie
            # will be cleared on the response so user gets re-prompted to
            # Lark login next time.
            print(f"[token-refresh] failed — refresh_token expired or invalid")
            user_tok = None
            clear_session_on_response = True  # force fresh login on next visit
    result = _create_lines(payload, record_id, user_token=user_tok)

    # Fetch latest Request No. for the response (1 quick GET, no polling — Vercel timeout)
    latest_request_no = ""
    try:
        chk = lark_request("GET",
            f"/open-apis/bitable/v1/apps/{BASE_APP_TOKEN}/tables/{TABLES['qt_mgmt']}/records/{record_id}",
            token=get_token())
        if chk.get("code") == 0:
            latest_request_no = text_val(chk["data"]["record"]["fields"].get("Request No."))
    except Exception:
        pass

    resp = make_response(jsonify({
        "ok": not result.get("line_errors"),
        "record_id": record_id,
        "request_no": latest_request_no,
        "deleted_old_lines": 0,
        "created_lines": result.get("created_lines", []),
        "line_errors": result.get("line_errors", []),
        "warnings": result.get("warnings", []),
        "skipped_invalid_options": skipped,
        "pdf_attached_to": None,
        "parent_fields_written": list(all_fields.keys()),
        "token_refreshed": bool(refreshed_session),
    }))
    # If we minted a fresh user_token during this submit, write it back to
    # the session cookie so the next request doesn't re-refresh.
    if refreshed_session:
        return set_session_cookie(resp, refreshed_session)
    # If refresh_token itself expired, clear the cookie so the frontend's
    # bootstrap auto-redirects to Lark login on the next page load.
    if clear_session_on_response:
        resp.set_cookie("sid", "", max_age=0, httponly=True, samesite="Lax")
    return resp


@app.route("/api/qt-lookup", methods=["POST"])
def api_qt_lookup():
    payload = request.get_json(silent=True) or {}
    q = (payload.get("qt_id") or payload.get("request_no") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "qt_id required"}), 400
    recs = fetch_all_records(TABLES["qt_mgmt"], None)
    match = None
    for r in recs:
        f = r.get("fields", {})
        if text_val(f.get("QT ID")) == q or text_val(f.get("Request No.")) == q:
            match = r
            break
    if not match:
        return jsonify({"ok": False, "error": f"QT '{q}' not found"}), 404
    parent_id = match["record_id"]
    return jsonify({
        "ok": True,
        "record_id": parent_id,
        "qt_id": text_val(match["fields"].get("QT ID")),
        "request_no": text_val(match["fields"].get("Request No.")),
        "data": _get_qt_full(parent_id),
    })


@app.route("/api/qt/<record_id>", methods=["GET"])
def api_qt_record(record_id):
    return jsonify(_get_qt_full(record_id))


@app.route("/api/draft-render", methods=["POST"])
def api_draft_render():
    # PDF generation is removed in the Vercel build (no headless Chrome).
    # The frontend preview pane still calls this on every change — return a
    # lightweight HTML placeholder so the side-pane stays clean instead of
    # showing errors. The user can preview the final document in Lark Base.
    return ('<html><body style="font-family:Sarabun,sans-serif;padding:40px;'
            'text-align:center;color:#666;line-height:1.6;">'
            '<div style="font-size:18px;margin-bottom:10px;">📄 Live Preview</div>'
            '<div>PDF preview ใช้งานได้ใน Lark Base โดยตรงหลัง Submit</div>'
            '<div style="margin-top:8px;font-size:12px;">'
            '(การ render PDF ถูกตัดออกในเวอร์ชัน Vercel เพื่อให้ deploy serverless ได้)'
            '</div></body></html>',
            200, {"Content-Type": "text/html; charset=utf-8"})

# ─── Static fallback (Vercel routes / → public/index.html directly) ──────────

@app.route("/", methods=["GET"])
def index():
    # On Vercel, index.html at repo root is auto-served — this handler only
    # runs for local dev (python3 api/index.py).
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return send_from_directory(here, "index.html")

# ─── Local dev only ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 3000)), debug=True)
