import csv
import io
import re
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def find_occurrence_file(filenames: List[str]) -> Optional[str]:
    """
    Find a file matching occurrence pattern (case-insensitive).
    Patterns: occurrence.*, Occurrence.*, occ.*
    """
    pattern = re.compile(r'^(occurrence|Occurrence|occ)\..*$', re.IGNORECASE)
    for filename in filenames:
        if pattern.match(filename):
            return filename
    return None


def detect_delimiter(content: bytes, sample_size: int = 100) -> str:
    """
    Detect the delimiter (comma or tab) in the file content using csv.Sniffer.
    Raises ValueError if delimiter cannot be detected.
    """
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        try:
            text = content.decode('latin-1')
        except UnicodeDecodeError:
            text = content.decode('utf-8', errors='ignore')
    
    sample = text[:sample_size] if len(text) > sample_size else text
    
    sniffer = csv.Sniffer()
    try:
        delimiter = sniffer.sniff(sample, delimiters='\t,').delimiter
        return delimiter
    except csv.Error as e:
        raise ValueError(f"Could not detect delimiter (comma or tab) in file: {e}")


def parse_separated_file(content: bytes, delimiter: Optional[str] = None) -> Tuple[List[Dict[str, str]], str]:
    """
    Parse a separated text file (CSV/TSV) to a list of dictionaries.
    
    Args:
        content: File content as bytes
        delimiter: Optional delimiter (comma or tab). If None, will be auto-detected.
    
    Returns:
        Tuple of (list of dictionaries, detected delimiter)
    """
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        try:
            text = content.decode('latin-1')
        except UnicodeDecodeError:
            text = content.decode('utf-8', errors='ignore')
    
    if delimiter is None:
        delimiter = detect_delimiter(content)
    
    file_like = io.StringIO(text)
    
    reader = csv.DictReader(file_like, delimiter=delimiter)
    
    rows = []
    for row in reader:
        clean_row = {}
        for k, v in row.items():
            if v is None:
                clean_row[k] = ''
            elif isinstance(v, str):
                clean_row[k] = v.strip()
            elif isinstance(v, (list, tuple)):
                clean_row[k] = ', '.join(str(item).strip() for item in v)
            else:
                clean_row[k] = str(v).strip()
        rows.append(clean_row)
    
    return rows, delimiter


def process_uploaded_files(files_data: List[Dict]) -> Dict:
    """
    Process uploaded files, looking for occurrence files and parsing them.
    
    Args:
        files_data: List of dicts with 'filename' and 'content' keys
    
    Returns:
        Dict with processing results
    """
    filenames = [f['filename'] for f in files_data]
    occurrence_filename = find_occurrence_file(filenames)
    
    result = {
        'occurrence_file_found': occurrence_filename is not None,
        'occurrence_filename': occurrence_filename,
        'parsed_data': None,
        'error': None
    }
    
    if occurrence_filename:
        occurrence_file = next(
            (f for f in files_data if f['filename'] == occurrence_filename),
            None
        )
        
        if occurrence_file:
            try:
                parsed, detected_delimiter = parse_separated_file(occurrence_file['content'])
                result['row_count'] = len(parsed)
                result['detected_delimiter'] = detected_delimiter
                
                if parsed:
                    result['column_count'] = len(parsed[0])
                    result['columns'] = list(parsed[0].keys())
                    result['parsed_data'] = parsed[:10]
                else:
                    result['parsed_data'] = []
                    result['error'] = 'File is empty or has no data rows'
            except Exception as e:
                logger.error(f"Error processing file: {e}", exc_info=True)
                result['error'] = str(e)
                import traceback
                result['traceback'] = traceback.format_exc()
    
    return result

