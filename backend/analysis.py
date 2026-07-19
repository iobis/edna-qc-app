from typing import List, Dict
import logging

from grid_products import (
    densities_for_lookups,
    density_path_ok,
    DENSITY_PATH,
    H3_RESOLUTION,
    latlng_to_h3_int,
    open_h3_connection,
    preload_profiles,
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

    Lookups are batched: one parquet join for all density cells, one profile
    preload, then in-memory suitability evaluation.
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

    # Resolve H3 cells once up front.
    resolved = []
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
            h3_cell = latlng_to_h3_int(float(lat), float(lon))
        except Exception as e:
            logger.error("Failed to compute H3 cell for occurrence: %s", e)
            raise Exception(f"Failed to compute H3 cell: {e}") from e

        resolved.append((occurrence, int(aphiaid), h3_cell))

    if not resolved:
        return occurrences

    logger.info(
        "Scoring %s occurrences (%s unique AphiaIDs)",
        len(resolved),
        len({aphiaid for _, aphiaid, _ in resolved}),
    )

    density_map: Dict = {}
    if density_ok:
        conn = open_h3_connection()
        try:
            density_map = densities_for_lookups(
                conn,
                [(aphiaid, h3_cell) for _, aphiaid, h3_cell in resolved],
            )
        except Exception as e:
            logger.error("Failed batched density lookup: %s", e)
            raise Exception(f"Failed batched density lookup: {e}") from e
        finally:
            conn.close()
        logger.info("Density lookup done: %s hits", len(density_map))

    if thermal_ok:
        try:
            preload_profiles([aphiaid for _, aphiaid, _ in resolved])
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed profile preload: %s", e)
            raise Exception(f"Failed profile preload: {e}") from e
        logger.info("Thermal profiles preloaded")

    missing_density = 0
    missing_profile = 0
    scored = 0
    kde_cache: dict = {}

    for occurrence, aphiaid, h3_cell in resolved:
        if density_ok:
            density = density_map.get((aphiaid, h3_cell))
            if density is None:
                missing_density += 1
            occurrence["density"] = density

        if thermal_ok:
            try:
                suitability = suitability_at_cell(aphiaid, h3_cell, kde_cache=kde_cache)
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
