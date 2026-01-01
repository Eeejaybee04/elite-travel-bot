import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from zoho_auth import get_access_token


from flask import Flask, request, jsonify
import requests

from pricing_rules import compute_pricing

app = Flask(__name__)
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})
@app.route("/zoho/token-check", methods=["GET"])
def zoho_token_check():
    token = get_access_token()
    return jsonify({"token_loaded": bool(token)})



# ---------------- CONFIG ----------------
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "REPLACE_ME")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "REPLACE_ME")

ZOHO_ACCESS_TOKEN = os.getenv("ZOHO_ACCESS_TOKEN", "REPLACE_ME")
ZOHO_BASE = os.getenv("ZOHO_BIGIN_BASE", "https://www.zohoapis.com/bigin/v2")
ZOHO_MODULE = os.getenv("ZOHO_BIGIN_MODULE", "Leads")

CONVENIENCE_FEE_PCT = 0.088
COMMISSION_MAP = {"626": 0.025, "656": 0.05}

# In-memory stores (replace with Redis for production)
SESSIONS: Dict[str, Dict[str, Any]] = {}
QUOTES: Dict[str, Dict[str, Any]] = {}

# ---------------- HELPERS ----------------
def send_message(psid: str, text: str, quick_replies: Optional[list] = None) -> None:
    payload = {"recipient": {"id": psid}, "message": {"text": text}}
    if quick_replies:
        payload["message"]["quick_replies"] = quick_replies
    print(f"[Messenger-> {psid}] {json.dumps(payload, indent=2)}")
    # Real call:
    # url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    # requests.post(url, json=payload, timeout=15)


def send_quick_replies(psid: str, text: str, replies: Dict[str, str]) -> None:
    qrs = [
        {"content_type": "text", "title": title[:20], "payload": payload}
        for title, payload in replies.items()
    ]
    send_message(psid, text, quick_replies=qrs)

def create_bigin_record(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{ZOHO_BASE}/{ZOHO_MODULE}"
    body = {"data": [payload]}

    def _headers():
        return {
            "Authorization": f"Zoho-oauthtoken {get_access_token()}",
            "Content-Type": "application/json"
        }

    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=20)

        # If token expired mid-request, refresh & retry once
        if resp.status_code in (401, 403):
            print("[Zoho] Token rejected, retrying once with fresh token")
            resp = requests.post(url, headers=_headers(), json=body, timeout=20)

        # Try to parse JSON safely
        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": resp.text}

        if resp.status_code >= 400:
            print(f"[Zoho ERROR] {resp.status_code}: {data}")

        return {
            "status_code": resp.status_code,
            "body": data
        }

    except requests.RequestException as e:
        print(f"[Zoho EXCEPTION] {str(e)}")
        return {
            "status_code": 0,
            "error": str(e)
        }



def fetch_exact_price(origin: str, dest: str, depart_date: str, return_date: Optional[str],
                      airline_pref: Optional[str], pax: Dict[str, int]) -> Dict[str, Any]:
    """
    Hook to your scraper/API. Replace this stub with Amadeus scraper/Enterprise API.
    """
    base_total_doc_adult = 800.0
    tax_adult = 200.0
    total_doc = base_total_doc_adult * pax.get("adults", 1)
    tax_total = tax_adult * pax.get("adults", 1)
    airline_code = airline_pref or "626"
    return {
        "AIRLINE": airline_code,
        "TOTAL_DOC": round(total_doc, 2),
        "TAX": round(tax_total, 2),
        "FEE": 0.0,
        "ROUTE": f"{origin}-{dest}",
        "DEPART_DATE": depart_date,
        "RETURN_DATE": return_date
    }
