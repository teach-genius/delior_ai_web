import os
import requests
from dotenv import load_dotenv

load_dotenv()

CSOD_BASE_URL  = os.getenv("CSOD_BASE_URL")   # ex: https://delior.csod.com
CSOD_CLIENT_ID = os.getenv("CSOD_CLIENT_ID")
CSOD_CLIENT_SECRET = os.getenv("CSOD_CLIENT_SECRET")

def get_csod_token() -> str:
    """Récupère un token OAuth2 Cornerstone."""
    response = requests.post(
        f"{CSOD_BASE_URL}/services/api/oauth2/token",
        data={
            "grant_type"   : "client_credentials",
            "client_id"    : CSOD_CLIENT_ID,
            "client_secret": CSOD_CLIENT_SECRET,
        }
    )
    response.raise_for_status()
    return response.json()["access_token"]

def csod_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_csod_token()}",
        "Content-Type" : "application/json",
    }