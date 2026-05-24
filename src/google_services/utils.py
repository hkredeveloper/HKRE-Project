"""
Utility Functions Module
File size formatting and download decision logic
"""

import os
import pandas as pd


def format_file_size(size_bytes):
    """
    Convert file size in bytes to human readable format (e.g., '610 KB')
    
    Args:
        size_bytes: Size in bytes
    
    Returns:
        Human-readable size string
    """
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.0f} {size_names[i]}"


def parse_file_size(size_str):
    """
    Parse file size string (e.g., '610 KB') to bytes
    
    Args:
        size_str: Human-readable size string
    
    Returns:
        Size in bytes
    """
    if not size_str or pd.isna(size_str):
        return 0
    
    size_str = str(size_str).strip().upper()
    size_names = ["B", "KB", "MB", "GB"]
    
    for i, unit in enumerate(size_names):
        if unit in size_str:
            try:
                number = float(size_str.replace(unit, "").strip())
                return int(number * (1024 ** i))
            except ValueError:
                return 0
    
    return 0


def should_download_file(filename, file_path, filenames_df, development_name):
    """
    Check if file should be downloaded based on filename and size comparison
    
    Args:
        filename: Name of the file
        file_path: Local path where file would be saved
        filenames_df: DataFrame containing file records
        development_name: Name of the development
    
    Returns:
        True if should download, False if should skip
    """
    if filenames_df.empty:
        return True
    
    # Check if filename exists in database
    matching_rows = filenames_df[filenames_df['File Name'] == filename]
    
    if matching_rows.empty:
        return True  # File not in database, should download
    
    # File exists in database, check size
    if os.path.exists(file_path):
        current_size = os.path.getsize(file_path)
        current_size_str = format_file_size(current_size)
        recorded_size_str = matching_rows.iloc[0]['File Size']
        recorded_size = parse_file_size(recorded_size_str)
        if current_size != recorded_size:
            return True
        return False
    return True

