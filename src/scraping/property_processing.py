"""
Property Processing Module
Handles extraction, validation, and processing of property developments
"""

import time
from selenium.webdriver.support.ui import WebDriverWait
from src.extractors.sales_brochure import sales_brochure
from src.extractors.register_of_transactions import register_of_transactions
from src.extractors.price_orders import price_orders
from src.google_services import update_log, create_drive_folder, insert_new_data, get_or_create_drive_folder
from .browser import launch_web
from .file_download import download_pdf


def extract_property_data(driver, row_index):
    """
    Extract property information from the listing table row.
    
    Returns:
        Dictionary containing property data (name, web, phas, phasnm, addr, area, date)
    """
    devm = {}
    
    el_devm = driver.find_element("xpath", f"//*[@id='sort_table']/tbody/tr[{row_index}]/td[1]/div/a")
    devm['name'] = el_devm.text
    devm['web'] = driver.find_element("xpath", f"//*[@id='sort_table']/tbody/tr[{row_index}]/td[1]/div/div/a").get_attribute("href")
    devm['phas'] = driver.find_element("xpath", f"//*[@id='sort_table']/tbody/tr[{row_index}]/td[2]").text
    devm['phasnm'] = driver.find_element("xpath", f"//*[@id='sort_table']/tbody/tr[{row_index}]/td[3]").text
    devm['addr'] = driver.find_element("xpath", f"//*[@id='sort_table']/tbody/tr[{row_index}]/td[4]").text
    devm['area'] = driver.find_element("xpath", f"//*[@id='sort_table']/tbody/tr[{row_index}]/td[5]").text
    devm['date'] = driver.find_element("xpath", f"//*[@id='sort_table']/tbody/tr[{row_index}]/td[6]").text
    
    return devm


def navigate_and_extract_pdfs(driver, page_url, webload_timeout, docs, devm):
    """
    Navigate to property page and extract all PDF links.
    
    
    Returns:
        Dictionary with keys: 'sales_brochure_pdf', 'register_of_transactions_pdf', 'price_orders_pdf'
        Returns None if data is not fully available
    """
    # Navigate to property page
    driver.get(page_url)
    
    # Extract sales brochure information
    el_sb = []
    for attempt in range(2):
        try:
            el_sb = WebDriverWait(driver, webload_timeout).until(
                lambda d: d.find_elements("xpath", "//*[@id='brochure']/div[2]/table/tbody/tr")
            )
            break  # Success, exit loop
        except Exception:
            if attempt == 0:
                # Retry navigation on first failure
                driver.get(page_url)
            # If second attempt fails, el_sb remains []
    
    # Check if at least three rows are found (data is complete)
    if len(el_sb) <= 2:
        update_log(docs, f"Data not full available. Skipping this development.\n\n")
        return None
    
    # Extract PDFs from all sources (dicts keyed by cleaned text value → URL)
    pdfs = {
        'sales_brochure_pdf': {},
        'register_of_transactions_pdf': {},
        'price_orders_pdf': {},
    }
    
    sales_brochure(webload_timeout, docs, devm, el_sb, driver, pdfs['sales_brochure_pdf'])
    register_of_transactions(webload_timeout, docs, devm, driver, pdfs['register_of_transactions_pdf'])
    price_orders(webload_timeout, docs, devm, driver, pdfs['price_orders_pdf'])
    
    return pdfs


def clean_property_data(devm):
    """
    Remove newlines and strip whitespace from property data.
    """
    return {
        key: value.replace('\n', '').strip() if isinstance(value, str) else value
        for key, value in devm.items()
    }


def normalize_addr_for_lookup(addr):
    """
    Normalize address for lookup so that 'Sales suspended' and 'Sales terminated'
    do not affect matching. Removes these labels and trims whitespace.
    """
    if not addr or not isinstance(addr, str):
        return str(addr).strip() if addr is not None else ''
    s = str(addr).strip()
    for label in ('Sales suspended', 'Sales terminated'):
        if label in s:
            s = s.replace(label, '')
    return ' '.join(s.split())


def build_lookup_index(devm_df):
    """
    Build a dictionary index from the database DataFrame for O(1) property lookups.
    
    Key: (name, web, phas, phasnm, addr, area) tuple; addr is normalized (Sales suspended/terminated removed).
    Value: list of all matching rows (to handle duplicates)
    
    Call this once after loading devm_df, then pass the lookup to check_property_in_database.
    """
    lookup = {}
    for _, row in devm_df.iterrows():
        raw_addr = str(row.iloc[4]).strip()
        key = (
            str(row.iloc[0]).strip(),  # name
            str(row.iloc[1]).strip(),  # web
            str(row.iloc[2]).strip(),  # phas
            str(row.iloc[3]).strip(),  # phasnm
            normalize_addr_for_lookup(raw_addr),  # addr (normalized)
            str(row.iloc[5]).strip(),  # area
        )
        if key not in lookup:
            lookup[key] = []
        lookup[key].append(row)
    return lookup


def check_property_in_database(devm_nolines, devm_lookup):
    """
    Check if property exists in database and if it has changed.
    
    Uses a pre-computed lookup dict (from build_lookup_index) for O(1) matching
    by (name, web, phas, phasnm, addr, area).
    
    Then checks if all content values (sb1_date, sbe_date, sb1_text, sbe_text, sb_note, 
    rt_text, po1_text, po2_text, etc.) can be found anywhere in the matching database row.
    
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
    
    # Critical fields that must be non-empty (phas and phasnm can be empty strings)
    critical_fields = ['name', 'web', 'addr', 'area']
    critical_values = [devm_nolines.get(field) for field in critical_fields]
    
    if not all(v is not None and v != '' for v in critical_values):
        return found, is_new, updates_list, missing_fields
    
    # Step 1: O(1) lookup by (name, web, phas, phasnm, addr, area); addr normalized for comparison
    key = (
        str(devm_nolines.get('name', '')).strip(),
        str(devm_nolines.get('web', '')).strip(),
        str(devm_nolines.get('phas', '')).strip(),
        str(devm_nolines.get('phasnm', '')).strip(),
        normalize_addr_for_lookup(devm_nolines.get('addr', '')),
        str(devm_nolines.get('area', '')).strip(),
    )
    
    matching_rows = devm_lookup.get(key)
    
    # If no match, it's a new project
    if matching_rows is None:
        print(f"[DEBUG] No lookup match for key: {key}")
        return found, is_new, updates_list, missing_fields
    
    # Step 2: Property exists - check content values
    is_new = False
    property_name = devm_nolines.get('name', 'UNKNOWN')
    print(f"\n[DEBUG] ===== Checking content for: {property_name} ({len(matching_rows)} duplicate rows) =====")
    
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
    
    # Collect non-empty scraped values
    scraped_values = []
    for field_name in content_fields:
        value = devm_nolines.get(field_name)
        if value is not None and str(value).strip() != '':
            scraped_values.append((field_name, str(value).strip()))
    
    print(f"[DEBUG] Scraped values ({len(scraped_values)}):")
    for fn, sv in scraped_values:
        print(f"[DEBUG]   {fn}: {repr(sv)}")
    
    # Build set of all non-empty values across ALL duplicate rows
    row_values_set = set()
    for row in matching_rows:
        for col_idx in range(len(row)):
            cell_value = str(row.iloc[col_idx]).strip()
            if cell_value:
                row_values_set.add(cell_value)
    
    print(f"[DEBUG] Combined DB values from {len(matching_rows)} rows ({len(row_values_set)} unique):")
    for rv in sorted(row_values_set):
        print(f"[DEBUG]   {repr(rv)}")
    
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
            print(f"[DEBUG] ❌ MISSING: {field_name} = {repr(scraped_value)}")
        else:
            if is_rt_field:
                rt_matched_any = True
            print(f"[DEBUG] ✓ MATCH: {field_name}")

    # Handle RT fields: only count as genuine changes if RT was previously stored
    # (i.e. at least one scraped RT value was found in the DB).
    # If no RT values matched, it's legacy data (RT was never scraped) — ignore.
    rt_has_data_in_db = rt_matched_any
    if rt_has_data_in_db:
        for field_name, scraped_value in rt_missing:
            missing_fields.append(field_name)
            updates_list.append(f"{field_name}: {scraped_value}")
        print(f"[DEBUG] RT has data in DB — {len(rt_missing)} RT fields are genuine changes")
    else:
        print(f"[DEBUG] RT legacy (no RT data in DB) — ignoring {len(rt_missing)} missing RT fields")

    print(f"[DEBUG] Result: {len(missing_fields)} missing out of {len(scraped_values)} (excl legacy RT)")
    
    # If all non-legacy values found, it's a match (no changes)
    if len(updates_list) == 0:
        found = True
    
    return found, is_new, updates_list, missing_fields


def process_property_pdfs(
    driver,
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
    Download and process PDFs for a property.

    PDF dicts are keyed by cleaned text value (e.g. '1350KB30 Jun 2014' → url).

    If missing_fields is None, download all PDFs (new property).
    Otherwise, look up each missing field's text value in devm_nolines and download
    only the specific PDF whose text key matches.

    already_uploaded: optional set of text keys already uploaded (for this property).
    On retry after timeout we only download PDFs not in this set (resume where we left off).
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

        print(f"[DEBUG] Granular download: "
              f"sb={len(sb_pdfs)}/{len(pdfs['sales_brochure_pdf'])}, "
              f"rt={len(rt_pdfs)}/{len(pdfs['register_of_transactions_pdf'])}, "
              f"po={len(po_pdfs)}/{len(pdfs['price_orders_pdf'])}")

    # Resume: skip PDFs already uploaded (on retry after timeout)
    if already_uploaded:
        sb_pdfs = {k: v for k, v in sb_pdfs.items() if k not in already_uploaded}
        rt_pdfs = {k: v for k, v in rt_pdfs.items() if k not in already_uploaded}
        po_pdfs = {k: v for k, v in po_pdfs.items() if k not in already_uploaded}
        print(f"[DEBUG] Resuming: skipping {len(already_uploaded)} already-uploaded PDF(s)")

    # Download filtered sales brochure PDFs
    if sb_pdfs:
        timeout_download = download_pdf(
            driver, sb_pdfs, sales_brochure_files_dir,
            property_folder_id, drive_service, prices_folder_id,
            transactions_folder_id, development_name, version, docs,
            already_uploaded=already_uploaded,
        )
        if timeout_download:
            return True

    # Download register of transactions PDFs
    if rt_pdfs:
        timeout_download = download_pdf(
            driver, rt_pdfs, register_of_transactions_files_dir,
            property_folder_id, drive_service, prices_folder_id,
            transactions_folder_id, development_name, version, docs,
            already_uploaded=already_uploaded,
        )
        if timeout_download:
            return True

    # Download filtered price orders PDFs
    if po_pdfs:
        timeout_download = download_pdf(
            driver, po_pdfs, price_lists_files_dir,
            property_folder_id, drive_service, prices_folder_id,
            transactions_folder_id, development_name, version, docs,
            already_uploaded=already_uploaded,
        )
        if timeout_download:
            return True

    return False


def restart_browser(driver, target_web, webload_timeout, chrome_exe_path, delay=2):
    """
    Restart the browser to recover from errors or timeouts.
    """
    driver.quit()
    time.sleep(delay)
    return launch_web(target_web, webload_timeout=webload_timeout, chrome_exe_path=chrome_exe_path)


def process_single_property(
    driver,
    row_index,
    target_web,
    webload_timeout,
    chrome_exe_path,
    devm_lookup,
    sheet,
    run_folder_id,
    sales_brochure_files_dir,
    register_of_transactions_files_dir,
    price_lists_files_dir,
    drive_service,
    prices_folder_id,
    transactions_folder_id,
    version,
    docs,
    cached_folder_ids,
    already_uploaded_pdfs,
    logged_updates_for_rows=None,
):
    """
    Process a single property: extract data, check database, download PDFs.
    
    
    Returns:
        Tuple: (timeout_occurred, driver)
            - timeout_occurred: True if download timeout occurred
            - driver: WebDriver instance (may be restarted)
    """
    start_time = time.time()
    is_retry = logged_updates_for_rows is not None and row_index in logged_updates_for_rows

    # Extract property data from table
    devm = extract_property_data(driver, row_index)
    name_cleaned = devm['name'].replace('\n', '')
    if not is_retry:
        update_log(docs, f"==== Development {row_index} {name_cleaned} begins ====\n")

    # Navigate to property page and extract PDFs
    page_url = driver.find_element("xpath", f"//*[@id='sort_table']/tbody/tr[{row_index}]/td[1]/div/a").get_attribute("href")
    pdfs = navigate_and_extract_pdfs(driver, page_url, webload_timeout, docs, devm)
    
    if pdfs is None:
        # Data incomplete, skip this property
        driver.back()
        return False, driver
    
    # Clean property data and check database
    devm_nolines = clean_property_data(devm)
    found, is_new, updates_list, missing_fields = check_property_in_database(devm_nolines, devm_lookup)
    
    # Skip if property exists with no changes
    if found:
        driver.back()
        elapsed = (time.time() - start_time) / 60
        update_log(docs, f"finished devm {row_index} in {elapsed:.2f} min\n\n")
        return False, driver

    # Only insert/log updates when there are non-metadata changes.
    # If ONLY metadata-style notice fields differ, skip downloads and DB insert.
    metadata_only_fields = {'sb_note', 'sbe_date', 'rt_note', 'rt_date', 'po_note'}
    only_metadata_changed = (
        not is_new
        and missing_fields
        and all(f in metadata_only_fields for f in missing_fields)
    )
    if only_metadata_changed:
        driver.back()
        elapsed = (time.time() - start_time) / 60
        update_log(docs, f"finished devm {row_index} in {elapsed:.2f} min\n\n")
        return False, driver
    
    # Log new or updated property (skip long \"New File\" / \"Updates\" log on retry to avoid repetition)
    if is_new:
        if logged_updates_for_rows is not None and row_index in logged_updates_for_rows:
            update_log(docs, f"Retrying row {row_index} ({devm_nolines['name']}) — continuing downloads for new file.\\n")
        else:
            update_log(docs, f"New File: {devm_nolines['name']}\\n")
            if logged_updates_for_rows is not None:
                logged_updates_for_rows.add(row_index)
        # New property: download all PDFs
        missing_fields = None  # None means download all
    else:
        if logged_updates_for_rows is not None and row_index in logged_updates_for_rows:
            update_log(docs, f"Retrying row {row_index} ({devm_nolines['name']}) — updates unchanged.\\n")
        else:
            update_log(docs, f"Updates to Existing File: {devm_nolines['name']}\\n" + '\\n'.join([f'updated {u}' for u in updates_list]) + '\\n')
            if logged_updates_for_rows is not None:
                logged_updates_for_rows.add(row_index)
        # Existing property: only download PDFs for missing fields
    
    # Create property folder (or reuse cached one from a previous timeout retry;
    # or reuse existing folder by name after script restart to avoid duplicates)
    if devm_nolines['name'] in cached_folder_ids:
        property_folder_id = cached_folder_ids[devm_nolines['name']]
    else:
        property_folder_id = get_or_create_drive_folder(
            devm_nolines['name'], run_folder_id, drive_service
        )
        cached_folder_ids[devm_nolines['name']] = property_folder_id

    # Resume: only download PDFs not yet uploaded (on retry after timeout)
    timeout_occurred = process_property_pdfs(
        driver=driver,
        pdfs=pdfs,
        property_folder_id=property_folder_id,
        sales_brochure_files_dir=sales_brochure_files_dir,
        register_of_transactions_files_dir=register_of_transactions_files_dir,
        price_lists_files_dir=price_lists_files_dir,
        drive_service=drive_service,
        prices_folder_id=prices_folder_id,
        transactions_folder_id=transactions_folder_id,
        development_name=devm_nolines['name'],
        version=version,
        docs=docs,
        missing_fields=missing_fields,
        devm_nolines=devm_nolines,
        already_uploaded=already_uploaded_pdfs,
    )
    
    # Restart browser if timeout occurred (logging already handled at PDF level)
    if timeout_occurred:
        driver = restart_browser(driver, target_web, webload_timeout, chrome_exe_path)
        return True, driver
    
    # Update database with new property data
    insert_new_data(sheet, devm)

    # Clear cached folder and uploaded-PDF set since property completed successfully
    cached_folder_ids.pop(devm_nolines['name'], None)
    already_uploaded_pdfs.clear()
    if logged_updates_for_rows is not None:
        logged_updates_for_rows.discard(row_index)
    
    # Return to listing page
    driver.back()
    elapsed = (time.time() - start_time) / 60
    update_log(docs, f"finished devm {row_index} in {elapsed:.2f} min\n\n")
    
    return False, driver

