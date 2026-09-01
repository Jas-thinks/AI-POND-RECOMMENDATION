"""
Data quality and confidence assessment.

Produces an overall confidence level (HIGH / MEDIUM / LOW) and lists
which data sources were available vs. unavailable for each analysis.
"""

from __future__ import annotations


def assess_confidence(
    dem_available: bool = True,
    hydrology_available: bool = True,
    rainfall_available: bool | None = None,
    soil_available: bool | None = None,
    land_use_available: bool | None = None,
    osm_available: bool | None = None,
    storage_available: bool = True,
) -> dict:
    """
    Assess overall data confidence for a pond analysis.

    Parameters
    ----------
    dem_available, hydrology_available : bool
        Always True for a successful analysis run.
    rainfall_available, soil_available, land_use_available, osm_available : bool | None
        None = not attempted, True = available, False = attempted but failed.
    storage_available : bool
        True if terrain-based storage could be estimated.

    Returns
    -------
    dict
        Confidence assessment with sources available, missing, and limitations.
    """
    sources_available: list[str] = []
    sources_missing: list[str] = []
    limitations: list[str] = []

    sources_available.append("DEM (contour-derived)")
    if dem_available:
        limitations.append(
            "DEM is derived from contour interpolation. "
            "Horizontal accuracy depends on contour density and interval."
        )

    sources_available.append("Hydrology (D8 flow routing)")
    if hydrology_available:
        limitations.append(
            "Hydrological flow routing assumes D8 single-flow direction. "
            "Real drainage patterns may differ."
        )

    if osm_available is True:
        sources_available.append("OpenStreetMap (infrastructure exclusions)")
        limitations.append(
            "OSM infrastructure data may be incomplete in rural areas."
        )
    elif osm_available is False:
        sources_missing.append("OpenStreetMap (infrastructure)")
        limitations.append(
            "OpenStreetMap data was unavailable. "
            "Exclusion zones may be incomplete."
        )
    else:
        sources_missing.append("OpenStreetMap (infrastructure)")

    if rainfall_available is True:
        sources_available.append("Historical rainfall (Open-Meteo)")
        limitations.append(
            "Rainfall is estimated from archive data. "
            "Local orographic effects may cause variation."
        )
    elif rainfall_available is False:
        sources_missing.append("Historical rainfall")
        limitations.append(
            "Rainfall data was unavailable. "
            "Use local meteorological records for accurate water-budget estimates."
        )
    else:
        sources_missing.append("Historical rainfall")

    if soil_available is True:
        sources_available.append("Soil data (ISRIC SoilGrids, ~250 m)")
        limitations.append(
            "SoilGrids is at ~250 m resolution and may not represent "
            "site-level geotechnical conditions."
        )
    elif soil_available is False:
        sources_missing.append("Soil data")
        limitations.append(
            "Soil data was unavailable. "
            "Site-specific geotechnical investigation is strongly recommended."
        )
    else:
        sources_missing.append("Soil data")

    if land_use_available is True:
        sources_available.append("Land use / land cover (OpenStreetMap)")
        limitations.append(
            "Land-use classification is based on OSM tags. "
            "Accurate classification requires satellite imagery."
        )
    elif land_use_available is False:
        sources_missing.append("Land use / land cover")
        limitations.append(
            "Land-use data was unavailable."
        )
    else:
        sources_missing.append("Land use / land cover")

    if storage_available:
        limitations.append(
            "Storage estimate is terrain-based preliminary planning value. "
            "Final storage requires bathymetric survey and engineering design."
        )
    else:
        limitations.append(
            "Storage volume could not be estimated from available terrain data."
        )

    limitations.append(
        "This system provides planning-level suitability assessment only. "
        "Final pond approval requires civil-engineering design, "
        "geotechnical investigation, and statutory permissions."
    )

    # Compute overall confidence
    mandatory = [dem_available, hydrology_available]
    optional_available = sum(
        1 for v in [rainfall_available, soil_available, land_use_available, osm_available]
        if v is True
    )
    optional_total = sum(1 for v in [rainfall_available, soil_available, land_use_available, osm_available] if v is not None)
    failed = sum(
        1 for v in [rainfall_available, soil_available, land_use_available, osm_available]
        if v is False
    )

    # All mandatory sources must be present
    if not all(mandatory):
        overall = "LOW"
    elif failed >= 3:
        overall = "LOW"
    elif optional_total == 0:
        overall = "MEDIUM"
    elif failed >= 2:
        overall = "LOW"
    elif failed == 1:
        overall = "MEDIUM"
    elif optional_available >= 3:
        overall = "HIGH"
    else:
        overall = "MEDIUM"

    return {
        "overall_confidence": overall,
        "data_sources_available": sources_available,
        "data_sources_missing": sources_missing,
        "limitations": limitations,
    }
