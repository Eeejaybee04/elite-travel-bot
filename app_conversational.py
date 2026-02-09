import os
import time
import secrets
import requests
from flask import Flask, request

app = Flask(__name__)

PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN")

PRICE_FORM_URL = os.getenv("PRICE_FORM_URL", "")
GENERAL_FORM_URL = os.getenv("GENERAL_FORM_URL", "")
PAYMENT_FORM_URL = os.getenv("PAYMENT_FORM_URL", "")

FORM_REF_PARAM = os.getenv("FORM_REF_PARAM", "External_Lead_ID")

# --- Anti-spam controls ---
PROCESSED_MIDS = set()
LAST_REPLY_AT = {}          # psid -> timestamp
REPLY_THROTTLE_SECONDS = int(os.getenv("REPLY_THROTTLE_SECONDS", "10"))


def now_ts() -> float:
    return time.time()


def throttle_ok(psid: str) -> bool:
    """Allow replying at most once per REPLY_THROTTLE_SECONDS per PSID."""
    t = now_ts()
    last = LAST_REPLY_AT.get(psid, 0)
    if t - last < REPLY_THROTTLE_SECONDS:
        return False
    LAST_REPLY_AT[psid] = t
    return True


def generate_external_lead_id() -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    rnd = secrets.token_hex(3).upper()
    return f"ET-{ts}-{rnd}"


def send_to_messenger(psid: str, message_payload: dict):
    if not PAGE_ACCESS_TOKEN:
        return
    url = "https://graph.facebook.com/v18.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}
    requests.post(
        url,
        params=params,
        json={"recipient": {"id": psid}, "message": message_payload},
        timeout=10
    )


def send_text(psid: str, text: str):
    send_to_messenger(psid, {"text": text})


def build_form_link(base_url: str, external_lead_id: str) -> str:
    if not base_url:
        return ""
    joiner = "&" if "?" in base_url else "?"
    return f"{base_url}{joiner}{FORM_REF_PARAM}={external_lead_id}"


def show_main_menu(psid: str):
    # throttle menu too (prevents floods)
    if not throttle_ok(psid):
        return

    payload = {
        "attachment": {
            "type": "template",
            "payload": {
                "template_type": "button",
                "text": "✈️ Elite Travel Limited\nPlease choose an option:",
                "buttons": [
                    {"type": "postback", "title": "Price Only", "payload": "INTENT_PRICE"},
                    {"type": "postback", "title": "General Enquiry", "payload": "INTENT_GENERAL"},
                    {"type": "postback", "title": "Payment Proof", "payload": "INTENT_PAYMENT"},
                ],
            },
        }
    }
    send_to_messenger(psid, payload)


def handle_intent(psid: str, intent: str):
    # throttle intent replies too (prevents floods if postback repeats)
    if not throttle_ok(psid):
        return

    external_lead_id = generate_external_lead_id()

    if intent == "PRICE":
        link = build_form_link(PRICE_FORM_URL, external_lead_id)
        send_text(psid, f"✅ Price Only Request\n{link}\n\nSubmit and an agent will reply shortly.")

    elif intent == "GENERAL":
        link = build_form_link(GENERAL_FORM_URL, external_lead_id)
        send_text(psid, f"✅ General Enquiry\n{link}\n\nSubmit and our team will assist you.")

    elif intent == "PAYMENT":
        link = build_form_link(PAYMENT_FORM_URL, external_lead_id)
        send_text(psid, f"✅ Upload Payment Proof\n{link}\n\nSubmit and we’ll verify payment.")

    else:
        show_main_menu(psid)


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json() or {}

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):

            psid = (event.get("sender") or {}).get("id")
            if not psid:
                continue

            # --- Ignore receipts, reads, etc ---
            if "delivery" in event or "read" in event:
                continue

            # --- Postbacks (button clicks) ---
            if "postback" in event:
                payload = (event["postback"] or {}).get("payload", "")
                if payload == "INTENT_PRICE":
                    handle_intent(psid, "PRICE")
                elif payload == "INTENT_GENERAL":
                    handle_intent(psid, "GENERAL")
                elif payload == "INTENT_PAYMENT":
                    handle_intent(psid, "PAYMENT")
                else:
                    show_main_menu(psid)
                continue

            # --- Only process real message events ---
            msg = event.get("message")
            if not msg:
                continue

            # Ignore echoes (messages sent by the Page/bot)
            if msg.get("is_echo") is True:
                continue

            # De-dupe (Meta retries can resend same MID)
            mid = msg.get("mid")
            if mid:
                if mid in PROCESSED_MIDS:
                    continue
                PROCESSED_MIDS.add(mid)

            # Only respond to specific keywords (prevents loops on random text)
            text = (msg.get("text") or "").strip().upper()
            if text in ("HI", "HELLO", "START", "MENU"):
                show_main_menu(psid)
            else:
                # do NOTHING for other text (prevents accidental loops)
                pass

    return "OK", 200
