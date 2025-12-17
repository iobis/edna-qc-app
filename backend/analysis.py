from typing import List, Dict
import os
import logging

import duckdb


SPEEDY_DATA_DIR = os.environ.get("SPEEDY_DATA_DIR", "/data/parquet")
SPEEDY_RESOLUTION = 3

logger = logging.getLogger(__name__)


def analyze_species_occurrences(occurrences: List[Dict]) -> List[Dict]:
    """
    Analyze species occurrences and add numeric scores.

    For each occurrence, this function looks for a Parquet file in
    the SPEEDY data directory with the name "{aphiaid}.parquet" and
    runs the query:

        INSTALL h3 FROM community;
        LOAD h3;
        SELECT density, suitability
        FROM read_parquet('{file_path}')
        WHERE h3 = h3_latlng_to_cell_string(lat, lon, SPEEDY_RESOLUTION)

    The score is currently taken to be the suitability value.
    Both density and suitability are attached to the occurrence.
    """
    if not occurrences:
        return occurrences

    conn = duckdb.connect(database=":memory:")
    conn.execute("INSTALL h3 FROM community;")
    conn.execute("LOAD h3;")

    try:
        for occurrence in occurrences:
            aphiaid = occurrence.get("aphiaid")
            lon = occurrence.get("decimalLongitude")
            lat = occurrence.get("decimalLatitude")

            occurrence["density"] = None
            occurrence["suitability"] = None
            occurrence["score"] = None

            if not aphiaid or lon is None or lat is None:
                continue

            file_path = os.path.join(SPEEDY_DATA_DIR, f"{aphiaid}.parquet")
            if not os.path.exists(file_path):
                logger.info(f"Speedy parquet file not found for aphiaid={aphiaid}: {file_path}")
                continue

            try:
                result = conn.execute(
                    """
                    SELECT density, suitability
                    FROM read_parquet(?)
                    WHERE h3 = h3_latlng_to_cell_string(?, ?, ?)
                    """,
                    [file_path, float(lat), float(lon), SPEEDY_RESOLUTION],
                ).fetchone()
            except Exception:
                continue

            if result is None:
                continue

            density, suitability = result
            occurrence["density"] = density
            occurrence["suitability"] = suitability
            occurrence["score"] = suitability
    finally:
        conn.close()

    return occurrences

