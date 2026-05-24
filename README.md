# HKRE App

Pulls Hong Kong SRPE residential development listings and documents via **SRPE HTTP APIs** (`src/scraping/srpe_api.py`), compares against **devm** sheets, uploads PDFs to Google Drive, and converts PO/PR/RT PDFs to CSV (Tabula-Java).

## Features

- Automated collection of metadata and PDF links (**t18m** and **non-t18m**) via SRPE HTTP APIs
- Compares scraped data to existing rows in the **devm** sheet; only downloads PDFs when content (SB, RT, PO) has changed
- Granular PDF downloads: e.g. only the specific Price Order or Sales Brochure file that changed, not the whole category
- Register of Transactions (RT): special handling for legacy (no RT in DB) vs. stored RT; RT downloads piggyback when PO or SB changes
- PDF upload to Google Drive and PDF→CSV conversion (Tabula-Java) for PO/PR/RT files
- New/updated rows inserted into **devm t18m** or **devm non-t18m**; text wrap applied to note columns

## Project Structure

```
HKRE App/
├── config/              # Configuration (settings.py, credentials)
├── src/
│   ├── main.py          # Entry point, run loop
│   ├── scraping/        # SRPE HTTP API (`srpe_api`), property_processing, file_download
│   ├── google_services/ # Sheets, Drive, Docs, auth
│   └── converters/      # PDF to CSV (Tabula wrapper)
├── data/                # Local download dirs (t18m / non-t18m)
├── main.py              # Convenience launcher (runs src/main.py)
└── requirements.txt
```

## Quick Start

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Prerequisites**
   - **Python 3.9+**
   - **Java** (for Tabula-Java PDF→CSV)
   - Google API credentials: place `credentials.json` in `config/` (or adjust `CREDENTIALS_FILE` in `settings.py`). Optional: `.env` for `PARENT_FOLDER_ID` and other overrides.

3. **Run**
   From project root:
   ```bash
   python main.py
   ```
   Or:
   ```bash
   python src/main.py
   ```

## Configuration

- `config/settings.py` — SRPE API endpoints/referers, data dirs, credentials path, `PARENT_FOLDER_ID`.
- Environment: `.env` is loaded automatically when present (`python-dotenv`).
- **`HKRE_SKIP_METADATA_SHEET_INSERT`:** omit or set **`1`** to skip spreadsheet prepends when only metadata/note/date columns drift; set **`0`** if you still want those cells refreshed (prepend at row 2).
- **GitHub Actions:** `.github/workflows/hkre_scraper.yml` — use **Run workflow**, or the weekly `schedule` (change the cron). Add the repository secrets named in the YAML header comment.

## Requirements (requirements.txt)

- **Data:** pandas, numpy  
- **Google:** google-api-python-client, google-auth*, gspread  
- **Other:** requests, python-dotenv  

Java is required separately for Tabula-based PDF conversion.
