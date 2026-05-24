"""
Configuration settings for HKRE App
All configuration constants are defined here.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory (project root)
BASE_DIR = Path(__file__).parent.parent

# Load .env before reading any configuration values.
load_dotenv(BASE_DIR / ".env")

# ============================================================================
# Reference URLs (disclaimer gates; scraping uses SRPE_* API/search below).
# ============================================================================

T18M_URL = "https://www.srpe.gov.hk/opip/disclaimer_index_for_all_residential_t18m.htm"
NON_T18M_URL = "https://www.srpe.gov.hk/opip/disclaimer_index_for_all_residential"

# ============================================================================
# Google Services Configuration
# ============================================================================

# Google Drive parent folder ID
PARENT_FOLDER_ID = os.getenv(
    "PARENT_FOLDER_ID",
    "1hixECvWsddWgy94PT0y2OQ_-kysAkvya",
)

# ============================================================================
# SRPE HTTP API endpoints + Referer / from-path payloads
# ============================================================================

SRPE_SEARCH_API_URL = os.getenv(
    "SRPE_SEARCH_API_URL",
    "https://www.srpe.gov.hk/api/SrpeWebService/DistrictAreaSearch/getDistrictAreaSearchResult",
)
SRPE_SEARCH_API_URL_T18M = os.getenv(
    "SRPE_SEARCH_API_URL_T18M",
    "https://www.srpe.gov.hk/api/SrpeWebService/DistrictAreaSearch/getDistrictAreaT18mSearchResult",
)
SRPE_API_REFERER = os.getenv(
    "SRPE_API_REFERER",
    "https://www.srpe.gov.hk/opip/all_development",
)
SRPE_API_REFERER_T18M = os.getenv(
    "SRPE_API_REFERER_T18M",
    "https://www.srpe.gov.hk/opip/selected_dev_all_development_t18m",
)
SRPE_FROM_PATH = os.getenv(
    "SRPE_FROM_PATH",
    "disclaimer_index_for_all_residential",
)
SRPE_FROM_PATH_T18M = os.getenv(
    "SRPE_FROM_PATH_T18M",
    "disclaimer_index_for_all_residential",
)

# ============================================================================
# Directory Paths
# ============================================================================

# Data directories for downloads
DATA_DIR = BASE_DIR / "data"
T18M_DIR = DATA_DIR / "t18m"
NON_T18M_DIR = DATA_DIR / "non-t18m"

# Credentials file path
CREDENTIALS_FILE = BASE_DIR / "config" / "credentials.json"
