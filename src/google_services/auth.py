"""
Google Authentication Module
Handles authentication for Google Sheets, Drive, and Docs services
"""

import os
import json
import base64
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
import gspread
import sys

# Add parent directory to path for config imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import CREDENTIALS_FILE

load_dotenv()

# Google API scopes required for the application
# These define what permissions the app needs to access Google services
SCOPES = [
    "https://www.googleapis.com/auth/drive",        # Read/write access to Google Drive
    "https://www.googleapis.com/auth/spreadsheets",  # Read/write access to Google Sheets
    "https://www.googleapis.com/auth/documents",     # Read/write access to Google Docs
]


def google_auth():
    """
    Authenticate with Google using service account credentials.
    Returns spreadsheet and docs clients for Sheets and Docs operations.
    """
    raw = os.getenv("GOOGLE_CREDS_JSON")
    info = json.loads(base64.b64decode(raw).decode("utf-8"))
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key("1uVNZy9SE1PjtTeaCFl-dZrH4VdLPKen4pcTIbEIMKFE")
    docs = build("docs", "v1", credentials=creds)
    return spreadsheet, docs


def _load_creds():
    """
    Load OAuth credentials for Google Drive operations.
    Handles token refresh and re-authentication if needed.
    
    Returns Credentials object for Google Drive API access
    """
    # Step 1: Initialize credentials.json from environment variable if it doesn't exist
    # This allows deploying with a pre-saved token (useful for CI/CD or first-time setup)
    tok_b64 = os.getenv("GOOGLE_TOKEN_JSON_B64")
    if tok_b64 and not os.path.exists(CREDENTIALS_FILE):
        os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
        with open(CREDENTIALS_FILE, "wb") as f:
            f.write(base64.b64decode(tok_b64))

    # Step 2: Try to load existing credentials from credentials.json
    creds = None
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, "r") as f:
            data = json.load(f)
        try:
            creds = Credentials.from_authorized_user_info(data, SCOPES)
        except Exception:
            # Token file exists but is corrupted/invalid
            creds = None

    # Step 3: Refresh or re-authenticate if credentials are missing or expired
    if not creds or not creds.valid:
        # Try to refresh existing token first (faster, no user interaction)
        if creds and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                # Refresh failed, need full re-authentication
                creds = None
        
        # Full OAuth flow required if refresh didn't work or no token exists
        if not creds:
            raw = os.getenv("GOOGLE_OAUTH_JSON")
            if not raw:
                raise EnvironmentError("GOOGLE_OAUTH_JSON missing for OAuth re-auth")
            
            # Parse OAuth config (supports both base64-encoded and raw JSON)
            try:
                client_cfg = json.loads(base64.b64decode(raw).decode("utf-8"))
            except Exception:
                client_cfg = json.loads(raw)
            
            # Launch OAuth flow (opens browser for user consent)
            flow = InstalledAppFlow.from_client_config(client_cfg, SCOPES)
            creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

        # Step 4: Save credentials for future use (avoids repeated authentication)
        os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
        with open(CREDENTIALS_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def get_drive_service():
    """
    Get Google Drive service client using OAuth authentication.
    Returns a Drive API v3 service object.
    """
    creds = _load_creds()
    return build("drive", "v3", credentials=creds)


def initialize_google_services():
    """
    Initialize all Google services (Sheets, Docs, and Drive) in one call.
    

    """
    # Authenticate for Sheets and Docs (service account)
    spreadsheet, docs = google_auth()
    
    # Authenticate for Drive (OAuth)
    drive_service = get_drive_service()
    
    return spreadsheet, docs, drive_service

