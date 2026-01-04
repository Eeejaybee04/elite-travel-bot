# app_conversational.py
import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

from flask import Flask, request, jsonify
import requests

from zoho_auth import get_access_token
from pricing_rules import compute_pricing
PROCESSED_MIDS = set()
from typing import Dict, Any, Optional
import requests

app = Flask(__name__)

# =========================================================
# CONFIG (Render Environment Variables)
# =========================================================
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "REPLACE_ME")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "REPLACE_ME")

ZOHO_BASE = os.getenv("ZOHO_BIGIN_BASE", "https://www.zohoapis.com/bigin/v2")

CONVENIENCE_FEE_PCT = float(os.getenv("CONVENIENCE_FEE_PCT", "0.088"))
COMMISSION_MAP = {"626": 0.025, "656": 0.05}  # Air Niugini, PNG Air

# Optional: comma-separated Zoho user IDs for owner assignment
# Example: ZOHO_AGENT_IDS="4553...111,4553...222"
AGENT_IDS = [x.strip() for x in os.getenv("ZOHO_AGENT_IDS", "").split(",") if x.strip()]

# In-memory sessions (use Redis later if you want persistence across restarts)
SESSIONS: Dict[str, Dict[str, Any]] = {}

# =========================================================
# HEALTH / TEST ROUTES
# =========================================================
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

@app.route("/zoho/token-check", methods=["GET"])
def zoho_token_check():
    token = get_access_token()
    return jsonify({"token_loaded": bool(token)})

# =========================================================
# MESSENGER SEND API (real send)
# =========================================================
def _messenger_post(payload: dict) -> None:
    print(f"[Messenger SEND] {json.dumps(payload, indent=2)}")

    if not PAGE_ACCESS_TOKEN or PAGE_ACCESS_TOKEN == "REPLACE_ME":
        print("[Messenger WARN] FB_PAGE_ACCESS_TOKEN not set. Not sending to Facebook.")
        return

    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code >= 400:
            print(f"[Messenger ERROR] {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[Messenger EXCEPTION] {str(e)}")

def send_message(psid: str, text: str) -> None:
    payload = {"recipient": {"id": psid}, "message": {"text": text}}
    _messenger_post(payload)

# =========================================================
# ZOHO HELPERS
# =========================================================
def zoho_headers():
    return {
        "Authorization": f"Zoho-oauthtoken {get_access_token()}",
        "Content-Type": "application/json"
    }

def split_name(full_name: str) -> Tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", "Unknown"
    if len(parts) == 1:
        return "", parts[0]  # Zoho requires Last_Name
    return " ".join(parts[:-1]), parts[-1]

def find_contact_by_phone(phone: str) -> Optional[dict]:
    # Bigin uses Contacts, not Leads
    url = f"{ZOHO_BASE}/Contacts/search"
    params = {"criteria": f"(Mobile:equals:{phone})"}
    r = requests.get(url, headers=zoho_headers(), params=params, timeout=20)

    if r.status_code == 200:
        j = r.json()
        if j.get("data"):
            return j["data"][0]
    return None


def upsert_contact(full_name: str, phone: str, psid: str) -> str:
    existing = find_contact_by_phone(phone)
    first, last = split_name(full_name)

    payload = {
        "First_Name": first,
        "Last_Name": last or "Unknown",   # required
        "Mobile": phone,
        "Description": f"Messenger PSID: {psid}"
    }

    if existing:
        contact_id = existing["id"]
        url = f"{ZOHO_BASE}/Contacts/{contact_id}"
        r = requests.put(url, headers=zoho_headers(), json={"data": [payload]}, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"Failed to update contact: {r.status_code} {r.text}")
        return contact_id

    url = f"{ZOHO_BASE}/Contacts"
    r = requests.post(url, headers=zoho_headers(), json={"data": [payload]}, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Failed to create contact: {r.status_code} {r.text}")

    return r.json()["data"][0]["details"]["id"]

def _pick_agent_id(psid: str) -> Optional[str]:
    if not AGENT_IDS:
        return None
    # stable assignment for a given psid
    idx = sum(psid.encode("utf-8")) % len(AGENT_IDS)
    return AGENT_IDS[idx]

def create_deal(
    contact_id: str,
    trip: dict,
    booking_ref: str,
    owner_id: Optional[str] = None,
    estimate_low: Optional[float] = None,
    estimate_high: Optional[float] = None,
    estimate_source: str = "Amadeus Self-Service API",
) -> str:
    deal_name = f'{trip["origin"]}-{trip["destination"]} | {trip["depart_date"]} | {booking_ref}'

    description_lines = [
        f"Booking Ref: {booking_ref}",
        f"Route: {trip['origin']}-{trip['destination']}",
        f"Depart: {trip['depart_date']}",
        f"Return: {trip.get('return_date', 'One-way')}",
        f"PAX: A{trip['adults']} C{trip['children']} I{trip['infants']}",
        f"Preferred Airline: {trip.get('airline_pref') or 'Any'}",
        f"Source: Messenger",
    ]

    if estimate_low is not None and estimate_high is not None:
        description_lines += [
            "",
            f"ESTIMATE RANGE (incl. fees): {estimate_low:.2f} - {estimate_high:.2f}",
            f"Estimate Source: {estimate_source}",
        ]

    description = "\n".join(description_lines)

    payload: Dict[str, Any] = {
        "Deal_Name": deal_name,
        "Pipeline": "Flight Booking",
        "Stage": "New Lead",
        "Amount": 0,  # keep 0 until agent confirms final price
        "Contact_Name": {"id": contact_id},
        "Description": description,
    }

    if owner_id:
        payload["Owner"] = {"id": owner_id}

    # ✅ If you create custom fields in Bigin Deals, you can store estimates properly:
    # Example API names (YOU must replace with your real API field names):
    # payload["Estimated_Low"] = estimate_low
    # payload["Estimated_High"] = estimate_high

    url = f"{ZOHO_BASE}/Deals"
    r = requests.post(url, headers=zoho_headers(), json={"data": [payload]}, timeout=20)

    if r.status_code >= 400:
        raise RuntimeError(f"Failed to create deal: {r.status_code} {r.text}")

    j = r.json()
    return j["data"][0]["details"]["id"]

# =========================================================
# PRICING (stub now -> swap with Amadeus scraper later)
# =========================================================
def fetch_exact_price(origin: str, dest: str, depart_date: str, return_date: Optional[str],
                      airline_pref: Optional[str], pax: Dict[str, int]) -> Dict[str, Any]:
    base_total_doc_adult = 800.0
    tax_adult = 200.0

    adults = pax.get("adults", 1)
    total_doc = base_total_doc_adult * adults
    tax_total = tax_adult * adults
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

def generate_booking_ref() -> str:
    return f"ET-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

def process_booking_flow(psid: str, session: dict) -> dict:
    booking_ref = generate_booking_ref()

    try:
        contact_id = upsert_contact(full_name=session["name"], phone=session["phone"], psid=psid)

        owner_id = _pick_agent_id(psid)
        deal_id = create_deal(contact_id=contact_id, trip=session, booking_ref=booking_ref, owner_id=owner_id)

        add_deal_note(deal_id, f"Messenger session:\n{json.dumps(session, indent=2)}")

        pax = {"adults": session["adults"], "children": session["children"], "infants": session["infants"]}
        raw = fetch_exact_price(
            origin=session["origin"],
            dest=session["destination"],
            depart_date=session["depart_date"],
            return_date=session.get("return_date"),
            airline_pref=session.get("airline_pref"),
            pax=pax
        )

        priced = compute_pricing(raw, convenience_fee_pct=CONVENIENCE_FEE_PCT, commission_map=COMMISSION_MAP)

        quote_total = (
            priced.get("TOTAL_CUSTOMER")
            or priced.get("TOTAL")
            or priced.get("GRAND_TOTAL")
            or raw.get("TOTAL_DOC")
        )

        breakdown_text = priced.get("BREAKDOWN_TEXT") or json.dumps(priced, indent=2)

        upd = update_deal(deal_id, {
            "Amount": float(quote_total) if quote_total is not None else 0,
            "Stage": "Details Collected",
            "Description": (
                f"Booking Ref: {booking_ref}\n"
                f"Quoted Total: {quote_total}\n\n"
                f"{breakdown_text}"
            )
        })
        if upd["status_code"] >= 400:
            print("[Zoho Deal Update ERROR]", upd)

        return {
            "ok": True,
            "booking_ref": booking_ref,
            "lead_id": lead_id,
            "deal_id": deal_id,
            "quote_total": quote_total,
            "assigned_owner": owner_id
        }

    except Exception as e:
        return {"ok": False, "error": str(e), "booking_ref": booking_ref}

# =========================================================
# SIMPLE BOOKING QUESTION FLOW (Messenger -> session)
# =========================================================
def _init_session() -> dict:
    return {
        "step": "name",
        "name": None,
        "phone": None,
        "origin": None,
        "destination": None,
        "depart_date": None,
        "return_date": None,
        "adults": 1,
        "children": 0,
        "infants": 0,
        "airline_pref": None,  # "626" or "656" or None
    }

def handle_message(psid: str, text: str) -> None:
    session = SESSIONS.get(psid)

    # Start a new flow if none exists or user says start-like words
    if session is None or text.strip().lower() in ("start", "book", "booking", "flight", "hi", "hello"):
        session = _init_session()
        SESSIONS[psid] = session
        send_message(psid, "Hi! ✈️ Let’s book your flight.\nWhat’s your full name?")
        return

    step = session.get("step")

    if step == "name":
        session["name"] = text.strip()
        session["step"] = "phone"
        send_message(psid, "Thanks! What’s your phone number (WhatsApp/mobile)?")
        return

    if step == "phone":
        session["phone"] = text.strip()
        session["step"] = "origin"
        send_message(psid, "Origin airport/city code? (e.g., POM)")
        return

    if step == "origin":
        session["origin"] = text.strip().upper()
        session["step"] = "destination"
        send_message(psid, "Destination airport/city code? (e.g., LAE)")
        return

    if step == "destination":
        session["destination"] = text.strip().upper()
        session["step"] = "depart_date"
        send_message(psid, "Departure date? (YYYY-MM-DD)")
        return

    if step == "depart_date":
        session["depart_date"] = text.strip()
        session["step"] = "return_date"
        send_message(psid, "Return date? (YYYY-MM-DD) or type ONE-WAY")
        return

    if step == "return_date":
        t = text.strip().lower()
        session["return_date"] = None if t in ("one-way", "oneway", "one way") else text.strip()
        session["step"] = "adults"
        send_message(psid, "How many adults? (number)")
        return

    if step == "adults":
        session["adults"] = int(text) if text.isdigit() else 1
        session["step"] = "children"
        send_message(psid, "Children? (number)")
        return

    if step == "children":
        session["children"] = int(text) if text.isdigit() else 0
        session["step"] = "infants"
        send_message(psid, "Infants? (number)")
        return

    if step == "infants":
        session["infants"] = int(text) if text.isdigit() else 0
        session["step"] = "airline"
        send_message(psid, "Preferred airline? Reply:\n1) Air Niugini (PX)\n2) PNG Air (CG)\n3) Any")
        return

    if step == "airline":
        t = text.strip().lower()
        if t in ("1", "px") or "niugini" in t or "air niugini" in t:
            session["airline_pref"] = "626"
        elif t in ("2", "cg") or "png air" in t or "png" in t:
            session["airline_pref"] = "656"
        else:
            session["airline_pref"] = None

        send_message(psid, "✅ Got it. Saving your request and preparing an estimate…")
        result = process_booking_flow(psid, session)

        if result["ok"]:
            send_message(
                psid,
                f"✅ Request logged!\n"
                f"Booking Ref: {result['booking_ref']}\n"
                f"Estimated Total: {result['quote_total']}\n"
                f"Our consultant will contact you shortly."
            )
        else:
            send_message(
                psid,
                f"⚠️ Sorry—something went wrong.\nRef: {result.get('booking_ref')}\n"
                f"Please type START and try again."
            )
            print("[BOOKING FLOW ERROR]", result)

        SESSIONS.pop(psid, None)
        return

    send_message(psid, "Type START to begin a new flight booking request.")

# =========================================================
# MESSENGER WEBHOOK ROUTES
# =========================================================
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    # Meta verification
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification token mismatch", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    print("[WEBHOOK POST] received")

    # Messenger events
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            psid = event.get("sender", {}).get("id")
            msg = event.get("message", {})

            if not psid:
                continue

            # ✅ de-dupe
            mid = msg.get("mid")
            if mid:
                if mid in PROCESSED_MIDS:
                    continue
                PROCESSED_MIDS.add(mid)

            text = (msg.get("text") or "").strip()
            if text:
                handle_message(psid, text)

    return "EVENT_RECEIVED", 200
