import os, time, secrets, requests
from flask import Flask, request

app = Flask(__name__)

PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN")

PRICE_FORM_URL = os.getenv("PRICE_FORM_URL", "")
GENERAL_FORM_URL = os.getenv("GENERAL_FORM_URL", "")
PAYMENT_FORM_URL = os.getenv("PAYMENT_FORM_URL", "")

FORM_REF_PARAM = os.getenv("FORM_REF_PARAM", "External_Lead_ID")
FORM_PSID_PARAM = os.getenv("FORM_PSID_PARAM", "")  # optional

PROCESSED_MIDS = set()

def gen_ref():
    ts = time.strftime("%Y%m%d-%H%M%S")
    rnd = secrets.token_hex(3).upper()
    return f"ET-{ts}-{rnd}"

def send_payload(psid, payload):
    if not PAGE_ACCESS_TOKEN:
        print("[NO TOKEN] Would send:", payload)
        return
    url = "https://graph.facebook.com/v18.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}
    requests.post(
        url,
        params=params,
        json={"recipient": {"id": psid}, "message": payload},
        timeout=10
    )

def send_text(psid, text):
    send_payload(psid, {"text": text})

def build_link(base_url, psid, ref):
    if not base_url:
        return ""
    joiner = "&" if "?" in base_url else "?"
    link = f"{base_url}{joiner}{FORM_REF_PARAM}={ref}"
    if FORM_PSID_PARAM:
        link += f"&{FORM_PSID_PARAM}={psid}"
    return link

def show_menu(psid):
    payload = {
        "attachment": {
            "type": "template",
            "payload": {
                "template_type": "button",
                "text": "Elite Travel ✈️\nPlease choose an option:",
                "buttons": [
                    {"type": "postback", "title": "Price Only", "payload": "INTENT_PRICE"},
                    {"type": "postback", "title": "General Enquiry", "payload": "INTENT_GENERAL"},
                    {"type": "postback", "title": "Payment Proof", "payload": "INTENT_PAYMENT"},
                ],
            },
        }
    }
    send_payload(psid, payload)

def handle_intent(psid, intent):
    ref = gen_ref()
    if intent == "PRICE":
        link = build_link(PRICE_FORM_URL, psid, ref)
        send_text(psid, f"✅ Price Only Form:\n{link}\n\nSubmit it and our team will respond shortly.")
    elif intent == "GENERAL":
        link = build_link(GENERAL_FORM_URL, psid, ref)
        send_text(psid, f"✅ General Enquiry Form:\n{link}\n\nSubmit and we’ll assist you.")
    elif intent == "PAYMENT":
        link = build_link(PAYMENT_FORM_URL, psid, ref)
        send_text(psid, f"✅ Payment Proof Upload:\n{link}\n\nSubmit and we’ll verify your payment.")
    else:
        show_menu(psid)

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
            psid = (m.get("sender") or {}).get("id")
            if not psid:
                continue

            msg = m.get("message") or {}
            mid = msg.get("mid")
            if mid and mid in PROCESSED_MIDS:
                continue
            if mid:
                PROCESSED_MIDS.add(mid)

            # Handle button clicks
            if "postback" in m:
                p = (m["postback"] or {}).get("payload", "")
                if p == "INTENT_PRICE":
                    handle_intent(psid, "PRICE")
                elif p == "INTENT_GENERAL":
                    handle_intent(psid, "GENERAL")
                elif p == "INTENT_PAYMENT":
                    handle_intent(psid, "PAYMENT")
                else:
                    show_menu(psid)
                continue

            # Any text message just shows the menu
            show_menu(psid)

    return "OK", 200
