"""
Google Sheets Operations Module
Handles all interactions with Google Sheets
"""
import logging

import os
import pandas as pd
from datetime import datetime
from .utils import format_file_size


_log = logging.getLogger("hkre.google_services.sheets")


def number_to_column_name(n):
    """
    Convert a number to Excel column name (e.g., 1 -> 'A', 27 -> 'AA')
    """
    result = []
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result.append(chr(65 + remainder))  # 65 = 'A'
    return ''.join(reversed(result))


def get_devm(spreadsheet, version):
    """
    Get the development database from Google Sheets.
    
    Args:
        spreadsheet: Google Sheets client object
        version: Either "t18m" or "non-t18m"
    
    Returns:
        DataFrame and worksheet object
    """
    # Check what version is it and open the sheet accordingly
    if version == "t18m":
        sheet = spreadsheet.worksheet("devm t18m")
    else:
        sheet = spreadsheet.worksheet("devm non-t18m")

    # Retrieve all values from devm
    values = sheet.get_all_values()
    df = pd.DataFrame(values)
    df = df.iloc[1:, 2:]
    return df, sheet


def get_both_devm(spreadsheet):
    """
    Load both devm tabs and return their worksheets plus a combined DataFrame.
    Used so t18m and non-t18m runs share one combined database for comparison.

    """
    sheet_t18m = spreadsheet.worksheet("devm t18m")
    sheet_non_t18m = spreadsheet.worksheet("devm non-t18m")

    values_t18m = sheet_t18m.get_all_values()
    values_non_t18m = sheet_non_t18m.get_all_values()

    df_t18m = pd.DataFrame(values_t18m).iloc[1:, 2:]
    df_non_t18m = pd.DataFrame(values_non_t18m).iloc[1:, 2:]

    combined_df = pd.concat([df_t18m, df_non_t18m], ignore_index=True)
    return sheet_t18m, sheet_non_t18m, combined_df


def get_filenames_sheet(spreadsheet):
    """
    Get the 'Filenames' worksheet and return as DataFrame
    
    Args:
        spreadsheet: Google Sheets client object
    
    Returns:
        DataFrame and worksheet object
    """
    try:
        sheet = spreadsheet.worksheet("Filenames")
        values = sheet.get_all_values()
        if not values:
            return pd.DataFrame(columns=['File Name', 'File Size', 'Date Modified', 'Development', 'Devm Type']), sheet
        
        # Create DataFrame with headers
        df = pd.DataFrame(values[1:], columns=values[0])
        return df, sheet
    except Exception as e:
        _log.warning("Error accessing Filenames sheet: %s", e)
        # Create empty DataFrame with expected columns
        return pd.DataFrame(columns=['File Name', 'File Size', 'Date Modified', 'Development', 'Devm Type']), None


def insert_new_data(sheet, devm):
    """
    Insert new property data into the development sheet.
    
    Args:
        sheet: Google Sheets worksheet object
        devm: Dictionary containing property data
    """
    # Inserting a new row for new data 
    new_row = [""] * sheet.col_count  
    sheet.insert_row(new_row, 2)  

    # Inserting the new data from devm website
    row_data = list(devm.values())

    # Calculate column range (data starts at column C = index 3)
    end_col_index = 3 + len(row_data) - 1
    end_col = number_to_column_name(end_col_index)
    sheet.update([row_data], f"C2:{end_col}2")

    # Insert the Date of last update and check if there is a duplicate
    sheet.update("A2", [["=COUNTIFS($C$1:$C, C2, $E$1:$E, E2, $F$1:$F, F2) > 1"]], value_input_option="USER_ENTERED")
    today_date = datetime.today().strftime('%d/%m/%Y') 
    sheet.update("B2", [[today_date]], value_input_option="USER_ENTERED")

    # Apply text wrap to note fields
    keys_list = list(devm.keys())
    for field in ['sb_note', 'rt_note', 'po_note']:
        if field in keys_list:
            col_index = 3 + keys_list.index(field)
            col_letter = number_to_column_name(col_index)
            sheet.format(f"{col_letter}2", {"wrapStrategy": "WRAP"})


def add_file_to_database(sheet, filename, file_path, development_name, devm_type):
    """
    Add a new file record to the Filenames database at the top (row 2)
    
    """
    if not sheet or not os.path.exists(file_path):
        return
    
    try:
        # Get file size and modification date
        file_size = os.path.getsize(file_path)
        file_size_str = format_file_size(file_size)
        
        # Get file modification time
        mod_time = os.path.getmtime(file_path)
        mod_date = datetime.fromtimestamp(mod_time).strftime('%d %b %Y at %I:%M %p')
        
        # Prepare new row data with Devm Type column
        new_row = [filename, file_size_str, mod_date, development_name, devm_type]
        
        # Insert new row at the top (row 2, after header)
        sheet.insert_row(new_row, 2)
        _log.info("Added %s to Filenames database at the top", filename)

    except Exception as e:
        _log.warning("Error adding file to database: %s", e)

