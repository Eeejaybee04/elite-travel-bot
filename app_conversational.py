# app_conversational.py
import os
import json
import uuid
from datetime import datetime, date
from typing import Dict, Any, Optional, Tuple

from flask import Flask, request, jsonify
import requests

from zoho_auth import get_access_token

app = Flask(__name__)

# =========================================================
# CONFIG
# =========================================================
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN")
ZOHO_BASE = os.getenv("ZOHO_BIGIN_BASE", "https://www.zohoapis.com/bigin/v2")
ZOHO_WEBHOOK_KEY = os.getenv("ZOHO_WEBHOOK_KEY", "CHANGE_ME")

AGENT_IDS = [x.strip() for x in os.getenv("ZOHO_AGENT_IDS", "").split(",") if x.strip()]

# Bigin stages (MATCH YOUR PIPELINE)
STAGE_NEW = "New Lead"
STAGE_DETAILS = "Details Collected"
STAGE_PENDING = "Pending Booking"
STAGE_CONFIRMED = "Confirmed Booking"
STAGE_WON = "Closed Won"
STAGE_LOST = "Closed Lost"

SESSIONS: Dict[str, Dict[str, Any]] = {}
LAST_DEAL_BY_PSID: Dict[str, str] = {}

PROCESSED_MIDS = set()

# =========================================================
# UTILITIES
# =========================================================
def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except Exception:
        return None

def _age_on(travel: date, dob: date) -> float:
    return (travel - dob).days / 365.25

def _is_infant(travel: date, dob: date) -> bool:
    return 0 <= _age_on(travel, dob) <= 2

def _is_child(travel: date, dob: date) -> bool:
    return 2 <= _age_on(travel, dob) < 12

# =========================================================
# MESSENGER SEND
# =========================================================
def send_message(psid: str, text: str):
    payload = {
        "recipient": {"id": psid},
        "message": {"text": text}
    }
    if not PAGE_ACCESS_TOKEN:
        print("[Messenger]", text)
        return
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        json=payload,
        timeout=10
    )

# =========================================================
# ZOHO HELPERS
# =========================================================
def zoho_headers():
    return {
        "Authorization": f"Zoho-oauthtoken {get_access_token()}",
        "Content-Type": "application/json"
    }

def split_name(name: str) -> Tuple[str, str]:
    parts = name.split()
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]

def upsert_contact(name: str, phone: str, psid: str) -> str:
    first, last = split_name(name)
    payload = {
        "First_Name": first,
        "Last_Name": last or "Unknown",
        "Mobile": phone,
        "Description": f"Messenger PSID: {psid}"
    }
    r = requests.post(f"{ZOHO_BASE}/Contacts", headers=zoho_headers(), json={"data": [payload]})
    return r.json()["data"][0]["details"]["id"]

def pick_agent(psid: str) -> Optional[str]:
    if not AGENT_IDS:
        return None
    return AGENT_IDS[sum(psid.encode()) % len(AGENT_IDS)]

def create_deal(contact_id: str, session: dict) -> str:
    ref = f"ET-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}"
    payload = {
        "Deal_Name": f"{session['origin']}-{session['destination']} | {ref}",
        "Pipeline": "Flight Booking",
        "Stage": STAGE_NEW,
        "Amount": 0,
        "Contact_Name": {"id": contact_id},
        "Description": json.dumps(session, indent=2)
    }
    owner = pick_agent(session["psid"])
    if owner:
        payload["Owner"] = {"id": owner}

    r = requests.post(f"{ZOHO_BASE}/Deals", headers=zoho_headers(), json={"data": [payload]})
    return r.json()["data"][0]["details"]["id"]

def update_deal(deal_id: str, data: dict):
    requests.put(
        f"{ZOHO_BASE}/Deals/{deal_id}",
        headers=zoho_headers(),
        json={"data": [data]},
        timeout=10
    )

# =========================================================
# SESSION INIT
# =========================================================
def init_session(psid: str) -> dict:
    return {
        "psid": psid,
        "step": "intent",
        "intent": None,
        "name": None,
        "name_confirmed": False,
        "phone": None,
        "origin": None,
        "destination": None,
        "depart_date": None,
        "return_date": None,
        "total_pax": None,
        "adults": 1,
        "children": 0,
        "infants": 0,
        "students": 0,
        "child_dobs": [],
        "infant_dobs": [],
        "student_id_confirmed": None,
        "airline_pref": None
    }

# =========================================================
# MAIN CHAT FLOW
# =========================================================
def handle_message(psid: str, text: str):
    t = text.lower().strip()

    # COMMANDS
    if t == "cancel":
        deal = LAST_DEAL_BY_PSID.get(psid)
        if deal:
            update_deal(deal, {"Stage": STAGE_LOST})
        send_message(psid, "❌ Cancelled. Type START to begin again.")
        SESSIONS.pop(psid, None)
        return

    if t == "book":
        send_message(psid, "✅ Great. Continuing with booking.")
        if psid in SESSIONS:
            SESSIONS[psid]["intent"] = "booking"
        return

    # START
    if psid not in SESSIONS or t in ("start", "hi", "hello"):
        SESSIONS[psid] = init_session(psid)
        send_message(psid, "Hi ✈️\n1️⃣ Get prices\n2️⃣ Book a flight\nReply 1 or 2")
        return

    s = SESSIONS[psid]
    step = s["step"]

    if step == "intent":
        s["intent"] = "price" if t == "1" else "booking"
        s["step"] = "destination"
        send_message(psid, "Destination? (e.g. LAE)")
        return

    if step == "destination":
        s["destination"] = text.upper()
        s["step"] = "origin"
        send_message(psid, "Flying from? (e.g. POM)")
        return

    if step == "origin":
        s["origin"] = text.upper()
        s["step"] = "depart_date"
        send_message(psid, "Travel date? YYYY-MM-DD")
        return

    if step == "depart_date":
        d = _parse_date(text)
        if not d:
            send_message(psid, "Invalid date. Use YYYY-MM-DD")
            return
        s["depart_date"] = d.isoformat()
        s["step"] = "trip_type"
        send_message(psid, "ONE-WAY or RETURN?")
        return

    if step == "trip_type":
        if "return" in t:
            s["step"] = "return_date"
            send_message(psid, "Return date YYYY-MM-DD")
        else:
            s["return_date"] = None
            s["step"] = "name" if s["intent"] == "booking" else "total_pax"
            send_message(psid, "Full name as on ID?") if s["intent"] == "booking" else send_message(psid, "Total passengers?")
        return

    if step == "return_date":
        rd = _parse_date(text)
        if not rd or rd <= _parse_date(s["depart_date"]):
            send_message(psid, "Return must be after departure.")
            return
        s["return_date"] = rd.isoformat()
        s["step"] = "name" if s["intent"] == "booking" else "total_pax"
        send_message(psid, "Full name as on ID?") if s["intent"] == "booking" else send_message(psid, "Total passengers?")
        return

    if step == "name":
        s["name"] = text.strip()
        s["step"] = "name_confirm"
        send_message(psid, f"Confirm name EXACTLY as on ID:\n{s['name']}\nReply YES or NO")
        return

    if step == "name_confirm":
        if t == "yes":
            s["name_confirmed"] = True
            s["step"] = "phone"
            send_message(psid, "Phone number?")
        else:
            s["step"] = "name"
            send_message(psid, "Re-enter full name EXACTLY as on ID.")
        return

    if step == "phone":
        s["phone"] = text.strip()
        s["step"] = "total_pax"
        send_message(psid, "Total passengers?")
        return

    if step == "total_pax":
        if not text.isdigit():
            send_message(psid, "Enter a number.")
            return
        s["total_pax"] = int(text)
        s["step"] = "adults"
        send_message(psid, "Adults?")
        return

    if step == "adults":
        s["adults"] = int(text) if text.isdigit() else 1
        s["step"] = "children"
        send_message(psid, "Children (2–11)?")
        return

    if step == "children":
        s["children"] = int(text) if text.isdigit() else 0
        s["step"] = "infants"
        send_message(psid, "Infants (0–2)?")
        return

    if step == "infants":
        s["infants"] = int(text) if text.isdigit() else 0
        s["step"] = "students"
        send_message(psid, "Students?")
        return

    if step == "students":
        s["students"] = int(text) if text.isdigit() else 0

        # DOB loops
        if s["children"] > 0:
            s["step"] = "child_dob"
            send_message(psid, "Child DOB #1 YYYY-MM-DD")
            return
        if s["infants"] > 0:
            s["step"] = "infant_dob"
            send_message(psid, "Infant DOB #1 YYYY-MM-DD")
            return

        s["step"] = "airline"
        send_message(psid, "Airline: 1 PX / 2 PNG Air / 3 Any")
        return

    if step == "child_dob":
        dob = _parse_date(text)
        if not dob or not _is_child(_parse_date(s["depart_date"]), dob):
            send_message(psid, "Invalid child DOB.")
            return
        s["child_dobs"].append(dob.isoformat())
        if len(s["child_dobs"]) < s["children"]:
            send_message(psid, f"Child DOB #{len(s['child_dobs'])+1}")
            return
        s["step"] = "infant_dob" if s["infants"] else "airline"
        send_message(psid, "Infant DOB #1 YYYY-MM-DD") if s["infants"] else send_message(psid, "Airline?")
        return

    if step == "infant_dob":
        dob = _parse_date(text)
        if not dob or not _is_infant(_parse_date(s["depart_date"]), dob):
            send_message(psid, "Invalid infant DOB.")
            return
        s["infant_dobs"].append(dob.isoformat())
        if len(s["infant_dobs"]) < s["infants"]:
            send_message(psid, f"Infant DOB #{len(s['infant_dobs'])+1}")
            return
        s["step"] = "airline"
        send_message(psid, "Airline?")
        return

    if step == "airline":
        s["airline_pref"] = "PX" if t == "1" else "CG" if t == "2" else None

        # CREATE RECORDS
        contact_id = upsert_contact(s["name"] or "Price Enquiry", s["phone"], psid)
        deal_id = create_deal(contact_id, s)
        LAST_DEAL_BY_PSID[psid] = deal_id
        update_deal(deal_id, {"Stage": STAGE_DETAILS})

        send_message(
            psid,
            "✅ Request received.\n"
            "Our consultant will check the live fare in Amadeus and update you shortly.\n"
            "Reply BOOK to proceed, or CANCEL to stop."
        )

        SESSIONS.pop(psid, None)
        return

# =========================================================
# WEBHOOKS
# =========================================================
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Invalid token", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json() or {}
    for entry in data.get("entry", []):
        for m in entry.get("messaging", []):
            psid = m.get("sender", {}).get("id")
            msg = m.get("message", {})
            mid = msg.get("mid")
            if mid and mid in PROCESSED_MIDS:
                continue
            if mid:
                PROCESSED_MIDS.add(mid)
            if psid and msg.get("text"):
                handle_message(psid, msg["text"])
    return "OK", 200
