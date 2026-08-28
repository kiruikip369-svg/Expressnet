import os
import re
import socket
import requests

API_URL = "https://api.apiwap.com/api/v1/whatsapp/send-message"
API_HOST = "api.apiwap.com"
API_TOKEN = os.environ.get("APIWAP_TOKEN", "d4a64f4419cc610144bd2c7bffd99a4f46635f52f3d072044c8c8b5562348fb8")

# =========================================
# TEST CONFIG — edit these for local testing
# =========================================
TEST_PHONE_NUMBER = "+254729281669"   # <-- put the number you want to test here
TEST_MESSAGE = "Test Hello"
# =========================================


def check_dns(host: str) -> bool:
    """Quick pre-flight check so DNS failures give a clear message, not a raw traceback."""
    try:
        socket.gethostbyname(host)
        return True
    except socket.gaierror:
        return False


def normalize_phone_number(phone: str, default_country_code: str = "254") -> str:
    """
    Normalize a phone number to E.164-ish format (+<countrycode><number>).
    Handles common Kenyan formats: 07XXXXXXXX, 7XXXXXXXX, 2547XXXXXXXX, +2547XXXXXXXX
    """
    if not phone:
        raise ValueError("Phone number is required")

    cleaned = re.sub(r"[\s\-\(\)]", "", phone)

    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if not digits.isdigit():
            raise ValueError(f"Invalid phone number: {phone}")
        return f"+{digits}"

    if cleaned.startswith(default_country_code):
        if not cleaned.isdigit():
            raise ValueError(f"Invalid phone number: {phone}")
        return f"+{cleaned}"

    if cleaned.startswith("0"):
        if not cleaned.isdigit():
            raise ValueError(f"Invalid phone number: {phone}")
        return f"+{default_country_code}{cleaned[1:]}"

    if cleaned.isdigit():
        return f"+{default_country_code}{cleaned}"

    raise ValueError(f"Invalid phone number: {phone}")


def send_whatsapp_message(phone_number: str, message: str, msg_type: str = "text"):
    if not check_dns(API_HOST):
        return None, (
            f"Could not resolve '{API_HOST}'. This is a local network/DNS issue, "
            "not an API or code problem. Try: (1) switching your DNS to 8.8.8.8, "
            "(2) checking VPN/firewall/antivirus, (3) opening the URL in a browser "
            "to confirm the service is reachable from your network."
        )

    try:
        formatted_phone = normalize_phone_number(phone_number)
    except ValueError as e:
        return None, str(e)

    payload = {
        "phoneNumber": formatted_phone,
        "message": message,
        "type": msg_type,
    }
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
    }

    try:
        response = requests.post(API_URL, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json(), None
    except requests.exceptions.HTTPError as e:
        try:
            error_body = e.response.json()
        except ValueError:
            error_body = e.response.text
        return None, error_body
    except requests.exceptions.RequestException as e:
        return None, str(e)


if __name__ == "__main__":
    data, error = send_whatsapp_message(TEST_PHONE_NUMBER, TEST_MESSAGE)
    if error:
        print("Error:", error)
    else:
        print("Data:", data)