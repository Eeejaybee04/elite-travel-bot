import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from flask import Flask, request, jsonify
import requests

from zoho_auth import get_access_token
from pricing_rules import compute_pricing

app = Flask(__name__)

# =========================================================
# HEALTH & TEST ROUTES
# =========================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

@app.route("/zoho/token-check", methods=["GET"])
def zoho_token_check():
    token = get_access_token()
    return jsonify({"token_loaded": bool(token)})

# =========================================================
# CONFIG
# =========================================================

PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "REPLACE_ME")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "REPLACE_ME")

ZOHO_BASE = os.getenv("ZOHO_BIGIN_BASE", "https://www.zohoapis.com/bigin/v2")

CONVENIENCE_FEE_PCT = 0.088
COMMISSION_MAP = {"626": 0.025, "656": 0.05}

# In-memory stores (Redis later)
SESSIONS: Dict[str, Dict[str, Any]] = {}
QUOTES: Dict[str, Dict[str, Any]] = {}

# =========================================================
# MESSENGER HELPERS
# =========================================================

def send_message(psid: str, text: str) -> None:
    payload = {"recipient": {"id": psid}, "message": {"text": text}}
    print(f"[Messenger -> {psid}] {json.dumps(payload, indent=2)}")
    # Uncomment when ready
    # url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    # requests.post(url, json=payload, timeout=15)

# =========================================================
# ZOHO HELPERS
# =========================================================

def zoho_headers():
    return {
        "Authorization": f"Zoho-oauthtoken {get_access_token()}",
        "Content-Type": "application/json"
    }

# ---------- LEADS ----------

def find_lead_by_phone(phone: str) -> Optional[dict]:
    url = f"{ZOHO_BASE}/Leads/search"
    params = {"criteria": f"(Mobile:equals:{phone})"}
    r = requests.get(url, headers=zoho_headers(), params=params, timeout=20)
    if r.status_code == 200 and r.json().get("data"):
        return r.json()["data"][0]
    return None

def split_name(full_name: str):
    parts = full_name.strip().split()
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]

def upsert_lead(full_name: str, phone: str, psid: str) -> str:
    existing = find_lead_by_phone(phone)
    first, last = split_name(full_name)

    payload = {
        "First_Name": first,
        "Last_Name": last or "Unknown",
        "Mobile": phone,
        "Lead_Source": "Facebook Messenger",
        "Description": f"Messenger PSID: {psid}"
    }

    if existing:
        lead_id = existing["id"]
        url = f"{ZOHO_BASE}/Leads/{lead_id}"
        requests.put(url, headers=zoho_headers(), json={"data": [payload]}, timeout=20)
        return lead_id

    url = f"{ZOHO_BASE}/Leads"
    r = requests.post(url, headers=zoho_headers(), json={"data": [payload]}, timeout=20)
    return r.json()["data"][0]["details"]["id"]

# ---------- DEALS ----------

def create_deal(lead_id: str, trip: dict, booking_ref: str) -> str:
    deal_name = f'{trip["origin"]}-{trip["destination"]} | {trip["depart_date"]} | {booking_ref}'

    description = (
        f"Booking Ref: {booking_ref}\n"
        f"Route: {trip['origin']}-{trip['destination']}\n"
        f"Depart: {trip['depart_date']}\n"
        f"Return: {trip.get('return_date', 'One-way')}\n"
        f"PAX: A{trip['adults']} C{trip['children']} I{trip['infants']}\n"
        f"Source: Messenger"
    )

    payload = {
        "Deal_Name": deal_name,
        "Pipeline": "Flight Booking",
        "Stage": "New Lead",
        "Amount": 0,
        "Contact_Name": {"id": lead_id},
        "Description": description
    }

    url = f"{ZOHO_BASE}/Deals"
    r = requests.post(url, headers=zoho_headers(), json={"data": [payload]}, timeout=20)

    if r.status_code >= 400:
        print("[Zoho Deal ERROR]", r.text)
        raise RuntimeError("Failed to create deal")

    return r.json()["data"][0]["details"]["id"]

# ---------- NOTES ----------

def add_deal_note(deal_id: str, note_text: str):
    payload = {
        "Note_Title": "Messenger Enquiry",
        "Note_Content": note_text,
        "Parent_Id": deal_id,
        "se_module": "Deals"
    }
    url = f"{ZOHO_BASE}/Notes"
    requests.post(url, headers=zoho_headers(), json={"data": [payload]}, timeout=20)

# =========================================================
# BOOKING FLOW (CALL THIS FROM MESSENGER)
# =========================================================

def generate_booking_ref():
    return f"ET-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

def create_lead_and_deal(psid: str, session: dict) -> dict:
    booking_ref = generate_booking_ref()

    lead_id = upsert_lead(
        full_name=session["name"],
        phone=session["phone"],
        psid=psid
    )

    deal_id = create_deal(
        lead_id=lead_id,
        trip=session,
        booking_ref=booking_ref
    )

    add_deal_note(
        deal_id,
        f"Messenger session data:\n{json.dumps(session, indent=2)}"
    )

    return {
        "ok": True,
        "booking_ref": booking_ref,
        "lead_id": lead_id,
        "deal_id": deal_id
    }
