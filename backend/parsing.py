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
        if pattern.match(filename):
            logger.info("Found occurrence file: %s", filename)
            return filename
    return None


def find_event_file(filenames: List[str]) -> Optional[str]:
    """
    Find a file matching event pattern (case-insensitive).
    Patterns: event.*, Event.*, etc.
    """
    pattern = re.compile(r'.*event.*(txt|csv|tsv)', re.IGNORECASE)
    for filename in filenames:
        if pattern.match(filename):
            logger.info("Found event file: %s", filename)
            return filename
    return None


def find_dna_derived_data_file(filenames: List[str]) -> Optional[str]:
    """
    Find a DNADerivedData extension file (case-insensitive).

    Prefers names like DNADerivedData / dna_derived / dna-derived, then falls
    back to any *dna*.{txt,csv,tsv} table.
    """
    specific = re.compile(
        r'.*(dna[_-]?derived|dnaderiveddata).*(txt|csv|tsv)',
        re.IGNORECASE,
    )
    loose = re.compile(r'.*dna.*(txt|csv|tsv)', re.IGNORECASE)

    for filename in filenames:
        base = filename.rsplit('/', 1)[-1]
        if specific.match(base):
            logger.info("Found DNADerivedData file: %s", filename)
            return filename

    for filename in filenames:
        base = filename.rsplit('/', 1)[-1]
        if loose.match(base):
            logger.info("Found DNADerivedData file: %s", filename)
            return filename

    return None


def _markers_from_flat_fields(row: Dict) -> Dict[str, str]:
    """Build gene -> sequence map from flat DNA_sequence / target_gene fields."""
    sequence = _cell_value(row, 'DNA_sequence')
    target_gene = _cell_value(row, 'target_gene')
    if not sequence and not target_gene:
        return {}
    # Flat target_gene may already be a joined display string; only trust it when
    # it looks like a single gene token (no comma). Prefer dna_markers when present.
    if ',' in target_gene:
        target_gene = ''
        if not sequence:
            return {}
    return {target_gene: sequence}


def _markers_from_row(row: Dict) -> Dict[str, str]:
    """Prefer structured dna_markers; fall back to flat DNA fields."""
    listed = _markers_from_list(row.get('dna_markers'))
    if listed:
        return listed
    return _markers_from_flat_fields(row)


def _markers_from_list(markers: Optional[List]) -> Dict[str, str]:
    """Build gene -> sequence map from a dna_markers list (first sequence wins)."""
    result: Dict[str, str] = {}
    if not markers:
        return result
    for item in markers:
        if not isinstance(item, dict):
            continue
        gene = _cell_value(item, 'target_gene')
        sequence = _cell_value(item, 'DNA_sequence')
        if not gene and not sequence:
            continue
        if gene not in result:
            result[gene] = sequence
        elif sequence and not result[gene]:
            result[gene] = sequence
    return result


def _merge_marker_maps(*maps: Dict[str, str]) -> Dict[str, str]:
    """Merge gene -> sequence maps; first non-empty sequence per gene wins."""
    merged: Dict[str, str] = {}
    for marker_map in maps:
        for gene, sequence in marker_map.items():
            if gene not in merged:
                merged[gene] = sequence
            elif sequence and not merged[gene]:
                merged[gene] = sequence
    return merged


def _markers_to_list(markers: Dict[str, str]) -> List[Dict[str, str]]:
    """Stable list form for API/UI: one entry per gene (including unnamed)."""
    items = sorted(
        markers.items(),
        key=lambda item: (item[0] == '', item[0].lower()),
    )
    return [
        {'target_gene': gene, 'DNA_sequence': sequence}
        for gene, sequence in items
    ]


def _apply_markers_to_row(row: Dict, markers: Dict[str, str]) -> None:
    """Write dna_markers plus convenience flat fields onto an occurrence dict."""
    if not markers:
        row.pop('dna_markers', None)
        return

    row['dna_markers'] = _markers_to_list(markers)
    genes = [marker['target_gene'] for marker in row['dna_markers'] if marker['target_gene']]
    if genes:
        row['target_gene'] = ', '.join(genes)
    else:
        row.pop('target_gene', None)
    sequences = [marker['DNA_sequence'] for marker in row['dna_markers'] if marker['DNA_sequence']]
    if sequences:
        row['DNA_sequence'] = sequences[0]
    else:
        row.pop('DNA_sequence', None)


def build_dna_sequence_index(dna_rows: List[Dict]) -> Dict[str, Dict[str, str]]:
    """
    Build DNA marker index by occurrenceID.

    Each occurrenceID maps to {target_gene: DNA_sequence}, keeping the first
    non-empty example sequence per gene. Rows without a gene use '' as the key.
    """
    by_occurrence_id: Dict[str, Dict[str, str]] = {}

    for row in dna_rows:
        occurrence_id = _cell_value(row, 'occurrenceID')
        if not occurrence_id:
            continue

        sequence = _cell_value(row, 'DNA_sequence')
        target_gene = _cell_value(row, 'target_gene')
        if not sequence and not target_gene:
            continue

        markers = by_occurrence_id.setdefault(occurrence_id, {})
        if target_gene not in markers:
            markers[target_gene] = sequence
        elif sequence and not markers[target_gene]:
            markers[target_gene] = sequence

    return by_occurrence_id


def enrich_occurrences_with_dna_sequences(
    occurrence_rows: List[Dict],
    dna_rows: List[Dict],
) -> Tuple[List[Dict], Dict]:
    """
    Attach DNA markers (target_gene + example DNA_sequence) joined on occurrenceID.

    Multiple DNADerivedData rows for the same occurrence are collected: one example
    sequence per distinct target_gene. Existing values on the occurrence row are kept.
    """
    by_occurrence_id = build_dna_sequence_index(dna_rows)
    enriched: List[Dict] = []
    joined_count = 0
    target_gene_joined_count = 0

    for row in occurrence_rows:
        new_row = dict(row)
        occurrence_id = _cell_value(new_row, 'occurrenceID')
        dna_markers = by_occurrence_id.get(occurrence_id, {}) if occurrence_id else {}

        markers = _merge_marker_maps(
            _markers_from_row(new_row),
            dna_markers,
        )
        _apply_markers_to_row(new_row, markers)

        if any(seq for seq in markers.values()):
            joined_count += 1
        if any(gene for gene in markers):
            target_gene_joined_count += 1

        enriched.append(new_row)

    stats = {
        'dna_sequence_joined_count': joined_count,
        'target_gene_joined_count': target_gene_joined_count,
        'dna_row_count': len(dna_rows),
        'dna_occurrence_id_count': len(by_occurrence_id),
    }
    return enriched, stats


def _cell_value(row: Dict, key: str) -> str:
    value = row.get(key, '')
    if value is None:
        return ''
    return str(value).strip()


def build_event_index(event_rows: List[Dict]) -> Dict[str, Dict]:
    """Index event rows by eventID. Later duplicates overwrite earlier ones."""
    events_by_id: Dict[str, Dict] = {}
    for row in event_rows:
        event_id = _cell_value(row, 'eventID')
        if event_id:
            events_by_id[event_id] = row
    return events_by_id


def resolve_event_coordinates(
    event_id: str,
    events_by_id: Dict[str, Dict],
) -> Tuple[str, str]:
    """
    Walk event → parentEventID chain and return the nearest lat/lon values.

    Coordinates on a lower-level event win over parents. Lat and lon are
    resolved independently. Returns empty strings when unresolved.
    """
    lat = ''
    lon = ''
    visited = set()
    current_id = event_id

    while current_id and current_id not in visited:
        visited.add(current_id)
        event = events_by_id.get(current_id)
        if event is None:
            break

        if not lat:
            lat = _cell_value(event, 'decimalLatitude')
        if not lon:
            lon = _cell_value(event, 'decimalLongitude')

        if lat and lon:
            break

        current_id = _cell_value(event, 'parentEventID')

    return lat, lon


def enrich_occurrences_with_event_coords(
    occurrence_rows: List[Dict],
    event_rows: List[Dict],
) -> Tuple[List[Dict], Dict]:
    """
    Join occurrences to events on eventID and inherit coordinates from the
    event hierarchy when the occurrence (or child event) lacks values.

    Inheritance order (child wins): occurrence → event → parent events.
    """
    events_by_id = build_event_index(event_rows)
    enriched: List[Dict] = []
    inherited_count = 0
    unresolved_count = 0

    for row in occurrence_rows:
        new_row = dict(row)
        lat = _cell_value(new_row, 'decimalLatitude')
        lon = _cell_value(new_row, 'decimalLongitude')
        event_id = _cell_value(new_row, 'eventID')

        inherited_lat = False
        inherited_lon = False

        if event_id and (not lat or not lon):
            event_lat, event_lon = resolve_event_coordinates(event_id, events_by_id)
            if not lat and event_lat:
                new_row['decimalLatitude'] = event_lat
                lat = event_lat
                inherited_lat = True
            if not lon and event_lon:
                new_row['decimalLongitude'] = event_lon
                lon = event_lon
                inherited_lon = True

        if inherited_lat or inherited_lon:
            inherited_count += 1

        if not lat or not lon:
            unresolved_count += 1

        enriched.append(new_row)

    stats = {
        'coords_inherited_count': inherited_count,
        'coords_unresolved_count': unresolved_count,
        'event_count': len(events_by_id),
    }
    return enriched, stats


def detect_delimiter(content: bytes, sample_size: int = 8192) -> str:
    """
    Detect the delimiter (comma or tab) in the file content.

    Tries csv.Sniffer first, then falls back to counting tabs vs commas in the
    sample. DwC-A TSV files often confuse Sniffer on short samples.
    """
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        try:
            text = content.decode('latin-1')
        except UnicodeDecodeError:
            text = content.decode('utf-8', errors='ignore')

    sample = text[:sample_size] if len(text) > sample_size else text
    if not sample.strip():
        raise ValueError("Could not detect delimiter (comma or tab) in file: empty content")

    sniffer = csv.Sniffer()
    try:
        return sniffer.sniff(sample, delimiters='\t,').delimiter
    except csv.Error:
        pass

    # Fallback: prefer the delimiter that appears more often on the header /
    # first data lines (Sniffer is unreliable on some DwC-A TSVs).
    lines = [line for line in sample.splitlines() if line.strip()][:20]
    probe = '\n'.join(lines) if lines else sample
    tab_count = probe.count('\t')
    comma_count = probe.count(',')
    if tab_count == 0 and comma_count == 0:
        raise ValueError(
            "Could not detect delimiter (comma or tab) in file: Could not determine delimiter"
        )
    return '\t' if tab_count >= comma_count else ','


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
    Requires scientificName. Coordinates (decimalLongitude / decimalLatitude) are
    optional at the column level but rows without resolvable coords get None.
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
    if 'scientificName' not in first_row:
        logging.error("Missing required column: scientificName. Found columns: %s", list(first_row.keys()))
        raise ValueError(
            f"Missing required column: scientificName. "
            f"Found columns: {list(first_row.keys())}"
        )
    
    unique_occurrences: Dict[Tuple[str, Optional[str], Optional[float], Optional[float]], Dict] = {}
    
    for row in parsed_data:
        scientific_name = row.get('scientificName', '').strip()
        scientific_name_id = row.get('scientificNameID', '').strip()
        phylum = row.get('phylum', '').strip()
        class_name = row.get('class', '').strip()
        lon_str = row.get('decimalLongitude', '').strip() if row.get('decimalLongitude') is not None else ''
        lat_str = row.get('decimalLatitude', '').strip() if row.get('decimalLatitude') is not None else ''
        
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
        row_markers = _markers_from_row(row)
        
        if key not in unique_occurrences:
            footprint_wkt = None
            if lon is not None and lat is not None:
                min_lon = round(lon - 0.05, 1)
                max_lon = round(lon + 0.05, 1)
                min_lat = round(lat - 0.05, 1)
                max_lat = round(lat + 0.05, 1)
                footprint_wkt = f"POLYGON(({min_lon} {min_lat}, {max_lon} {min_lat}, {max_lon} {max_lat}, {min_lon} {max_lat}, {min_lon} {min_lat}))"
            
            unique_occurrences[key] = {
                'scientificName': scientific_name,
                'scientificNameID': scientific_name_id,
                'phylum': phylum,
                'class': class_name,
                'decimalLongitude': lon,
                'decimalLatitude': lat,
                'aphiaid': aphiaid,
                'footprintWKT': footprint_wkt,
            }
            if rank:
                unique_occurrences[key]['rank'] = rank
            _apply_markers_to_row(unique_occurrences[key], row_markers)
        else:
            # Keep one row per species/location; collect one example sequence per gene
            existing = unique_occurrences[key]
            merged = _merge_marker_maps(_markers_from_row(existing), row_markers)
            _apply_markers_to_row(existing, merged)
    
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

    When an event table is present and both tables have eventID, occurrences
    are joined to events and coordinates are inherited from the event hierarchy
    (occurrence → event → parent events) when lower levels lack values.

    When a DNADerivedData table is present, at most one DNA_sequence is joined
    to each occurrence via occurrenceID and carried through to each unique
    species-level detection.
    
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
    event_filename = find_event_file(filenames)
    dna_filename = find_dna_derived_data_file(filenames)
    
    result = {
        'occurrence_file_found': occurrence_filename is not None,
        'occurrence_filename': occurrence_filename,
        'event_file_found': event_filename is not None,
        'event_filename': event_filename,
        'event_core_joined': False,
        'dna_file_found': dna_filename is not None,
        'dna_filename': dna_filename,
        'dna_joined': False,
        'parsed_data': None
    }
    
    if not occurrence_filename:
        return result

    occurrence_file = next(
        (f for f in files_data if f['filename'] == occurrence_filename),
        None
    )
    if not occurrence_file:
        return result

    parsed, detected_delimiter = parse_separated_file(occurrence_file['content'])
    result['row_count'] = len(parsed)
    result['detected_delimiter'] = detected_delimiter

    if not parsed:
        raise ValueError('File is empty or has no data rows')

    result['column_count'] = len(parsed[0])
    result['columns'] = list(parsed[0].keys())
    result['parsed_data'] = parsed[:10]

    if 'scientificName' not in parsed[0]:
        raise ValueError(
            f"Missing required column: scientificName. "
            f"Found columns: {list(parsed[0].keys())}"
        )

    # Event Core: join occurrence → event → parent events for coordinates
    occ_has_event_id = 'eventID' in parsed[0]
    if event_filename and occ_has_event_id:
        event_file = next(
            (f for f in files_data if f['filename'] == event_filename),
            None
        )
        if event_file:
            event_rows, _ = parse_separated_file(event_file['content'])
            if event_rows and 'eventID' in event_rows[0]:
                parsed, event_stats = enrich_occurrences_with_event_coords(parsed, event_rows)
                result['event_core_joined'] = True
                result['event_row_count'] = len(event_rows)
                result['event_columns'] = list(event_rows[0].keys())
                result['columns'] = list(parsed[0].keys())
                result['parsed_data'] = parsed[:10]
                result.update(event_stats)
                logger.info(
                    "Event Core join: %d events, inherited coords for %d rows, "
                    "%d still unresolved",
                    event_stats['event_count'],
                    event_stats['coords_inherited_count'],
                    event_stats['coords_unresolved_count'],
                )
            else:
                logger.warning(
                    "Event file '%s' found but missing eventID column; skipping join",
                    event_filename,
                )
    elif event_filename and not occ_has_event_id:
        logger.info(
            "Event file '%s' found but occurrence table has no eventID; skipping join",
            event_filename,
        )

    # DNADerivedData: attach DNA_sequence / target_gene per occurrence row
    if dna_filename:
        dna_file = next(
            (f for f in files_data if f['filename'] == dna_filename),
            None
        )
        if dna_file:
            dna_rows, _ = parse_separated_file(dna_file['content'])
            dna_headers = dna_rows[0].keys() if dna_rows else []
            has_dna_fields = (
                'DNA_sequence' in dna_headers or 'target_gene' in dna_headers
            )
            if dna_rows and has_dna_fields:
                if 'occurrenceID' in dna_headers:
                    parsed, dna_stats = enrich_occurrences_with_dna_sequences(
                        parsed, dna_rows
                    )
                    result['dna_joined'] = True
                    result['dna_columns'] = list(dna_headers)
                    result['columns'] = list(parsed[0].keys())
                    result['parsed_data'] = parsed[:10]
                    result.update(dna_stats)
                    logger.info(
                        "DNADerivedData join: %d DNA rows, sequences on %d occurrences, "
                        "target_gene on %d occurrences",
                        dna_stats['dna_row_count'],
                        dna_stats['dna_sequence_joined_count'],
                        dna_stats['target_gene_joined_count'],
                    )
                else:
                    logger.warning(
                        "DNA file '%s' found but missing occurrenceID; skipping join",
                        dna_filename,
                    )
            else:
                logger.warning(
                    "DNA file '%s' found but missing DNA_sequence and target_gene; skipping join",
                    dna_filename,
                )

    has_lat = any(_cell_value(row, 'decimalLatitude') for row in parsed)
    has_lon = any(_cell_value(row, 'decimalLongitude') for row in parsed)
    if not has_lat or not has_lon:
        raise ValueError(
            "No coordinates found. Provide decimalLatitude and decimalLongitude "
            "on the occurrence table, or use Event Core with eventID so coordinates "
            "can be inherited from the event hierarchy."
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

