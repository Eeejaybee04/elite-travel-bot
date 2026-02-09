import os
import time
import secrets
import requests
from flask import Flask, request

app = Flask(__name__)

# =========================
# ENV VARIABLES (REQUIRED)
# =========================
PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN")

PRICE_FORM_URL = os.getenv("PRICE_FORM_URL", "")
GENERAL_FORM_URL = os.getenv("GENERAL_FORM_URL", "")
PAYMENT_FORM_URL = os.getenv("PAYMENT_FORM_URL", "")

# Must match Zoho Forms Field Alias exactly
FORM_REF_PARAM = os.getenv("FORM_REF_PARAM", "External_Lead_ID")

# =========================
# SIMPLE IN-MEMORY DEDUPE
# =========================
PROCESSED_MIDS = set()

# =========================
# HELPERS
# =========================
def generate_external_lead_id():
    ts = time.strftime("%Y%m%d-%H%M%S")
    rnd = secrets.token_hex(3).upper()
    return f"ET-{ts}-{rnd}"

def send_to_messenger(psid, message_payload):
    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN missing")
        return

    url = "https://graph.facebook.com/v18.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}

    requests.post(
        url,
        params=params,
        json={"recipient": {"id": psid}, "message": message_payload},
        timeout=10
    )

def send_text(psid, text):
    send_to_messenger(psid, {"text": text})

def build_form_link(base_url, external_lead_id):
    if not base_url:
        return ""
    joiner = "&" if "?" in base_url else "?"
    return f"{base_url}{joiner}{FORM_REF_PARAM}={external_lead_id}"

# =========================
# UI
# =========================
def show_main_menu(psid):
    payload = {
        "attachment": {
            "type": "template",
            "payload": {
                "template_type": "button",
                "text": "✈️ Elite Travel Limited\nHow can we help you today?",
                "buttons": [
                    {"type": "postback", "title": "Price Only", "payload": "INTENT_PRICE"},
                    {"type": "postback", "title": "General Enquiry", "payload": "INTENT_GENERAL"},
                    {"type": "postback", "title": "Payment Proof", "payload": "INTENT_PAYMENT"},
                ],
            },
        }
    }
    send_to_messenger(psid, payload)

def handle_intent(psid, intent):
    external_lead_id = generate_external_lead_id()

    if intent == "PRICE":
        link = build_form_link(PRICE_FORM_URL, external_lead_id)
        send_text(
            psid,
            f"✅ Price Only Request\n\nPlease complete this short form:\n{link}\n\nOur agent will reply shortly."
        )

    elif intent == "GENERAL":
        link = build_form_link(GENERAL_FORM_URL, external_lead_id)
        send_text(
            psid,
            f"✅ General Enquiry\n\nPlease complete this form:\n{link}\n\nOur team will assist you."
        )

    elif intent == "PAYMENT":
        link = build_form_link(PAYMENT_FORM_URL, external_lead_id)
        send_text(
            psid,
            f"✅ Upload Payment Proof\n\nPlease use this form:\n{link}\n\nWe’ll verify and proceed."
        )

    else:
        show_main_menu(psid)

# =========================
# WEBHOOK VERIFY
# =========================
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if token == VERIFY_TOKEN:
        return challenge

    return "Verification failed", 403

# =========================
# WEBHOOK RECEIVER (SAFE)
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json() or {}

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):

            # Sender
            psid = (event.get("sender") or {}).get("id")
            if not psid:
                continue

            # ---------------------------------
            # 1. HANDLE BUTTON CLICKS (POSTBACK)
            # ---------------------------------
            if "postback" in event:
                payload = (event["postback"] or {}).get("payload")

                if payload == "INTENT_PRICE":
                    handle_intent(psid, "PRICE")
                elif payload == "INTENT_GENERAL":
                    handle_intent(psid, "GENERAL")
                elif payload == "INTENT_PAYMENT":
                    handle_intent(psid, "PAYMENT")
                else:
                    show_main_menu(psid)

                continue  # IMPORTANT

            # ---------------------------------
            # 2. IGNORE NON-MESSAGE EVENTS
            # ---------------------------------
            if "message" not in event:
                continue

            message = event.get("message") or {}

            # Ignore bot echoes
            if message.get("is_echo"):
                continue

            # Deduplicate
            mid = message.get("mid")
            if mid and mid in PROCESSED_MIDS:
                continue
            if mid:
                PROCESSED_MIDS.add(mid)

            # ---------------------------------
            # 3. RESPOND ONLY TO USER TEXT
            # ---------------------------------
            text = (message.get("text") or "").strip()
            if not text:
                continue

            # Show menu ONCE
            show_main_menu(psid)

    return "OK", 200

# =========================
# LOCAL RUN (OPTIONAL)
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
