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
    
    Raises:
        Exception: If database connection or query execution fails
    """
    if not occurrences:
        return occurrences

    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute("INSTALL h3 FROM community;")
        conn.execute("LOAD h3;")
    except Exception as e:
        conn.close()
        raise Exception(f"Failed to install or load h3 extension: {e}")

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
            except Exception as e:
                logger.error(f"Failed to query parquet file {file_path} for occurrence: {e}")
                raise Exception(f"Failed to query parquet file {file_path}: {e}")

            if result is None:
                continue

            density, suitability = result
            occurrence["density"] = density
            occurrence["suitability"] = suitability
            occurrence["score"] = suitability
    finally:
        conn.close()

    return occurrences

