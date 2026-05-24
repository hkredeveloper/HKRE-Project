"""
Property Processing Module
Validation, devm lookup indexing, and HTTP PDF downloads for SRPE developments.
"""

import re
from datetime import date, datetime

from .diagnostics import get_logger
from .file_download import download_pdf_http

_log = get_logger("property_processing")


def normalize_addr_for_lookup(addr):
    """
    Normalize address for lookup so that 'Sales suspended' and 'Sales terminated'
    do not affect matching. Removes these labels and trims whitespace.
    """
    if not addr or not isinstance(addr, str):
        return str(addr).strip() if addr is not None else ''
    s = str(addr).strip()
    # Comparison-only cleanup: keep these status labels in stored/display text,
    # but ignore them when building lookup keys.
    s = re.sub(r"sales\s*suspended", "", s, flags=re.IGNORECASE)
    s = re.sub(r"sales\s*terminated", "", s, flags=re.IGNORECASE)
    return ' '.join(s.split())


def normalize_web_for_lookup(web):
    """
    Strip query string and fragment so listing URLs (e.g. ?devId=...) match spreadsheet
    base URLs that omit tracking parameters.
    """
    if web is None:
        return ''
    s = str(web).strip()
    if not s:
        return ''
    if '?' in s:
        s = s.split('?', 1)[0]
    if '#' in s:
        s = s.split('#', 1)[0]
    # Root URLs often differ only by trailing slash vs spreadsheet.
    # Compare URLs case-insensitively.
    return s.rstrip('/').lower()


def _is_sb_er_text_field(key: str) -> bool:
    """Sales brochure / examination record anchor labels (not dates or notes)."""
    if not isinstance(key, str):
        return False
    return key == "sbe_text" or (key.startswith("sb") and key.endswith("_text"))


def _sb_er_compare_source_text(value) -> str:
    """
    Basis string for comparing SB and Examination Record labels to the sheet.

    The API often appends a second line '(File size: NKB)'; sheet rows frequently
    store only the first-line anchor title. If '(File size:' appears on the first
    line, strip from there so one-line pastes still match.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    first = s.split("\n", 1)[0].strip()
    idx = first.lower().find("(file size:")
    if idx >= 0:
        first = first[:idx].strip()
    return first


def normalize_lookup_field(value):
    """
    Normalize text for devm lookup keys and sheet comparison.

    Google Sheets often stores multiline cells (newlines inside a cell). For direct
    comparison stability, remove all whitespace characters (spaces/newlines/tabs)
    so values match regardless of visual spacing differences.

    Real date/datetime cells (and pandas Timestamp) are formatted as DD Mon YYYY
    before stripping, so they match API string dates like '03 Dec 2025'.
    """
    if value is None:
        return ''

    try:
        import pandas as pd
        if value is pd.NaT or pd.isna(value):
            return ''
    except Exception:
        pass

    if isinstance(value, datetime):
        value = value.strftime("%d %b %Y")
    elif isinstance(value, date):
        value = value.strftime("%d %b %Y")

    return ''.join(str(value).split())


def normalize_price_list_compare_token(t: str) -> str:
    """
    Extra normalization for price-list (po*_text) compare only.

    API builds KB as f\"{kb:,}KB\" (e.g. 1,234KB); Sheets often store 1234KB.
    Whitespace-normalized tokens then differ; strip digit-group commas so they match.
    """
    if not t:
        return ""
    return re.sub(r"(?<=\d),(?=\d)", "", t)


_DATE_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_SLASH_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
_DATE_DASH_DMY_RE = re.compile(r"\d{1,2}-\d{1,2}-\d{4}")


def _rewrite_slash_date_substring(match) -> str:
    s = match.group(0)
    for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return "".join(dt.strftime("%d %b %Y").split())
        except ValueError:
            continue
    return s


def _rewrite_iso_date_substring(match) -> str:
    s = match.group(0)
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return "".join(dt.strftime("%d %b %Y").split())
    except ValueError:
        return s


def _rewrite_dash_dmy_substring(match) -> str:
    s = match.group(0)
    for fmt in ("%d-%m-%Y", "%m-%d-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return "".join(dt.strftime("%d %b %Y").split())
        except ValueError:
            continue
    return s


def normalize_compare_token_calendar_dates(flat: str) -> str:
    """
    Rewrite embedded calendar literals inside a whitespace-collapsed token.

    Sheets often use 23/01/2024 or 2024-01-23; main.py replaces newlines with spaces,
    so PO cells become \"8L 455KB 23/01/2024\" → \"8L455KB23/01/2024\" which never
    matched API \"8L455KB23Jan2024\". Same for standalone sbe_date / rt_date cells.
    """
    if not flat:
        return flat
    out = _DATE_ISO_RE.sub(_rewrite_iso_date_substring, flat)
    out = _DATE_SLASH_RE.sub(_rewrite_slash_date_substring, out)
    out = _DATE_DASH_DMY_RE.sub(_rewrite_dash_dmy_substring, out)
    return out


def clean_property_data(devm):
    """
    Normalize string fields for lookup (multiline → single line, collapse spaces).
    Normalizes web URL for lookup (drops ?query and #fragment).
    Date/datetime values are normalized the same way as sheet cells.
    SB / Examination Record *_text fields use the first-line title only (before
    '(File size:') so they match sheet cells that omit the file-size line.
    """
    out = {}
    for key, value in devm.items():
        if isinstance(value, str) or isinstance(value, (datetime, date)):
            if _is_sb_er_text_field(key) and isinstance(value, str):
                v = normalize_lookup_field(_sb_er_compare_source_text(value))
            else:
                v = normalize_lookup_field(value)
            if key == 'web':
                v = normalize_web_for_lookup(v)
            out[key] = v
        else:
            out[key] = value
    return out


def devm_display_for_log(devm: dict) -> dict:
    """
    Copy of devm suitable for Google Docs: strip outer whitespace on strings,
    keep interior newlines (e.g. price-list cells: label / KB / date).
    """
    if not devm:
        return {}
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in devm.items()}


def format_existing_file_update_line(line: str, devm_display: dict = None) -> str:
    """
    Format one updates_list line for Google Docs.

    PO *_text values are logged as a compact one-line token built from the
    sheet-style multiline cell (label + KB + date), e.g.
    "po10_text: 1I463KB05 Feb 2024".
    """
    if not line or not devm_display or ":" not in line:
        return line
    field, _, _ = line.partition(":")
    field = field.strip()
    if field.startswith("po") and field.endswith("_text"):
        raw = devm_display.get(field)
        if raw is not None and isinstance(raw, str) and raw.strip():
            parts = [p.strip() for p in raw.splitlines() if p and p.strip()]
            if parts:
                return f"{field}: {''.join(parts)}"
    return line


def format_po_missing_field_docs(field: str, devm_display: dict = None, devm_nolines: dict = None) -> str:
    """One bullet for [existing-file diff debug] price-list section (sheet-style newlines)."""
    if devm_display is not None:
        raw = devm_display.get(field)
        if raw is not None and isinstance(raw, str) and raw.strip():
            inner = raw.strip()
            return f'  - {field}:\n"{inner}"'
    fallback = (devm_nolines or {}).get(field, "")
    return f"  - {field}: {fallback}"


def build_lookup_index(devm_df):
    """
    Build a dictionary index from the database DataFrame for O(1) property lookups.
    
    Key: (name, web, phas, phasnm) tuple; multiline sheet cells are
    normalized the same way as scraped data; web has ?query/#fragment stripped.
    Value: list of all DataFrame rows that share that key (duplicate 4-field rows
    each appear in the list). Content comparison unions every cell from every
    matching row into one set — a value matches if it appears in any column of
    any duplicate row.
    
    Call this once after loading devm_df, then pass the lookup to check_property_in_database.
    """
    lookup = {}
    for _, row in devm_df.iterrows():
        key = (
            normalize_lookup_field(row.iloc[0]),
            normalize_web_for_lookup(normalize_lookup_field(row.iloc[1])),
            normalize_lookup_field(row.iloc[2]),
            normalize_lookup_field(row.iloc[3]),
        )
        if key not in lookup:
            lookup[key] = []
        lookup[key].append(row)
    return lookup


def _build_lookup_key(devm_nolines):
    """Build the normalized 4-field lookup key used for sheet matching."""
    return (
        normalize_lookup_field(devm_nolines.get('name', '')),
        normalize_web_for_lookup(normalize_lookup_field(devm_nolines.get('web', ''))),
        normalize_lookup_field(devm_nolines.get('phas', '')),
        normalize_lookup_field(devm_nolines.get('phasnm', '')),
    )


def check_property_in_database(devm_nolines, devm_lookup, docs=None):
    """
    Check if property exists in database and if it has changed.
    
    Uses a pre-computed lookup dict (from build_lookup_index) for O(1) matching
    by (name, web, phas, phasnm).
    
    Then checks if all content values (sb1_date, sbe_date, sb1_text, sbe_text, sb_note, 
    rt_text, po1_text, po2_text, etc.) can be found anywhere in the matching database row(s).
    When multiple sheet rows share the same 4-key, every cell from all of those rows
    is combined into one set (OR semantics). SB / Examination Record label fields
    compare using the anchor title only (see clean_property_data).
    
    Returns:
        Tuple: (found, is_new, updates_list, missing_fields)
            - found: True if property exists with no changes (all values found in row)
            - is_new: True if property is new (not in database)
            - updates_list: List of formatted values not found in database row
            - missing_fields: List of field names that are missing (for PDF mapping)
    """
    found = False
    is_new = True
    updates_list = []
    missing_fields = []
    
    # Critical fields: name and web must be non-empty.
    critical_fields = ['name', 'web']
    critical_values = [devm_nolines.get(field) for field in critical_fields]
    critical_ok = all(v is not None and str(v).strip() != '' for v in critical_values)

    if not critical_ok:
        return found, is_new, updates_list, missing_fields
    
    # Step 1: O(1) lookup by (name, web, phas, phasnm); same rules as build_lookup_index
    key = _build_lookup_key(devm_nolines)
    
    matching_rows = devm_lookup.get(key)
    
    # If no match, it's a new project
    if matching_rows is None:
        return found, is_new, updates_list, missing_fields
    
    # Step 2: Property exists - check content values
    is_new = False

    # Content fields to check (static fields + dynamically discovered sb/rt/po text fields)
    content_fields = ['sb1_date', 'sbe_date', 'sbe_text', 'sb_note', 'rt_date', 'rt_note', 'po_note']
    sb_text_fields = sorted([k for k in devm_nolines.keys()
                             if k.startswith('sb') and k.endswith('_text') and k not in content_fields])
    content_fields.extend(sb_text_fields)
    rt_text_fields = sorted([k for k in devm_nolines.keys()
                             if k.startswith('rt') and k.endswith('_text')])
    content_fields.extend(rt_text_fields)
    po_text_fields = sorted([k for k in devm_nolines.keys()
                             if k.startswith('po') and k.endswith('_text')])
    content_fields.extend(po_text_fields)
    
    # Collect non-empty scraped values (normalize like sheet cells for comparison)
    scraped_values = []
    for field_name in content_fields:
        value = devm_nolines.get(field_name)
        base = normalize_lookup_field(str(value)) if value is not None else ""
        if not base:
            continue
        if field_name.startswith("po") and field_name.endswith("_text"):
            scraped_values.append(
                (
                    field_name,
                    normalize_compare_token_calendar_dates(
                        normalize_price_list_compare_token(base)
                    ),
                )
            )
        elif field_name in ("sb1_date", "sbe_date", "rt_date"):
            scraped_values.append(
                (field_name, normalize_compare_token_calendar_dates(base))
            )
        else:
            scraped_values.append((field_name, base))
    
    # Build set of all non-empty values across ALL duplicate rows
    row_values_set = set()
    for row in matching_rows:
        for col_idx in range(len(row)):
            raw_cell = row.iloc[col_idx]
            cell_value = normalize_lookup_field(raw_cell)
            if cell_value:
                row_values_set.add(cell_value)
                pl = normalize_price_list_compare_token(cell_value)
                row_values_set.add(pl)
                row_values_set.add(normalize_compare_token_calendar_dates(cell_value))
                row_values_set.add(normalize_compare_token_calendar_dates(pl))
            # Title-only token so SB / Examination Record cells with a file-size line
            # still match API/sheet rows that store only the first line.
            cell_sb_er = normalize_lookup_field(_sb_er_compare_source_text(raw_cell))
            if cell_sb_er:
                row_values_set.add(cell_sb_er)
    
    # Check each scraped value against the combined set
    rt_missing = []
    rt_matched_any = False

    for field_name, scraped_value in scraped_values:
        is_rt_field = field_name.startswith('rt') and field_name.endswith('_text')

        if scraped_value not in row_values_set:
            if is_rt_field:
                rt_missing.append((field_name, scraped_value))
            else:
                missing_fields.append(field_name)
                updates_list.append(f"{field_name}: {scraped_value}")
        else:
            if is_rt_field:
                rt_matched_any = True

    # Handle RT fields: only count as genuine changes if RT was previously stored
    # (i.e. at least one scraped RT value was found in the DB).
    # If no RT values matched, it's legacy data (RT was never scraped) — ignore.
    rt_has_data_in_db = rt_matched_any
    if rt_has_data_in_db:
        for field_name, scraped_value in rt_missing:
            missing_fields.append(field_name)
            updates_list.append(f"{field_name}: {scraped_value}")

    # If all non-legacy values found, it's a match (no changes)
    if len(updates_list) == 0:
        found = True

    return found, is_new, updates_list, missing_fields


def process_property_pdfs(
    pdfs,
    property_folder_id,
    sales_brochure_files_dir,
    register_of_transactions_files_dir,
    price_lists_files_dir,
    drive_service,
    prices_folder_id,
    transactions_folder_id,
    development_name,
    version,
    docs,
    missing_fields=None,
    devm_nolines=None,
    already_uploaded=None,
):
    """
    Download and process PDFs for a property (HTTP GET to SRPE, then Drive upload).

    PDF dicts are keyed by cleaned text value (e.g. '1350KB30 Jun 2014' → url).

    If missing_fields is None, download all PDFs (new property).
    Otherwise, look up each missing field's text value in devm_nolines and download
    only the specific PDF whose text key matches.

    already_uploaded: optional set of text keys already uploaded (for this property).
    On retry after timeout we only download PDFs not in this set (resume where we left off).

    Returns False normally; True signals retry (historical compat; HTTP path rarely returns True).
    """
    if missing_fields is None:
        # New property: download everything (minus already_uploaded on retry)
        sb_pdfs = pdfs['sales_brochure_pdf']
        rt_pdfs = pdfs['register_of_transactions_pdf']
        po_pdfs = pdfs['price_orders_pdf']
    else:
        # Collect the cleaned text values for all missing fields
        missing_values = set()
        if devm_nolines:
            for f in missing_fields:
                val = devm_nolines.get(f, '')
                if val:
                    missing_values.add(str(val).strip())

        # Filter SB and PO: only download specific PDFs whose text key matches
        sb_pdfs = {k: v for k, v in pdfs['sales_brochure_pdf'].items() if k in missing_values}
        po_pdfs = {k: v for k, v in pdfs['price_orders_pdf'].items() if k in missing_values}

        # RT: download specific changed RT PDFs
        rt_pdfs = {k: v for k, v in pdfs['register_of_transactions_pdf'].items() if k in missing_values}

        # Always download ALL RT when PO or SB triggers a download
        if sb_pdfs or po_pdfs:
            rt_pdfs = pdfs['register_of_transactions_pdf']

    # Resume: skip PDFs already uploaded (on retry after timeout)
    if already_uploaded:
        sb_pdfs = {k: v for k, v in sb_pdfs.items() if k not in already_uploaded}
        rt_pdfs = {k: v for k, v in rt_pdfs.items() if k not in already_uploaded}
        po_pdfs = {k: v for k, v in po_pdfs.items() if k not in already_uploaded}

    def _dl(pdf_dict, dest_dir):
        return download_pdf_http(
            pdf_dict, dest_dir, property_folder_id, drive_service,
            prices_folder_id, transactions_folder_id,
            development_name, version, docs,
            already_uploaded=already_uploaded,
        )

    # Download filtered sales brochure PDFs
    if sb_pdfs:
        if _dl(sb_pdfs, sales_brochure_files_dir):
            return True

    # Download register of transactions PDFs
    if rt_pdfs:
        if _dl(rt_pdfs, register_of_transactions_files_dir):
            return True

    # Download filtered price orders PDFs
    if po_pdfs:
        if _dl(po_pdfs, price_lists_files_dir):
            return True

    return False

