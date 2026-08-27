"""
Test script for the Slek WhatsApp API (https://slek.org/whatsapp)

Handles phone numbers in these formats and normalizes them all to 2547XXXXXXXX / 2541XXXXXXXX:
    - 07XXXXXXXX   -> 2547XXXXXXXX
    - 01XXXXXXXX   -> 2541XXXXXXXX
    - +2547XXXXXXXX -> 2547XXXXXXXX
    - 2547XXXXXXXX  -> unchanged
"""

import os
import re
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 1. CONFIG — loaded from a .env file in the same directory
#
# Create a file named ".env" next to this script containing:
#     SLEK_KEY=your_actual_key
#     SLEK_SECRET=your_actual_secret
# ---------------------------------------------------------------------------
load_dotenv()

SLEK_KEY = os.getenv("SLEK_KEY")
SLEK_SECRET = os.getenv("SLEK_SECRET")
API_URL = "https://slek.org/whatsapp"

if not SLEK_KEY or not SLEK_SECRET:
    raise EnvironmentError(
        "SLEK_KEY and/or SLEK_SECRET not found. "
        "Make sure you have a .env file with these values set."
    )


# ---------------------------------------------------------------------------
# 2. Phone number normalization
# ---------------------------------------------------------------------------
def normalize_kenyan_number(phone: str) -> str:
    """
    Normalize a Kenyan phone number to the 2547XXXXXXXX / 2541XXXXXXXX format
    expected by the Slek API.

    Accepts:
        0712345678   -> 254712345678
        0112345678   -> 254112345678
        +254712345678 -> 254712345678
        254712345678  -> 254712345678 (unchanged)

    Raises:
        ValueError if the number doesn't match a recognizable Kenyan format.
    """
    if not phone:
        raise ValueError("Phone number is empty")

    # Strip whitespace, dashes, parentheses etc.
    cleaned = re.sub(r"[\s\-\(\)]", "", phone.strip())

    # Case 1: starts with +254
    if cleaned.startswith("+254"):
        cleaned = cleaned[1:]  # drop the '+', leaving 254XXXXXXXXX

    # Case 2: starts with 254 already
    elif cleaned.startswith("254"):
        pass  # already good

    # Case 3: starts with 0 (07... or 01...)
    elif cleaned.startswith("0"):
        cleaned = "254" + cleaned[1:]

    # Case 4: starts with 7 or 1 directly (missing leading 0/254)
    elif cleaned.startswith(("7", "1")):
        cleaned = "254" + cleaned

    else:
        raise ValueError(f"Unrecognized phone number format: {phone}")

    # Final validation: should now be 254 followed by 9 digits (12 digits total)
    if not re.fullmatch(r"254(7|1)\d{8}", cleaned):
        raise ValueError(f"Invalid Kenyan phone number after normalization: {cleaned}")

    return cleaned


# ---------------------------------------------------------------------------
# 3. Send WhatsApp message via Slek
# ---------------------------------------------------------------------------
def send_whatsapp_message(recipient_phone: str, recipient_name: str, header: str, message: str):
    """
    Sends a WhatsApp message via the Slek API.

    Returns a dict: {'success': bool, 'response': str} or {'success': False, 'error': str}
    """
    try:
        normalized_phone = normalize_kenyan_number(recipient_phone)
    except ValueError as e:
        return {"success": False, "error": f"Phone validation error: {e}"}

    post_fields = {
        "slek_key": SLEK_KEY,
        "slek_secret": SLEK_SECRET,
        "header": header,
        "message": message,
        "recipient_phone": normalized_phone,
        "recipient_name": recipient_name,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    print(f"Sending to {normalized_phone} ({recipient_name})...")

    try:
        response = requests.post(
            API_URL,
            data=post_fields,
            headers=headers,
            timeout=30,
        )
        print("Raw response:", response.text)

        response.raise_for_status()

        return {
            "success": True,
            "response": response.text,
        }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# 4. Run it
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # >>> Edit these test values before running <<<
    test_phone = "0712345678"          # try 07xxxxxxxx, 01xxxxxxxx, or +2547xxxxxxxx
    test_name = "John Koech"
    test_header = "TEST MESSAGE"
    test_message = "This is a test WhatsApp message sent via the Slek API."

    result = send_whatsapp_message(
        recipient_phone=test_phone,
        recipient_name=test_name,
        header=test_header,
        message=test_message,
    )

    print("\n--- Result ---")
    print(result)