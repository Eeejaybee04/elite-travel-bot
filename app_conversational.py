import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from flask import Flask, request, jsonify
import requests

from pricing_rules import compute_pricing

app = Flask(__name__)
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


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
    headers = {
        "Authorization": f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    body = {"data": [payload]}
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=20)
        return {"status_code": resp.status_code, "body": resp.json()}
    except Exception as e:
        return {"status_code": 0, "error": str(e)}


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
