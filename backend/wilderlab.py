"""
Wilderlab sample-batch XLSX ingest.

Converts Wilderlab job exports (metadata / aggregated / full sheets) into
DwC-shaped occurrence rows, then reuses shared WoRMS matching and analysis
helpers. Darwin Core parsing stays in parsing.py; this module is Wilderlab-only.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from python_calamine import CalamineWorkbook

from analysis import analyze_species_occurrences
from parsing import extract_species_occurrences, match_names_with_worms

logger = logging.getLogger(__name__)

WILDERLAB_REQUIRED_SHEETS = frozenset({"metadata", "aggregated"})


def _wilderlab_workbook_from_bytes(content: bytes) -> CalamineWorkbook:
    return CalamineWorkbook.from_filelike(io.BytesIO(content))


def _wilderlab_cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _wilderlab_count(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def is_wilderlab_xlsx(filename: str, content: bytes) -> bool:
    """Return True if content is a Wilderlab sample-batch workbook."""
    if os.path.splitext(filename)[1].lower() != ".xlsx":
        return False
    try:
        workbook = _wilderlab_workbook_from_bytes(content)
    except Exception:
        logger.info("File '%s' is not a readable XLSX for Wilderlab detection", filename)
        return False
    sheet_names = {name.strip().lower() for name in workbook.sheet_names}
    return WILDERLAB_REQUIRED_SHEETS.issubset(sheet_names)


def find_wilderlab_xlsx_files(files_data: List[Dict]) -> List[Dict]:
    """Return uploaded files that are Wilderlab XLSX exports."""
    found = []
    for file_info in files_data:
        filename = file_info.get("filename", "")
        content = file_info.get("content", b"")
        if is_wilderlab_xlsx(filename, content):
            found.append(file_info)
    return found


def parse_wilderlab_workbook(content: bytes) -> Dict[str, List[List[Any]]]:
    """
    Read Wilderlab sheets into raw row matrices.

    Returns dict keyed by lowercased sheet name: metadata, aggregated, full (optional).
    """
    workbook = _wilderlab_workbook_from_bytes(content)
    sheets: Dict[str, List[List[Any]]] = {}
    for name in workbook.sheet_names:
        key = name.strip().lower()
        sheets[key] = workbook.get_sheet_by_name(name).to_python()
    missing = WILDERLAB_REQUIRED_SHEETS - set(sheets)
    if missing:
        raise ValueError(
            f"Wilderlab XLSX is missing required sheet(s): {', '.join(sorted(missing))}"
        )
    return sheets


def _parse_wilderlab_job_id(metadata_rows: List[List[Any]]) -> Optional[str]:
    for row in metadata_rows:
        if not row:
            continue
        key = _wilderlab_cell_str(row[0]).rstrip(":")
        if key.lower() == "jobid" and len(row) > 1:
            job_id = _wilderlab_cell_str(row[1])
            return job_id or None
    return None


def parse_wilderlab_metadata_samples(
    metadata_rows: List[List[Any]],
) -> Dict[str, Dict[str, str]]:
    """
    Parse the Wilderlab metadata sample table into UID -> sample fields.

    Coordinates are stored as Latitude / Longitude strings for later DwC mapping.
    """
    header_idx = None
    for idx, row in enumerate(metadata_rows):
        if row and _wilderlab_cell_str(row[0]) == "UID":
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError("Wilderlab metadata sheet has no UID sample table")

    header = [_wilderlab_cell_str(cell) for cell in metadata_rows[header_idx]]
    samples: Dict[str, Dict[str, str]] = {}
    for row in metadata_rows[header_idx + 1 :]:
        if not row or all(_wilderlab_cell_str(cell) == "" for cell in row):
            continue
        values = [_wilderlab_cell_str(cell) for cell in row]
        # Pad short rows
        if len(values) < len(header):
            values.extend([""] * (len(header) - len(values)))
        record = {header[i]: values[i] for i in range(len(header))}
        uid = record.get("UID", "")
        if not uid:
            continue
        samples[uid] = record
    if not samples:
        raise ValueError("Wilderlab metadata sample table has no sample rows")
    return samples


def _wilderlab_sample_columns(
    header: List[str], sample_uids: Dict[str, Dict[str, str]]
) -> List[str]:
    return [col for col in header if col in sample_uids]


def _build_wilderlab_sequence_index(
    full_rows: List[List[Any]], sample_uids: Dict[str, Dict[str, str]]
) -> Dict[Tuple[str, str], str]:
    """Map (scientificName, sample_uid) -> first non-empty Sequence with count > 0."""
    if not full_rows:
        return {}
    header = [_wilderlab_cell_str(cell) for cell in full_rows[0]]
    try:
        name_idx = header.index("ScientificName")
        seq_idx = header.index("Sequence")
    except ValueError:
        logger.warning("Wilderlab full sheet missing ScientificName or Sequence; skipping DNA join")
        return {}

    sample_cols = _wilderlab_sample_columns(header, sample_uids)
    index: Dict[Tuple[str, str], str] = {}
    for row in full_rows[1:]:
        if not row:
            continue
        name = _wilderlab_cell_str(row[name_idx] if name_idx < len(row) else "")
        sequence = _wilderlab_cell_str(row[seq_idx] if seq_idx < len(row) else "")
        if not name or not sequence:
            continue
        for col_name in sample_cols:
            col_idx = header.index(col_name)
            count = _wilderlab_count(row[col_idx] if col_idx < len(row) else None)
            if count <= 0:
                continue
            key = (name, col_name)
            if key not in index:
                index[key] = sequence
    return index


def wilderlab_aggregated_to_occurrence_rows(
    aggregated_rows: List[List[Any]],
    samples: Dict[str, Dict[str, str]],
    sequence_index: Optional[Dict[Tuple[str, str], str]] = None,
) -> List[Dict[str, str]]:
    """
    Unpivot Wilderlab aggregated counts into long DwC-shaped occurrence rows.

    One row per (taxon, sample) with count > 0 and resolvable metadata coordinates.
    NCBI TaxID is intentionally not mapped to scientificNameID.
    """
    if not aggregated_rows:
        raise ValueError("Wilderlab aggregated sheet is empty")

    header = [_wilderlab_cell_str(cell) for cell in aggregated_rows[0]]
    try:
        name_idx = header.index("ScientificName")
    except ValueError as exc:
        raise ValueError("Wilderlab aggregated sheet missing ScientificName column") from exc

    rank_idx = header.index("Rank") if "Rank" in header else None
    phylum_idx = header.index("Phylum") if "Phylum" in header else None
    class_idx = header.index("Class") if "Class" in header else None
    sample_cols = _wilderlab_sample_columns(header, samples)
    if not sample_cols:
        raise ValueError(
            "Wilderlab aggregated sheet has no sample UID columns matching metadata"
        )

    sequence_index = sequence_index or {}
    occurrences: List[Dict[str, str]] = []
    skipped_no_coords = 0

    for row in aggregated_rows[1:]:
        if not row:
            continue
        scientific_name = _wilderlab_cell_str(row[name_idx] if name_idx < len(row) else "")
        if not scientific_name:
            continue
        taxon_rank = (
            _wilderlab_cell_str(row[rank_idx] if rank_idx is not None and rank_idx < len(row) else "")
            if rank_idx is not None
            else ""
        )
        phylum = (
            _wilderlab_cell_str(row[phylum_idx] if phylum_idx is not None and phylum_idx < len(row) else "")
            if phylum_idx is not None
            else ""
        )
        class_name = (
            _wilderlab_cell_str(row[class_idx] if class_idx is not None and class_idx < len(row) else "")
            if class_idx is not None
            else ""
        )

        for uid in sample_cols:
            col_idx = header.index(uid)
            count = _wilderlab_count(row[col_idx] if col_idx < len(row) else None)
            if count <= 0:
                continue
            sample = samples.get(uid, {})
            lat = sample.get("Latitude", "")
            lon = sample.get("Longitude", "")
            if not lat or not lon:
                skipped_no_coords += 1
                continue
            occ: Dict[str, str] = {
                "scientificName": scientific_name,
                "taxonRank": taxon_rank,
                "decimalLatitude": lat,
                "decimalLongitude": lon,
                "phylum": phylum,
                "class": class_name,
                "occurrenceID": uid,
            }
            sequence = sequence_index.get((scientific_name, uid))
            if sequence:
                occ["DNA_sequence"] = sequence
            occurrences.append(occ)

    if skipped_no_coords:
        logger.info(
            "Wilderlab: skipped %d positive-count cells without metadata coordinates",
            skipped_no_coords,
        )
    return occurrences


def process_wilderlab_files(files_data: List[Dict]) -> Dict:
    """
    Process a Wilderlab XLSX upload into the same analysis result shape as DwC ingest.
    """
    wilderlab_files = find_wilderlab_xlsx_files(files_data)
    if not wilderlab_files:
        raise ValueError("No Wilderlab XLSX export found in upload")
    if len(wilderlab_files) > 1:
        raise ValueError("Upload a single Wilderlab XLSX file per job")

    # Reject mixes with Darwin Core text tables
    non_wilderlab = [
        f["filename"]
        for f in files_data
        if f not in wilderlab_files
    ]
    if non_wilderlab:
        raise ValueError(
            "Cannot mix Wilderlab XLSX exports with Darwin Core text files in one job. "
            f"Extra files: {', '.join(non_wilderlab)}"
        )

    wilderlab_file = wilderlab_files[0]
    filename = wilderlab_file["filename"]
    logger.info("Processing Wilderlab XLSX: %s", filename)

    sheets = parse_wilderlab_workbook(wilderlab_file["content"])
    metadata_rows = sheets["metadata"]
    aggregated_rows = sheets["aggregated"]
    full_rows = sheets.get("full") or []

    job_id = _parse_wilderlab_job_id(metadata_rows)
    samples = parse_wilderlab_metadata_samples(metadata_rows)
    sequence_index = _build_wilderlab_sequence_index(full_rows, samples)
    parsed = wilderlab_aggregated_to_occurrence_rows(
        aggregated_rows, samples, sequence_index=sequence_index
    )

    result: Dict = {
        "source_format": "wilderlab",
        "wilderlab_file_found": True,
        "wilderlab_filename": filename,
        "wilderlab_job_id": job_id,
        "wilderlab_sample_count": len(samples),
        "wilderlab_aggregated_row_count": max(len(aggregated_rows) - 1, 0),
        "wilderlab_full_row_count": max(len(full_rows) - 1, 0) if full_rows else 0,
        "wilderlab_dna_joined": bool(sequence_index),
        "occurrence_file_found": True,
        "occurrence_filename": filename,
        "event_file_found": False,
        "event_filename": None,
        "event_core_joined": False,
        "dna_file_found": bool(full_rows),
        "dna_filename": f"{filename}#full" if full_rows else None,
        "dna_joined": bool(sequence_index),
        "row_count": len(parsed),
        "column_count": len(parsed[0]) if parsed else 0,
        "columns": list(parsed[0].keys()) if parsed else [],
        "parsed_data": parsed[:10],
    }

    if not parsed:
        raise ValueError(
            "Wilderlab export produced no occurrence rows. "
            "Need positive read counts joined to metadata Latitude/Longitude."
        )

    has_lat = any(row.get("decimalLatitude") for row in parsed)
    has_lon = any(row.get("decimalLongitude") for row in parsed)
    if not has_lat or not has_lon:
        raise ValueError(
            "Wilderlab export has no coordinates after joining metadata samples"
        )

    has_taxon_rank = "taxonRank" in parsed[0]
    if has_taxon_rank:
        filtered_parsed = [
            row for row in parsed
            if row.get("taxonRank", "").strip().lower() == "species"
        ]
    else:
        filtered_parsed = parsed
    result["filtered_row_count"] = len(filtered_parsed)

    # No scientificNameID from Wilderlab (TaxID is NCBI); always WoRMS name-match
    unique_names = sorted(
        set(
            row.get("scientificName", "").strip()
            for row in filtered_parsed
            if row.get("scientificName", "").strip()
        )
    )
    name_matches = None
    if unique_names:
        logger.info(
            "Wilderlab: matching %d unique scientific names with WoRMS",
            len(unique_names),
        )
        name_matches = match_names_with_worms(unique_names)
        result["name_matching_performed"] = True
        result["name_matching_count"] = len(name_matches)
        result["name_matching_total"] = len(unique_names)
        result["name_matching_message"] = (
            f"Name matching performed: {len(name_matches)} out of {len(unique_names)} "
            f"scientific names were matched with WoRMS (exact matches only). "
            f"Wilderlab NCBI TaxID is not used as scientificNameID."
        )
    else:
        result["name_matching_performed"] = False
        result["name_matching_message"] = None

    occurrences = extract_species_occurrences(filtered_parsed, name_matches)

    if not has_taxon_rank:
        before = len(occurrences)
        occurrences = [
            occ for occ in occurrences
            if occ.get("rank", "").lower() == "species"
        ]
        result["rank_filtered_count"] = len(occurrences)
        result["rank_filtered_removed"] = before - len(occurrences)

    result["original_occurrence_count"] = len(filtered_parsed)
    result["unique_occurrence_count"] = len(occurrences)
    analyzed_occurrences = analyze_species_occurrences(occurrences)
    result["analyzed_occurrences"] = analyzed_occurrences
    result["analyzed_count"] = len(analyzed_occurrences)
    return result
