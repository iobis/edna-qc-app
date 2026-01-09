import csv
import io
import re
import logging
from typing import List, Dict, Optional, Tuple

import requests

from analysis import analyze_species_occurrences

logger = logging.getLogger(__name__)

WORMS_BATCH_URL = "https://www.marinespecies.org/rest/AphiaRecordsByAphiaIDs"
WORMS_MATCH_NAMES_URL = "https://www.marinespecies.org/rest/AphiaRecordsByMatchNames"


def find_occurrence_file(filenames: List[str]) -> Optional[str]:
    """
    Find a file matching occurrence pattern (case-insensitive).
    Patterns: occurrence.*, Occurrence.*, occ.*
    """
    pattern = re.compile(r'.*occ.*(txt|csv|tsv)', re.IGNORECASE)
    for filename in filenames:
        print(f"Found occurrence file: {filename}")
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


def extract_species_occurrences(parsed_data: List[Dict], name_matches: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """
    Extract unique species occurrences from parsed data.
    Requires exact column names: scientificName, decimalLongitude, decimalLatitude.
    scientificNameID is optional - if missing, will use name_matches if provided.
    Coordinates are rounded to 1 decimal place, and only unique combinations are returned.
    
    Args:
        parsed_data: List of dictionaries from parsed file
        name_matches: Optional dictionary mapping scientific names to WoRMS match data
    
    Returns:
        List of unique dictionaries with scientificName, scientificNameID, decimalLongitude, decimalLatitude
    """
    if not parsed_data:
        return []
    
    first_row = parsed_data[0]
    required_columns = ['scientificName', 'decimalLongitude', 'decimalLatitude']
    
    missing_columns = [col for col in required_columns if col not in first_row]
    if missing_columns:
        logging.error(f"Missing required columns: {missing_columns}. Found columns: {list(first_row.keys())}")
        raise ValueError(
            f"Missing required columns: {missing_columns}. "
            f"Found columns: {list(first_row.keys())}"
        )
    
    unique_occurrences: Dict[Tuple[str, Optional[str], Optional[float], Optional[float]], Dict] = {}
    
    for row in parsed_data:
        scientific_name = row.get('scientificName', '').strip()
        scientific_name_id = row.get('scientificNameID', '').strip()
        phylum = row.get('phylum', '').strip()
        class_name = row.get('class', '').strip()
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
        
        # If scientificNameID is missing and we have name matches, use the match
        rank = None
        if not scientific_name_id and name_matches and scientific_name in name_matches:
            match = name_matches[scientific_name]
            scientific_name_id = match.get('scientificNameID', '')
            if not phylum:
                phylum = match.get('phylum', '')
            if not class_name:
                class_name = match.get('class', '')
            aphiaid = match.get('aphiaid')
            rank = match.get('rank', '')
        else:
            # Extract aphiaid from scientificNameID if present
            aphiaid_match = re.search(r"(\d+)$", scientific_name_id) if scientific_name_id else None
            aphiaid = int(aphiaid_match.group(1)) if aphiaid_match else None
        
        key = (scientific_name, scientific_name_id, lon, lat)
        
        if key not in unique_occurrences:
            unique_occurrences[key] = {
                'scientificName': scientific_name,
                'scientificNameID': scientific_name_id,
                'phylum': phylum,
                'class': class_name,
                'decimalLongitude': lon,
                'decimalLatitude': lat,
                'aphiaid': aphiaid,
            }
            if rank:
                unique_occurrences[key]['rank'] = rank
    
    return list(unique_occurrences.values())


def match_names_with_worms(scientific_names: List[str]) -> Dict[str, Dict]:
    """
    Match scientific names with WoRMS using the match names API.
    Only returns exact matches (match_type="exact"), using the first match for each name.
    
    Args:
        scientific_names: List of unique scientific names to match
    
    Returns:
        Dictionary mapping scientific name to match data with keys:
        - aphiaid: valid AphiaID
        - phylum: phylum name
        - class: class name
        - scientificNameID: LSID constructed from AphiaID
    """
    if not scientific_names:
        return {}
    
    name_to_match: Dict[str, Dict] = {}
    
    batch_size = 50
    for i in range(0, len(scientific_names), batch_size):
        batch = scientific_names[i : i + batch_size]
        params = [
            ("marine_only", "false"),
            ("extant_only", "false"),
            ("match_authority", "false")
        ]
        for name in batch:
            params.append(("scientificnames[]", name))
        
        try:
            response = requests.get(WORMS_MATCH_NAMES_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("Failed to match names batch %s: %s", batch[:3], exc)
            continue
        
        # Process response - it's a list of lists, one per input name
        for idx, name_matches in enumerate(data):
            if idx >= len(batch):
                continue
            
            name = batch[idx]
            if not name_matches:
                continue
            
            # Find first exact match
            for match in name_matches:
                if match.get("match_type") == "exact":
                    aphiaid = match.get("valid_AphiaID") or match.get("AphiaID")
                    if aphiaid:
                        rank = match.get("rank", "").lower() if match.get("rank") else ""
                        name_to_match[name] = {
                            "aphiaid": int(aphiaid),
                            "phylum": match.get("phylum") or "",
                            "class": match.get("class") or "",
                            "rank": rank,
                            "scientificNameID": f"urn:lsid:marinespecies.org:taxname:{aphiaid}"
                        }
                    break
    
    logger.info("Matched %d out of %d names with WoRMS", len(name_to_match), len(scientific_names))
    return name_to_match


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

    id_to_data: Dict[int, Dict] = {}

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
                    rank = record.get("rank", "").lower() if record.get("rank") else ""
                    id_to_data[int(original)] = {
                        "valid_aphiaid": int(valid),
                        "phylum": record.get("phylum") or "",
                        "class": record.get("class") or "",
                        "rank": rank
                    }
            except Exception:
                continue

    if not id_to_data:
        return

    for occ in occurrences:
        aphiaid = occ.get("aphiaid")
        if aphiaid in id_to_data:
            data = id_to_data[aphiaid]
            occ["aphiaid"] = data["valid_aphiaid"]
            if not occ.get("phylum"):
                occ["phylum"] = data["phylum"]
            if not occ.get("class"):
                occ["class"] = data["class"]
            # Add rank from API response
            if "rank" in data:
                occ["rank"] = data["rank"]


def process_uploaded_files(files_data: List[Dict]) -> Dict:
    """
    Process uploaded files, looking for occurrence files and parsing them.
    
    Args:
        files_data: List of dicts with 'filename' and 'content' keys
    
    Returns:
        Dict with processing results
    
    Raises:
        ValueError: If file parsing fails or required columns are missing
        Exception: For other processing errors
    """
    filenames = [f['filename'] for f in files_data]
    occurrence_filename = find_occurrence_file(filenames)
    
    result = {
        'occurrence_file_found': occurrence_filename is not None,
        'occurrence_filename': occurrence_filename,
        'parsed_data': None
    }
    
    if occurrence_filename:
        occurrence_file = next(
            (f for f in files_data if f['filename'] == occurrence_filename),
            None
        )
        
        if occurrence_file:
            parsed, detected_delimiter = parse_separated_file(occurrence_file['content'])
            result['row_count'] = len(parsed)
            result['detected_delimiter'] = detected_delimiter
            
            if not parsed:
                raise ValueError('File is empty or has no data rows')
            
            result['column_count'] = len(parsed[0])
            result['columns'] = list(parsed[0].keys())
            result['parsed_data'] = parsed[:10]
            
            # Validate required columns exist in the file before filtering
            required_columns = ['scientificName', 'decimalLongitude', 'decimalLatitude']
            missing_columns = [col for col in required_columns if col not in parsed[0]]
            if missing_columns:
                raise ValueError(
                    f"Missing required columns: {missing_columns}. "
                    f"Found columns: {list(parsed[0].keys())}"
                )
            
            # Filter for species rank if taxonRank column exists, otherwise include all rows
            has_taxon_rank = 'taxonRank' in parsed[0]
            if has_taxon_rank:
                filtered_parsed = [
                    row for row in parsed 
                    if row.get('taxonRank', '').strip().lower() == 'species'
                ]
            else:
                # If taxonRank column doesn't exist, include all rows (assume they are species-level)
                filtered_parsed = parsed
            result['filtered_row_count'] = len(filtered_parsed)
            
            # Check if scientificNameID is missing - if so, match names with WoRMS
            has_scientific_name_id = 'scientificNameID' in parsed[0]
            name_matches = None
            
            if not has_scientific_name_id:
                # Extract unique scientific names and match them with WoRMS
                unique_names = sorted(set(
                    row.get('scientificName', '').strip() 
                    for row in filtered_parsed 
                    if row.get('scientificName', '').strip()
                ))
                if unique_names:
                    logger.info(f"Matching {len(unique_names)} unique scientific names with WoRMS")
                    name_matches = match_names_with_worms(unique_names)
                    result['name_matching_performed'] = True
                    result['name_matching_count'] = len(name_matches)
                    result['name_matching_total'] = len(unique_names)
                    result['name_matching_message'] = (
                        f"Name matching performed: {len(name_matches)} out of {len(unique_names)} "
                        f"scientific names were matched with WoRMS (exact matches only) due to missing scientificNameID column."
                    )
                else:
                    result['name_matching_performed'] = False
                    result['name_matching_message'] = None
            else:
                result['name_matching_performed'] = False
                result['name_matching_message'] = None
            
            occurrences = extract_species_occurrences(filtered_parsed, name_matches)
            
            # Only normalize AphiaIDs if we didn't do name matching (name matching already provides valid IDs)
            if has_scientific_name_id:
                normalize_aphiaids(occurrences)
            
            # If taxonRank was missing initially, filter by rank from API response before further processing
            if not has_taxon_rank:
                occurrences_before_rank_filter = len(occurrences)
                occurrences = [
                    occ for occ in occurrences 
                    if occ.get('rank', '').lower() == 'species'
                ]
                result['rank_filtered_count'] = len(occurrences)
                result['rank_filtered_removed'] = occurrences_before_rank_filter - len(occurrences)
                logger.info(f"Filtered by rank: {len(occurrences)} species out of {occurrences_before_rank_filter} occurrences")
            
            result['original_occurrence_count'] = len(filtered_parsed)
            result['unique_occurrence_count'] = len(occurrences)
            analyzed_occurrences = analyze_species_occurrences(occurrences)
            result['analyzed_occurrences'] = analyzed_occurrences
            result['analyzed_count'] = len(analyzed_occurrences)
    
    return result

