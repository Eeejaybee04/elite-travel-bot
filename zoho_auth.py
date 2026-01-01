import os
import time
import requests
from threading import Lock

_TOKEN_LOCK = Lock()

def _token_url() -> str:
    dc = os.getenv("ZOHO_DC", "com")
    return f"https://accounts.zoho.{dc}/oauth/v2/token"

def _refresh_access_token() -> tuple[str, int]:
    """
    Refresh Zoho access token using refresh_token.
    Returns: (access_token, expires_at_unix)
    """
    data = {
        "grant_type": "refresh_token",
        "client_id": os.environ["ZOHO_CLIENT_ID"],
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
        "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
    }

    r = requests.post(_token_url(), data=data, timeout=30)
    r.raise_for_status()
    j = r.json()

    if "access_token" not in j:
        raise RuntimeError(f"Zoho refresh failed: {j}")

    expires_in = int(j.get("expires_in", 3600))
    expires_at = int(time.time()) + expires_in

    # Cache in process env so other calls can reuse it
    os.environ["ZOHO_ACCESS_TOKEN"] = j["access_token"]
    os.environ["ZOHO_ACCESS_TOKEN_EXPIRES_AT"] = str(expires_at)

    return j["access_token"], expires_at

def get_access_token() -> str:
    """
    Return a valid access token, refreshing if missing/expiring soon.
    """
    with _TOKEN_LOCK:
        token = os.getenv("ZOHO_ACCESS_TOKEN")
        expires_at = int(os.getenv("ZOHO_ACCESS_TOKEN_EXPIRES_AT", "0"))

        # refresh if missing or expiring within 2 mins
        if (not token) or (time.time() > (expires_at - 120)):
            token, _ = _refresh_access_token()

        return token
