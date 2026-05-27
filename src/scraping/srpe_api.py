"""
SRPE HTTP API module for development scraping (t18m and non-t18m).

Uses direct HTTPS calls to www.srpe.gov.hk APIs, devm sheet comparison,
HTTP PDF download, Tabula CSV conversion where applicable, and Drive upload.

Entry points
------------
list_dev_ids()            — ordered development ID strings by version
build_devm_and_pdfs()     — per dev ID: (devm dict, PDF URL dicts)
process_single_dev_api()  — full pipeline for one ID (DB diff, downloads, Sheet insert)
main_api()                — loops over IDs for one version
"""

import html
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from config import (
    SRPE_API_REFERER,
    SRPE_API_REFERER_T18M,
    SRPE_FROM_PATH,
    SRPE_FROM_PATH_T18M,
    SRPE_SEARCH_API_URL,
    SRPE_SEARCH_API_URL_T18M,
)

from src.scraping.diagnostics import get_logger
from src.scraping.file_download import (
    download_pdf_http,
    get_download_directories,
)
from src.scraping.property_processing import (
    check_property_in_database,
    clean_property_data,
    devm_display_for_log,
    format_existing_file_update_line,
    process_property_pdfs,
)
from src.google_services import (
    create_drive_folder,
    get_or_create_drive_folder,
    insert_new_data,
    update_log,
)

_log = get_logger("srpe_api")


def _metadata_sheet_inserts_disabled() -> bool:
    """
    Default: do NOT prepend a new devm row when only metadata cells differ — avoids noisy duplicates.
    Set HKRE_SKIP_METADATA_SHEET_INSERT=0 to restore writing those fields (prepend row 2, as before).
    """
    v = os.getenv("HKRE_SKIP_METADATA_SHEET_INSERT", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SRPE_BASE = "https://www.srpe.gov.hk"
_DOWNLOAD_HEADERS = {
    "Referer": f"{SRPE_BASE}/opip/selected_dev_all_development",
    "Origin": SRPE_BASE,
    "User-Agent": "Mozilla/5.0",
}


def _api_headers(version: str) -> dict:
    """HTTP API headers by listing version."""
    v = (version or "non-t18m").strip().lower()
    referer = SRPE_API_REFERER_T18M if v == "t18m" else SRPE_API_REFERER
    return {
        "Content-Type": "application/json",
        "Referer": referer,
        "Origin": SRPE_BASE,
        "User-Agent": "Mozilla/5.0 (compatible; HKRE script)",
    }


def _search_api_url(version: str) -> str:
    """District area search endpoint by listing version."""
    v = (version or "non-t18m").strip().lower()
    return SRPE_SEARCH_API_URL_T18M if v == "t18m" else SRPE_SEARCH_API_URL

# ---------------------------------------------------------------------------
# Utility helpers (ported from hkre_experiment.ipynb)
# ---------------------------------------------------------------------------


def _normalize_website(url):
    if not url:
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    if not url.endswith("/"):
        url += "/"
    return url


def _concat_locale(en_val, zh_val, sep=" "):
    a = (en_val or "").strip()
    b = (zh_val or "").strip()
    if not a and not b:
        return None
    if not a:
        return b or None
    if not b:
        return a
    if a == b:
        return a
    return f"{a}{sep}{b}"


def _concat_locale_for_lookup(en_val, zh_val):
    """
    Join EN/ZH without an extra separator to align with current devm sheet
    pre-normalization flow where line breaks are removed before indexing.
    """
    return _concat_locale(en_val, zh_val, sep="")


def _concat_locale_multiline(en_val, zh_val):
    """Join EN/ZH with newline for readable Google Sheet display."""
    return _concat_locale(en_val, zh_val, sep="\n")


def _plain_text_from_api_html(val) -> str:
    """Strip simple HTML from Map API remark fields for Google Sheet cells."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _devm_identity_for_lookup(devm: dict) -> dict:
    """
    Build a comparison-only copy of devm identity fields.

    The current sheet indexing flow removes line breaks from source cells before
    lookup index construction, so we mirror that for matching only.
    """
    out = dict(devm)
    for key in ("name", "phas", "phasnm", "addr", "area"):
        val = out.get(key)
        if isinstance(val, str):
            out[key] = val.replace("\n", "")
    return out


def _devm_values_for_download_mapping(devm: dict) -> dict:
    """
    Build a download-mapping copy that preserves interior whitespace.

    Comparison now removes all whitespace, but PDF key filtering in
    process_property_pdfs needs the original label text (including spaces/newlines)
    to match keys in the generated pdf maps.
    """
    out = {}
    for key, value in devm.items():
        if isinstance(value, str):
            out[key] = value.strip()
        else:
            out[key] = value
    return out


def _format_date(value):
    """Normalise API ISO datetimes to sheet-style DD Mon YYYY (or None)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%d %b %Y")
    except ValueError:
        return s[:10] if len(s) >= 10 else s


def _bytes_to_kb(size):
    if size is None:
        return None
    try:
        return int(size) // 1024
    except (TypeError, ValueError):
        return None


def _sheet_label_with_filesize(base_label: str, file_size_bytes):
    """Format label as '<base>\\n(File size: XKB)' when size exists."""
    if not base_label:
        return ""
    kb = _bytes_to_kb(file_size_bytes)
    if kb is None:
        return base_label
    return f"{base_label}\n(File size: {kb:,}KB)"


def _po_sheet_cell_text(label, file_size_bytes, date_of_printing_raw) -> str:
    """
    Mimic devm price-order cell layout, e.g.:
        1C
        674KB
        26 Apr 2016
    """
    lab = (str(label).strip() if label is not None else "").strip()
    if not lab:
        return ""
    parts = [lab]
    kb = _bytes_to_kb(file_size_bytes)
    if kb is not None:
        parts.append(f"{kb:,}KB")
    date_str = _format_date(date_of_printing_raw) if date_of_printing_raw else ""
    if date_str:
        parts.append(date_str)
    return "\n".join(parts)


def _parse_display_date(value):
    """Parse display date (DD Mon YYYY) into a date object for sorting."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%d %b %Y").date()
    except ValueError:
        return None


def _dedupe_brochure_rows(rows):
    """
    Remove duplicate brochure rows while preserving order.

    Dedupe key prioritizes file identity; falls back to label-shaping fields.
    """
    seen = set()
    out = []
    for r in rows:
        key = (
            r.get("file_id"),
            r.get("file_name"),
            r.get("seq"),
            r.get("part_no"),
            r.get("type"),
            r.get("date_of_exam"),
            r.get("date_of_print"),
            r.get("file_size"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _dedupe_exam_rows(rows):
    """Remove duplicate examination-record rows while preserving order."""
    seen = set()
    out = []
    for r in rows:
        key = (
            r.get("file_id"),
            r.get("file_name"),
            r.get("type"),
            r.get("date_of_exam"),
            r.get("date_of_print"),
            r.get("file_size"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _dedupe_transaction_rows(rows):
    """Remove duplicate register-of-transactions rows while preserving order."""
    seen = set()
    out = []
    for r in rows:
        key = (
            r.get("file_id"),
            r.get("file_name"),
            r.get("file_size"),
            r.get("update_date"),
            r.get("update_time"),
            r.get("update_datetime"),
            r.get("submission_time"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _extract_area_from_dev(dev: dict) -> tuple:
    """
    Extract area text from Map API dev payload.

    Handles both payload shapes:
    1) Nested planning objects (planningArea1/planningArea2 as dicts)
    2) Flattened string fields (planningArea1/planningArea2/broadDistrict + *Chn variants)
    """
    if not isinstance(dev, dict):
        return None, None

    # Preferred: nested planning objects
    for key in ("planningArea1", "planningArea2"):
        area_obj = dev.get(key)
        if isinstance(area_obj, dict):
            eng = area_obj.get("planningAreaNameEng")
            chn = area_obj.get("planningAreaNameChn")
            if eng or chn:
                return eng, chn

    # Fallback: broad district object
    broad_obj = dev.get("broadDistrict")
    if isinstance(broad_obj, dict):
        eng = broad_obj.get("broadDistrictNameEng")
        chn = broad_obj.get("broadDistrictNameChn")
        if eng or chn:
            return eng, chn

    # Legacy fallback: flattened string fields
    area_en = ", ".join(
        filter(None, [dev.get("planningArea1"), dev.get("planningArea2"), dev.get("broadDistrict")])
    ) or None
    area_zh = ", ".join(
        filter(
            None,
            [dev.get("planningArea1Chn"), dev.get("planningArea2Chn"), dev.get("broadDistrictChn")],
        )
    ) or None
    return area_en, area_zh


# ---------------------------------------------------------------------------
# Download URL builders
# ---------------------------------------------------------------------------


def _build_price_download_url(token: str, file_id, file_name: str, dev_id: str) -> str:
    """
    Price list PDF — same pattern as hkre_experiment / live site.
    /api/SrpeWebService/download/all_development_map/price/{token}/{file_id}/{file_name}/en?devId=...
    """
    base = f"{SRPE_BASE}/api/SrpeWebService/download/all_development_map/price"
    t = quote(str(token), safe="")
    fid = quote(str(file_id), safe="")
    fn = quote(str(file_name), safe="")
    did = quote(str(dev_id), safe="")
    return f"{base}/{t}/{fid}/{fn}/en?devId={did}"


def _build_brochure_part_download_url(
    token: str, file_id, seq, file_name: str, dev_id: str
) -> str:
    """
    Sales brochure part PDF — SRPE SPA uses:
    .../all_development_map/brochure/{token}/{file_id}/{seq}/{fileName}/en?devId=...
    (seq comes from API partFiles.seq, e.g. \"1\").
    """
    base = f"{SRPE_BASE}/api/SrpeWebService/download/all_development_map/brochure"
    t = quote(str(token), safe="")
    fid = quote(str(file_id), safe="")
    sq = quote(str(seq if seq is not None else ""), safe="")
    fn = quote(str(file_name), safe="")
    did = quote(str(dev_id), safe="")
    return f"{base}/{t}/{fid}/{sq}/{fn}/en?devId={did}"


def _build_exam_download_url(token: str, file_id, file_name: str, dev_id: str) -> str:
    """Examination record PDF — same /en suffix as other Map API downloads."""
    base = f"{SRPE_BASE}/api/SrpeWebService/download/all_development_map/exam"
    t = quote(str(token), safe="")
    fid = quote(str(file_id), safe="")
    fn = quote(str(file_name), safe="")
    did = quote(str(dev_id), safe="")
    return f"{base}/{t}/{fid}/{fn}/en?devId={did}"


def _build_trx_download_url(token: str, file_id, file_name: str, dev_id: str) -> str:
    """Register of transactions PDF — same shape as price (segment: trx)."""
    base = f"{SRPE_BASE}/api/SrpeWebService/download/all_development_map/trx"
    t = quote(str(token), safe="")
    fid = quote(str(file_id), safe="")
    fn = quote(str(file_name), safe="")
    did = quote(str(dev_id), safe="")
    return f"{base}/{t}/{fid}/{fn}/en?devId={did}"


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


def _listing_payload(version: str, limit: int) -> dict:
    """Build DistrictAreaSearch payload for non-t18m vs t18m listing."""
    v = (version or "non-t18m").strip().lower()
    if v == "t18m":
        return {
            "actionType": "Index For All Residential",
            "fromPath": SRPE_FROM_PATH_T18M,
            "page": 1,
            "limit": limit,
        }
    return {
        "actionType": "Index For All Residential",
        "fromPath": SRPE_FROM_PATH,
        "page": 1,
        "limit": limit,
    }


def list_dev_ids(limit: int = 600, version: str = "non-t18m") -> list:
    """
    Return development IDs from the SRPE search API.
    Result is in listing order (matches the website index).
    """
    url = _search_api_url(version)
    payload = _listing_payload(version, limit)
    res = requests.post(url, json=payload, headers=_api_headers(version), timeout=60)
    res.raise_for_status()
    data = res.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"API error listing devs: code={data.get('code')} remarks={data.get('remarks')!r}"
        )
    block = data.get("resultData") or {}
    rows = block.get("list") or []
    dev_ids = []
    for item in rows:
        did = item.get("developmentId") or item.get("id")
        if did is not None:
            dev_ids.append(str(did))
    _log.info(
        "list_dev_ids(%s): fetched %s / %s total",
        version,
        len(dev_ids),
        block.get("total"),
    )
    return dev_ids


def _extract_listing_area_eng_chn(item: dict) -> tuple:
    """Extract planning area (preferred) or broad district from listing row."""
    for key in ("planningArea1", "planningArea2"):
        obj = item.get(key)
        if isinstance(obj, dict):
            eng = obj.get("planningAreaNameEng")
            chn = obj.get("planningAreaNameChn")
            if eng or chn:
                return eng, chn
    broad = item.get("broadDistrict")
    if isinstance(broad, dict):
        eng = broad.get("broadDistrictNameEng")
        chn = broad.get("broadDistrictNameChn")
        if eng or chn:
            return eng, chn
    return None, None


def list_dev_identity_hints(limit: int = 600, version: str = "non-t18m") -> dict:
    """
    Return identity hints from DistrictAreaSearch keyed by developmentId.

    This mirrors the listing/table source used for identity matching:
    name, web, phas, phasnm, addr, area.
    """
    url = _search_api_url(version)
    payload = _listing_payload(version, limit)
    res = requests.post(url, json=payload, headers=_api_headers(version), timeout=60)
    res.raise_for_status()
    data = res.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"API error listing identity hints: code={data.get('code')} remarks={data.get('remarks')!r}"
        )
    block = data.get("resultData") or {}
    rows = block.get("list") or []

    hints = {}
    for item in rows:
        did = item.get("developmentId") or item.get("id")
        if did is None:
            continue
        did = str(did)

        area_eng, area_chn = _extract_listing_area_eng_chn(item)
        addresses = item.get("addresses") or []
        addr0 = addresses[0] if addresses and isinstance(addresses[0], dict) else {}

        hints[did] = {
            "name": _concat_locale_multiline(item.get("engName"), item.get("chnName")) or "",
            "web": _normalize_website(item.get("website")) or "",
            "phas": _concat_locale_multiline(item.get("engPhaseNo"), item.get("chnPhaseNo")) or "",
            "phasnm": _concat_locale_multiline(item.get("engPhaseName"), item.get("chnPhaseName")) or "",
            "addr": _concat_locale_multiline(addr0.get("engAddress"), addr0.get("chnAddress")) or "",
            "area": _concat_locale_multiline(area_eng, area_chn) or "",
            # Keep raw area pair for Map payload fallback.
            "area_eng": area_eng,
            "area_chn": area_chn,
        }

    _log.info("list_dev_identity_hints(%s): fetched %s hint(s)", version, len(hints))
    return hints


def get_dev_data(dev_id: str) -> dict:
    """Fetch full development data by devId from the Map API."""
    url = f"{SRPE_BASE}/api/SrpeWebService/Map/getMapDevResultById"
    # Map API request itself is shared across versions.
    res = requests.post(url, json={"devId": dev_id}, headers=_api_headers("non-t18m"), timeout=60)
    res.raise_for_status()
    return res.json()


def get_session_token(dev_id: str) -> str:
    """Fetch a fresh uniqueSessionId needed to sign download URLs."""
    data = get_dev_data(dev_id)
    rd = data.get("resultData") or {}
    tok = rd.get("uniqueSessionId")
    if not tok:
        raise KeyError(f"uniqueSessionId missing for devId={dev_id}")
    return tok


# ---------------------------------------------------------------------------
# Data extraction (ported from hkre_experiment.ipynb extract_clean)
# ---------------------------------------------------------------------------


def _extract_sales_brochures(result: dict) -> dict:
    """Return { dates: {date_of_print, date_of_exam}, files: [...] }."""
    raw = []

    def _collect_brochure(b):
        for f in b.get("partFiles") or []:
            raw.append({
                "file_id": f.get("id"),
                "file_name": f.get("fileName"),
                "file_size": f.get("fileSize"),
                "seq": f.get("seq"),
                "part_no": f.get("partNo"),
                "type": "sales_brochure",
                "date": b.get("dateOfPrint"),
                "date_of_print": _format_date(b.get("dateOfPrint")),
                "date_of_exam": _format_date(b.get("dateOfExam")),
            })
        if b.get("examRecord"):
            f = b["examRecord"]
            raw.append({
                "file_id": f.get("id"),
                "file_name": f.get("fileName"),
                "file_size": f.get("fileSize"),
                "type": "exam_record",
                "date": b.get("dateOfExam"),
                "date_of_print": _format_date(b.get("dateOfPrint")),
                "date_of_exam": _format_date(b.get("dateOfExam")),
            })

    if result.get("brochure"):
        _collect_brochure(result["brochure"])
    for b in result.get("brochureList") or []:
        _collect_brochure(b)

    sb_rows = [x for x in raw if x.get("type") == "sales_brochure"]
    er_rows = [x for x in raw if x.get("type") == "exam_record"]

    latest_sb_rows = []
    if sb_rows:
        parsed_exam_dates = [
            _parse_display_date(r.get("date_of_exam")) for r in sb_rows
        ]
        valid_exam_dates = [d for d in parsed_exam_dates if d is not None]
        latest_sb_dt = max(valid_exam_dates) if valid_exam_dates else None
        if latest_sb_dt is not None:
            latest_sb_rows = [
                r for r in sb_rows if _parse_display_date(r.get("date_of_exam")) == latest_sb_dt
            ]
        if not latest_sb_rows:
            # Fallback when date_of_exam is missing on brochure rows.
            latest_sb_date = max((r.get("date") or "") for r in sb_rows)
            latest_sb_rows = [r for r in sb_rows if (r.get("date") or "") == latest_sb_date]
        latest_sb_rows = _dedupe_brochure_rows(latest_sb_rows)
        latest_sb_rows.sort(
            key=lambda r: (
                str(r.get("seq") if r.get("seq") is not None else ""),
                str(r.get("part_no") if r.get("part_no") is not None else ""),
                str(r.get("file_name") or ""),
            )
        )

    er_rows = _dedupe_exam_rows(er_rows)
    er_raw = None
    if er_rows:
        er_rows.sort(key=lambda r: (r.get("date") or ""), reverse=True)
        er_raw = er_rows[0]
    sb_raw_for_dates = latest_sb_rows[0] if latest_sb_rows else None

    dates = {
        "date_of_print": (sb_raw_for_dates["date_of_print"] if sb_raw_for_dates else None)
                         or (er_raw["date_of_print"] if er_raw else None),
        "date_of_exam": (er_raw["date_of_exam"] if er_raw else None)
                        or (sb_raw_for_dates["date_of_exam"] if sb_raw_for_dates else None),
    }
    files_out = []
    files_out.extend(latest_sb_rows)
    if er_raw:
        files_out.append(er_raw)
    return {"dates": dates, "files": files_out}


def _extract_clean(data: dict, dev_id_str: str, area_hint: dict = None) -> dict:
    """Convert raw API response to a cleaned dict (same shape as the notebook extract_clean)."""
    result = data.get("resultData") or {}
    dev = result.get("dev") or {}
    addresses = dev.get("addresses") or []
    addr = addresses[0] if addresses else {}

    area_en, area_zh = _extract_area_from_dev(dev)
    if not (area_en or area_zh):
        hint = area_hint or {}
        area_en = hint.get("eng")
        area_zh = hint.get("chn")

    token = result.get("uniqueSessionId")
    if not token:
        try:
            token = get_session_token(dev_id_str)
        except Exception:
            token = None

    sales_brochure_info = _extract_sales_brochures(result)

    transaction_files = []
    for item in result.get("transactions") or []:
        f = item.get("file") or {}
        transaction_files.append({
            "file_id": f.get("id"),
            "file_name": f.get("fileName"),
            "file_size": f.get("fileSize"),
            "update_date": item.get("updateDate"),
        })
    transaction_files = _dedupe_transaction_rows(transaction_files)

    price_list_files = []
    for item in result.get("prices") or []:
        f = item.get("file") or {}
        fid = f.get("id")
        fn = f.get("fileName")
        label = item.get("serialNo")
        dl_url = None
        if token and fid and fn:
            dl_url = _build_price_download_url(token, fid, fn, dev_id_str)
        price_list_files.append({
            "file_id": fid,
            "file_name": fn,
            "label": label,
            "file_size": f.get("fileSize"),
            "date_of_printing": item.get("dateOfPrinting"),
            "download_url": dl_url,
        })

    dev_popup_msg = dev.get("devpopupmsg") or {}

    return {
        "dev_id": result.get("devId") or dev_id_str,
        "name_of_development": _concat_locale_multiline(dev.get("engName"), dev.get("chnName")),
        "phase_no": _concat_locale_multiline(dev.get("engPhaseNo"), dev.get("chnPhaseNo")),
        "phase_name": _concat_locale_multiline(dev.get("engPhaseName"), dev.get("chnPhaseName")),
        "website": _normalize_website(dev.get("website")),
        "address": _concat_locale_multiline(addr.get("engAddress"), addr.get("chnAddress")),
        "area": _concat_locale_multiline(area_en or None, area_zh or None),
        "token": token,
        "engSalesBrochureRemark": dev.get("engSalesBrochureRemark"),
        "transactionEngMsg": dev_popup_msg.get("transactionEngMsg"),
        "sales_brochure_files": sales_brochure_info,
        "register_of_transactions_files": transaction_files,
        "price_list_files": price_list_files,
    }


# ---------------------------------------------------------------------------
# Build devm + pdfs dicts that the existing pipeline understands
# ---------------------------------------------------------------------------


def build_devm_and_pdfs(dev_id: str, area_hint: dict = None) -> tuple:
    """
    Fetch API data for dev_id and return (devm, pdfs).

    devm  – dict with keys matching what check_property_in_database and
            insert_new_data expect (same field order as the browser path).
    pdfs  – { 'sales_brochure_pdf': {key: url},
              'register_of_transactions_pdf': {key: url},
              'price_orders_pdf': {key: url} }

    Text keys in the PDF maps are intentionally identical to the values
    stored under the matching devm['sbN_text'] / 'rtN_text' / 'poN_text'
    field so that process_property_pdfs can resolve them correctly.

    Sales-brochure keys use SRPE's standard MUI anchor-title pattern:
        "Sales Brochure (Part 1)", "Sales Brochure (Part 2)", "Examination Record"

    Register-of-transactions keys use the PDF file-name stem
    (e.g. "52111250826001RT") which mirrors the MUI anchor title on the
    live site.

    Price-list keys and poN_text mimic devm cells: serial label, KB line,
    then date of printing (DD Mon YYYY), joined with newlines.
    """
    data = get_dev_data(dev_id)
    clean = _extract_clean(data, dev_id, area_hint=area_hint)
    token = clean.get("token") or ""

    # ------------------------------------------------------------------
    # Core identity fields (order matters – insert_new_data uses .values())
    # ------------------------------------------------------------------
    devm = {
        "name": clean["name_of_development"] or "",
        "web": clean["website"] or "",
        "phas": clean["phase_no"] or "",
        "phasnm": clean["phase_name"] or "",
        "addr": clean["address"] or "",
        "area": clean["area"] or "",
        "date": "",  # listing-table date column; not exposed by Map API
    }

    # ------------------------------------------------------------------
    # Sales brochure section
    # ------------------------------------------------------------------
    sb_info = clean["sales_brochure_files"]
    dates = sb_info["dates"]
    devm["sb1_date"] = dates.get("date_of_print") or ""
    devm["sbe_date"] = dates.get("date_of_exam") or ""

    sales_brochure_pdf = {}
    sb_part = 0
    er_key = ""
    total_sb_parts = sum(1 for f in sb_info["files"] if f.get("type") == "sales_brochure")
    for f in sb_info["files"]:
        ftype = f.get("type")
        fid = f.get("file_id")
        fname = f.get("file_name") or ""
        if ftype == "sales_brochure":
            sb_part += 1
            if total_sb_parts > 1:
                base_label = f"Sales Brochure (Part {sb_part})"
            else:
                base_label = "Sales Brochure"
            key = _sheet_label_with_filesize(base_label, f.get("file_size"))
            devm[f"sb{sb_part}_text"] = key
            if fid and fname and token:
                seq = f.get("seq")
                if seq is None or seq == "":
                    seq = f.get("part_no") if f.get("part_no") not in (None, "") else ""
                sales_brochure_pdf[key] = _build_brochure_part_download_url(
                    token, fid, seq, fname, dev_id
                )
        elif ftype == "exam_record":
            er_key = _sheet_label_with_filesize("Examination Record", f.get("file_size"))
            devm["sbe_text"] = er_key
            if fid and fname and token:
                sales_brochure_pdf[er_key] = _build_exam_download_url(
                    token, fid, fname, dev_id
                )

    if not er_key:
        devm.setdefault("sbe_text", "")
    devm["sb_note"] = ""

    # ------------------------------------------------------------------
    # Register of transactions section
    # ------------------------------------------------------------------
    rt_pdf = {}
    rt_dates = []
    for i, f in enumerate(clean["register_of_transactions_files"], start=1):
        fid = f.get("file_id")
        fname = f.get("file_name") or ""
        upd = f.get("update_date") or ""
        base_label = "Register of Transactions" if i == 1 else f"Register of Transactions (Update {i})"
        key = _sheet_label_with_filesize(base_label, f.get("file_size"))
        devm[f"rt{i}_text"] = key
        if fid and fname and token:
            rt_pdf[key] = _build_trx_download_url(token, fid, fname, dev_id)
        if upd:
            formatted = _format_date(upd) or str(upd).strip()
            if formatted:
                rt_dates.append(formatted)

    devm["rt_date"] = ", ".join(rt_dates) if rt_dates else ""
    devm["rt_note"] = _plain_text_from_api_html(clean.get("transactionEngMsg"))

    # ------------------------------------------------------------------
    # Price orders section
    # ------------------------------------------------------------------
    po_pdf = {}
    for i, f in enumerate(clean["price_list_files"], start=1):
        label = (f.get("label") or f.get("file_name") or f"Price List {i}")
        if isinstance(label, str):
            label = label.strip()
        cell_text = _po_sheet_cell_text(
            label,
            f.get("file_size"),
            f.get("date_of_printing"),
        )
        devm[f"po{i}_text"] = cell_text or label
        url = f.get("download_url")
        key = cell_text or label
        if url and key:
            po_pdf[key] = url

    devm["po_note"] = ""

    pdfs = {
        "sales_brochure_pdf": sales_brochure_pdf,
        "register_of_transactions_pdf": rt_pdf,
        "price_orders_pdf": po_pdf,
    }
    return devm, pdfs


# ---------------------------------------------------------------------------
# Per-development processing (mirrors process_single_property in property_processing.py)
# ---------------------------------------------------------------------------


def process_single_dev_api(
    dev_id: str,
    dev_index: int,
    devm_lookup: dict,
    sheet,
    run_folder_id: str,
    sales_brochure_files_dir: str,
    register_of_transactions_files_dir: str,
    price_lists_files_dir: str,
    drive_service,
    prices_folder_id: str,
    transactions_folder_id: str,
    version: str,
    docs,
    cached_folder_ids: dict,
    already_uploaded_pdfs: set,
    logged_updates_for_rows: set,
    identity_hints_by_dev_id: dict = None,
):
    """
    Process one development via the API path.

    Returns True if a retryable error occurred (caller should retry the same
    dev_id), False on success or unrecoverable skip.
    """
    start_time = time.time()
    is_retry = dev_index in logged_updates_for_rows
    # ------------------------------------------------------------------
    # 1. Fetch data from API
    # ------------------------------------------------------------------
    try:
        identity_hint = (identity_hints_by_dev_id or {}).get(str(dev_id)) or {}
        area_hint = {
            "eng": identity_hint.get("area_eng"),
            "chn": identity_hint.get("area_chn"),
        }
        devm, pdfs = build_devm_and_pdfs(dev_id, area_hint=area_hint)
    except Exception as exc:
        _log.warning("build_devm_and_pdfs failed for dev_id=%s: %s", dev_id, exc)
        update_log(docs, f"API fetch failed for dev_id {dev_id}: {exc}\nSkipping.\n\n")
        return False

    name_cleaned = (devm.get("name") or "").replace("\n", "")
    if not is_retry:
        update_log(docs, f"==== Development {dev_index} {name_cleaned} begins ====\n")

    # ------------------------------------------------------------------
    # 2. Check database
    # ------------------------------------------------------------------
    # Stage A: identity new/existing check based on DistrictAreaSearch fields.
    identity_devm = {
        "name": identity_hint.get("name") or devm.get("name") or "",
        "web": identity_hint.get("web") or devm.get("web") or "",
        "phas": identity_hint.get("phas") or devm.get("phas") or "",
        "phasnm": identity_hint.get("phasnm") or devm.get("phasnm") or "",
        "addr": identity_hint.get("addr") or devm.get("addr") or "",
        "area": identity_hint.get("area") or devm.get("area") or "",
    }
    identity_nolines = clean_property_data(_devm_identity_for_lookup(identity_devm))
    _, identity_was_new, _, _ = check_property_in_database(
        identity_nolines, devm_lookup, docs=docs
    )

    # Stage B: for existing developments only, do full content diff (sb/rt/po).
    devm_nolines = clean_property_data(_devm_identity_for_lookup(devm))
    display_name = (devm.get("name") or "").replace("\n", " ").strip() or devm_nolines.get("name", "")
    found = False
    updates_list = []
    missing_fields = None
    is_new = identity_was_new
    if not identity_was_new:
        found, is_new, updates_list, missing_fields = check_property_in_database(
            devm_nolines, devm_lookup, docs=docs
        )
        if is_new:
            # Defensive fallback: if full compare unexpectedly resolves as new,
            # honour it as new and download full set.
            found = False
            updates_list = []
            missing_fields = None

    if found:
        _log.info("devId %s: sheet matches — skipping", dev_id)
        elapsed = (time.time() - start_time) / 60
        update_log(docs, f"finished devm {dev_index} in {elapsed:.2f} min\n\n")
        return False

    metadata_only_fields = {"sb_note", "sbe_date", "rt_note", "rt_date", "po_note"}
    only_metadata_changed = (
        not is_new
        and missing_fields
        and all(f in metadata_only_fields for f in missing_fields)
    )
    if only_metadata_changed:
        if _metadata_sheet_inserts_disabled():
            _log.info(
                "devId %s: metadata-only fields %s — sheet insert skipped (default HKRE_SKIP_METADATA_SHEET_INSERT)",
                dev_id,
                ", ".join(missing_fields),
            )
            elapsed = (time.time() - start_time) / 60
            update_log(docs, f"finished devm {dev_index} in {elapsed:.2f} min\n\n")
            return False

        _log.info(
            "devId %s: metadata-only fields %s — prepending sheet row; no PDFs",
            dev_id,
            ", ".join(missing_fields),
        )
        insert_new_data(sheet, devm)
        cached_folder_ids.pop(devm_nolines["name"], None)
        already_uploaded_pdfs.clear()
        logged_updates_for_rows.discard(dev_index)
        elapsed = (time.time() - start_time) / 60
        update_log(docs, f"finished devm {dev_index} in {elapsed:.2f} min\n\n")
        return False

    # ------------------------------------------------------------------
    # 3. Log new / updated
    # ------------------------------------------------------------------
    if is_new:
        if is_retry:
            update_log(docs, f"Retrying dev_id {dev_id} ({display_name}) — continuing downloads for new file.\n")
        else:
            update_log(docs, f"New File: {display_name}\n")
            logged_updates_for_rows.add(dev_index)
        missing_fields = None  # download all PDFs for new property
    else:
        if is_retry:
            update_log(docs, f"Retrying dev_id {dev_id} ({display_name}) — updates unchanged.\n")
        else:
            devm_display = devm_display_for_log(devm)
            update_log(
                docs,
                f"Updates to Existing File: {display_name}\n"
                + "\n".join(
                    f"updated {format_existing_file_update_line(u, devm_display)}"
                    for u in updates_list
                )
                + "\n",
            )
        logged_updates_for_rows.add(dev_index)

    # ------------------------------------------------------------------
    # 4. Create / reuse property Drive folder
    # ------------------------------------------------------------------
    if devm_nolines["name"] in cached_folder_ids:
        property_folder_id = cached_folder_ids[devm_nolines["name"]]
    else:
        property_folder_id = get_or_create_drive_folder(
            devm_nolines["name"], run_folder_id, drive_service
        )
        cached_folder_ids[devm_nolines["name"]] = property_folder_id

    _log.info(
        "devId %s: starting PDF download/upload to Drive for %s",
        dev_id,
        devm_nolines["name"][:80],
    )

    # ------------------------------------------------------------------
    # 5. Download & upload PDFs (HTTP, no browser)
    # ------------------------------------------------------------------
    devm_for_download_mapping = _devm_values_for_download_mapping(devm)
    timeout_occurred = process_property_pdfs(
        pdfs=pdfs,
        property_folder_id=property_folder_id,
        sales_brochure_files_dir=sales_brochure_files_dir,
        register_of_transactions_files_dir=register_of_transactions_files_dir,
        price_lists_files_dir=price_lists_files_dir,
        drive_service=drive_service,
        prices_folder_id=prices_folder_id,
        transactions_folder_id=transactions_folder_id,
        development_name=devm_nolines["name"],
        version=version,
        docs=docs,
        missing_fields=missing_fields,
        devm_nolines=devm_for_download_mapping,
        already_uploaded=already_uploaded_pdfs,
    )

    if timeout_occurred:
        # HTTP downloads don't time out in the same way; treat as transient
        return True

    # ------------------------------------------------------------------
    # 6. Persist to Google Sheet
    # ------------------------------------------------------------------
    insert_new_data(sheet, devm)

    cached_folder_ids.pop(devm_nolines["name"], None)
    already_uploaded_pdfs.clear()
    logged_updates_for_rows.discard(dev_index)

    elapsed = (time.time() - start_time) / 60
    update_log(docs, f"finished devm {dev_index} in {elapsed:.2f} min\n\n")
    return False


# ---------------------------------------------------------------------------
# Top-level loop over development IDs for one listing version (t18m / non-t18m)
# ---------------------------------------------------------------------------


def main_api(
    version: str,
    run_folder_id: str,
    start_idx: int,
    sheet,
    devm_lookup: dict,
    drive_service,
    docs,
    dev_ids: list = None,
):
    """
    Main API scraping loop over development IDs by version (**t18m** or **non-t18m**).

    start_idx  – 1-based index into dev_ids; acts as resume cursor.
    dev_ids    – pre-fetched list; fetched fresh if None.
    """
    prices_folder_id = create_drive_folder("Prices", parent_id=run_folder_id)
    transactions_folder_id = create_drive_folder("Transactions", parent_id=run_folder_id)

    sb_dir, rt_dir, po_dir = get_download_directories(version, "")

    if dev_ids is None:
        dev_ids = list_dev_ids(version=version)
    identity_hints_by_dev_id = list_dev_identity_hints(version=version)

    total = len(dev_ids)
    _log.info("main_api: %s dev IDs, starting from index %s", total, start_idx)

    cached_folder_ids = {}
    already_uploaded_pdfs = set()
    logged_updates_for_rows = set()

    idx = start_idx
    tl_loop = time.time()

    while idx <= total:
        dev_id = dev_ids[idx - 1]  # convert 1-based to 0-based
        try:
            should_retry = process_single_dev_api(
                dev_id=dev_id,
                dev_index=idx,
                devm_lookup=devm_lookup,
                sheet=sheet,
                run_folder_id=run_folder_id,
                sales_brochure_files_dir=sb_dir,
                register_of_transactions_files_dir=rt_dir,
                price_lists_files_dir=po_dir,
                drive_service=drive_service,
                prices_folder_id=prices_folder_id,
                transactions_folder_id=transactions_folder_id,
                version=version,
                docs=docs,
                cached_folder_ids=cached_folder_ids,
                already_uploaded_pdfs=already_uploaded_pdfs,
                logged_updates_for_rows=logged_updates_for_rows,
                identity_hints_by_dev_id=identity_hints_by_dev_id,
            )
            if should_retry:
                time.sleep(5)
                continue
            idx += 1

        except Exception as exc:
            err_name = type(exc).__name__
            err_msg = str(exc).split("\n")[0].strip()
            try:
                update_log(
                    docs,
                    f"Error at dev_id {dev_id} (index {idx}): {err_name}: {err_msg}\n"
                    f"Retrying same dev.\n\n",
                )
            except Exception:
                _log.warning(
                    "Could not append Docs log for dev_id=%s idx=%s (continuing retry loop)",
                    dev_id,
                    idx,
                    exc_info=True,
                )
            _log.warning("Unhandled error at dev_id=%s idx=%s: %s", dev_id, idx, exc)
            time.sleep(5)
            continue

    try:
        update_log(docs, f"Total time: {(time.time() - tl_loop) / 60:.2f} min\n\n")
    except Exception:
        _log.warning("Could not append total time to Docs log", exc_info=True)
    return idx
