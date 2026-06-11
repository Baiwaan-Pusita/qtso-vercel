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

BASE_APP_TOKEN = "HZwsbAdIHabtqXspWLDlNryUg1e"
LARK_HOST = "https://open.larksuite.com"
LARK_AUTH_HOST = "https://accounts.larksuite.com"

TABLES = {
    "qt_mgmt":        "tbllrHviruBy5ltH",
    "qtso_detail":    "tbl01rn7UYG0Gl7w",
    "brands_company": "tblB5r3geCkdI8YH",
    "customer":       "tblnpZe52qwky9U2",
    "item_code":      "tblUTOBmBulfRriq",
    "employee":       "tbl2ReQCQvCc7rg1",
    "project":        "tbl7Vd0aRxXXCx1l",
}

def _redirect_url() -> str:
    # Vercel sets VERCEL_URL = e.g. "qtso-app.vercel.app" on every deploy.
    # Use it so OAuth redirect matches the live hostname automatically.
    explicit = os.environ.get("LARK_OAUTH_REDIRECT")
    if explicit:
        return explicit
    if os.environ.get("VERCEL_URL"):
        return f"https://{os.environ['VERCEL_URL']}/api/auth/lark/callback"
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
    recs = fetch_all_records(TABLES["item_code"],
                             ["Item Name", "Item", "Item Code", "Item for selection",
                              "Document Type", "BU", "Description TH",
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
        out.append({
            "record_id": r["record_id"], "item": item,
            "item_code": text_val(f.get("Item Code")),
            "selection": text_val(f.get("Item for selection")),
            "doc_type": text_val(f.get("Document Type")),
            "bu": text_val(f.get("BU")),
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

# ─── Static fallback (Vercel routes / → public/index.html directly) ──────────

@app.route("/", methods=["GET"])
def index():
    # Vercel routes "/" to public/index.html via vercel.json — but local dev
    # (e.g. `flask run`) hits this handler. Serve the HTML from the public dir.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return send_from_directory(os.path.join(here, "public"), "index.html")

# ─── Local dev only ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 3000)), debug=True)
