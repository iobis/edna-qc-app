import csv
import io
import re
import logging
from typing import List, Dict, Optional, Tuple

import requests

from analysis import analyze_species_occurrences

logger = logging.getLogger(__name__)

WORMS_BATCH_URL = "https://www.marinespecies.org/rest/AphiaRecordsByAphiaIDs"


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


def extract_species_occurrences(parsed_data: List[Dict]) -> List[Dict]:
    """
    Extract unique species occurrences from parsed data.
    Requires exact column names: scientificName, scientificNameID, decimalLongitude, decimalLatitude.
    Coordinates are rounded to 1 decimal place, and only unique combinations are returned.
    
    Args:
        parsed_data: List of dictionaries from parsed file
    
    Returns:
        List of unique dictionaries with scientificName, scientificNameID, decimalLongitude, decimalLatitude
    """
    if not parsed_data:
        return []
    
    first_row = parsed_data[0]
    required_columns = ['scientificName', 'scientificNameID', 'decimalLongitude', 'decimalLatitude']
    
    missing_columns = [col for col in required_columns if col not in first_row]
    if missing_columns:
        raise ValueError(
            f"Missing required columns: {missing_columns}. "
            f"Found columns: {list(first_row.keys())}"
        )
    
    unique_occurrences: Dict[Tuple[str, str, Optional[float], Optional[float]], Dict] = {}
    
    for row in parsed_data:
        scientific_name = row.get('scientificName', '').strip()
        scientific_name_id = row.get('scientificNameID', '').strip()
        phylum = row.get('phylum', '').strip()
        lon_str = row.get('decimalLongitude', '').strip()
        lat_str = row.get('decimalLatitude', '').strip()
        
        try:
            lon = round(float(lon_str), 1) if lon_str else None
        except (ValueError, TypeError):
            lon = None
        
        try:
            lat = round(float(lat_str), 1) if lat_str else None
        except (ValueError, TypeError):
            lat = None
        
        aphiaid_match = re.search(r"(\d+)$", scientific_name_id) if scientific_name_id else None
        aphiaid = int(aphiaid_match.group(1)) if aphiaid_match else None
        
        key = (scientific_name, scientific_name_id, lon, lat)
        
        if key not in unique_occurrences:
            unique_occurrences[key] = {
                'scientificName': scientific_name,
                'scientificNameID': scientific_name_id,
                'phylum': phylum,
                'decimalLongitude': lon,
                'decimalLatitude': lat,
                'aphiaid': aphiaid,
            }
    
    return list(unique_occurrences.values())


def normalize_aphiaids(occurrences: List[Dict]) -> None:
    """
    Replace AphiaIDs with their accepted (valid) AphiaIDs using the WoRMS API.

    This function modifies the occurrences list in place.
    """
    unique_ids = sorted(
        {occ["aphiaid"] for occ in occurrences if occ.get("aphiaid") is not None}
    )

    if not unique_ids:
        return

    logger.info("Normalizing %d AphiaIDs via WoRMS", len(unique_ids))

    id_to_valid: Dict[int, int] = {}

    batch_size = 50
    for i in range(0, len(unique_ids), batch_size):
        batch = unique_ids[i : i + batch_size]
        params = [("aphiaids[]", str(aid)) for aid in batch]
        try:
            response = requests.get(WORMS_BATCH_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.info("Failed to normalize AphiaIDs batch %s: %s", batch, exc)
            continue

        for record in data:
            if not record:
                continue
            try:
                original = record.get("AphiaID")
                valid = record.get("valid_AphiaID") or original
                if original is not None and valid is not None:
                    id_to_valid[int(original)] = int(valid)
            except Exception:
                continue

    if not id_to_valid:
        return

    for occ in occurrences:
        aphiaid = occ.get("aphiaid")
        if aphiaid in id_to_valid:
            occ["aphiaid"] = id_to_valid[aphiaid]


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
                    
                    filtered_parsed = [
                        row for row in parsed 
                        if row.get('taxonRank', '').strip().lower() == 'species'
                    ]
                    result['filtered_row_count'] = len(filtered_parsed)
                    
                    try:
                        occurrences = extract_species_occurrences(filtered_parsed)
                        # Normalize AphiaIDs to their accepted (valid) IDs via WoRMS
                        normalize_aphiaids(occurrences)
                        result['original_occurrence_count'] = len(filtered_parsed)
                        result['unique_occurrence_count'] = len(occurrences)
                        analyzed_occurrences = analyze_species_occurrences(occurrences)
                        result['analyzed_occurrences'] = analyzed_occurrences
                        result['analyzed_count'] = len(analyzed_occurrences)
                    except ValueError as e:
                        result['analysis_error'] = str(e)
                else:
                    result['parsed_data'] = []
                    result['error'] = 'File is empty or has no data rows'
            except Exception as e:
                logger.error(f"Error processing file: {e}", exc_info=True)
                result['error'] = str(e)
                import traceback
                result['traceback'] = traceback.format_exc()
    
    return result

