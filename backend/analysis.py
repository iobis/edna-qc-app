from typing import List, Dict
import logging

from grid_products import (
    density_at_cell,
    density_path_ok,
    DENSITY_PATH,
    H3_RESOLUTION,
    latlng_to_h3,
    open_h3_connection,
    suitability_at_cell,
    thermal_paths_ok,
    THERMAL_PROFILES_PATH,
    THERMAL_THETAO_PATH,
)

# Re-export for callers that still import these names.
SPEEDY_RESOLUTION = H3_RESOLUTION
SPEEDY_DATA_DIR = DENSITY_PATH

logger = logging.getLogger(__name__)


def analyze_species_occurrences(occurrences: List[Dict]) -> List[Dict]:
    """
    Attach density and thermal suitability scores to each occurrence.

    Density comes from build/density_3 (uint16 / 65535). Suitability is
    evaluated on the fly from thermal_3 thetao + species KDE profiles.
    The score is the suitability value.
    """
    if not occurrences:
        return occurrences

    density_ok = density_path_ok()
    thermal_ok = thermal_paths_ok()
    if not density_ok:
        logger.warning("Density product not found: %s", DENSITY_PATH)
    if not thermal_ok:
        logger.warning(
            "Thermal products missing (thetao=%s, profiles=%s)",
            THERMAL_THETAO_PATH,
            THERMAL_PROFILES_PATH,
        )
    if not density_ok and not thermal_ok:
        return occurrences

    missing_density = 0
    missing_profile = 0
    scored = 0
    kde_cache: dict = {}

    conn = open_h3_connection()
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

            try:
                h3_cell = latlng_to_h3(conn, float(lat), float(lon))
            except Exception as e:
                logger.error("Failed to compute H3 cell for occurrence: %s", e)
                raise Exception(f"Failed to compute H3 cell: {e}") from e

            if density_ok:
                try:
                    density = density_at_cell(conn, int(aphiaid), h3_cell)
                except Exception as e:
                    logger.error("Failed density lookup for AphiaID %s: %s", aphiaid, e)
                    raise Exception(f"Failed density lookup for AphiaID {aphiaid}: {e}") from e
                if density is None:
                    missing_density += 1
                occurrence["density"] = density

            if thermal_ok:
                try:
                    suitability = suitability_at_cell(int(aphiaid), h3_cell, kde_cache=kde_cache)
                except FileNotFoundError:
                    raise
                except Exception as e:
                    logger.error("Failed suitability lookup for AphiaID %s: %s", aphiaid, e)
                    raise Exception(
                        f"Failed suitability lookup for AphiaID {aphiaid}: {e}"
                    ) from e
                if suitability is None:
                    missing_profile += 1
                else:
                    occurrence["suitability"] = suitability
                    occurrence["score"] = suitability
                    scored += 1
    finally:
        conn.close()

    logger.info(
        "Grid lookup: %s scored, %s without density cell, %s without thermal profile "
        "(density=%s, thermal=%s)",
        scored,
        missing_density,
        missing_profile,
        DENSITY_PATH,
        THERMAL_PROFILES_PATH,
    )

    return occurrences
