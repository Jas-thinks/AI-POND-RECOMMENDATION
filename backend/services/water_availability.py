"""
Water availability / runoff estimation.

Uses the simplified rational runoff formula:

    Runoff Volume = Rainfall (mm) × Catchment Area (m2) × Runoff Coefficient / 1000

The runoff coefficient is derived from land-cover assumptions.
"""

from __future__ import annotations


def _runoff_coefficient(
    land_cover_type: str | None = None,
    soil_permeability: str | None = None,
) -> float:
    """
    Estimate runoff coefficient (0-1) based on land cover and soil.

    Default: agricultural land with moderate permeability = 0.30.
    """
    defaults = {
        "impervious": 0.90,
        "urban": 0.75,
        "bare rock": 0.70,
        "built-up": 0.60,
        "agricultural": 0.30,
        "grassland": 0.25,
        "forest": 0.20,
        "woodland": 0.20,
        "scrub": 0.18,
        "wetland": 0.10,
        "water body": 1.00,
    }
    base = defaults.get(land_cover_type.lower() if land_cover_type else "", 0.30)

    # Impermeable soils increase runoff slightly
    if soil_permeability in ("very slow (<0.1 mm/h)", "slow (0.1-1.0 mm/h)"):
        base = min(base + 0.10, 0.95)
    elif soil_permeability in ("moderate (1-10 mm/h)",):
        pass
    else:
        base = max(base - 0.05, 0.05)

    return round(base, 2)


def _confidence_level(annual_rainfall: float | None, catchment_area_m2: float | None) -> str:
    if annual_rainfall is None or catchment_area_m2 is None:
        return "low"
    if annual_rainfall < 200 or catchment_area_m2 < 1000:
        return "low"
    if annual_rainfall >= 600 and catchment_area_m2 >= 10000:
        return "high"
    return "medium"


def estimate_water_availability(
    catchment_area_m2: float,
    annual_rainfall_mm: float | None,
    runoff_coefficient: float | None = None,
    land_cover_type: str | None = None,
    soil_permeability: str | None = None,
) -> dict:
    """
    Estimate annual runoff volume from a catchment.

    Parameters
    ----------
    catchment_area_m2 : float
        Upstream contributing area in square metres.
    annual_rainfall_mm : float | None
        Long-term average annual rainfall in millimetres.
        If None, the module returns available=False.
    runoff_coefficient : float | None
        Rational-method runoff coefficient (0-1).
        If None, it is estimated from land cover / soil data.
    land_cover_type, soil_permeability : str | None
        Used to estimate runoff coefficient when not explicitly provided.

    Returns
    -------
    dict
        Structured water-availability estimate with score, confidence,
        and clear caveats.
    """
    assumptions: list[str] = []
    coef = runoff_coefficient
    if coef is None:
        coef = _runoff_coefficient(land_cover_type, soil_permeability)
        assumptions.append(
            f"Runoff coefficient ({coef}) estimated from "
            f"land cover ({land_cover_type or 'unknown'}) and "
            f"soil permeability ({soil_permeability or 'unknown'})."
        )
    else:
        assumptions.append(f"Runoff coefficient ({coef}) provided explicitly.")

    if annual_rainfall_mm is None:
        return {
            "available": False,
            "catchment_area_m2": catchment_area_m2,
            "annual_rainfall_mm": None,
            "runoff_coefficient": coef,
            "estimated_annual_runoff_m3": None,
            "water_availability_score": None,
            "confidence": "low",
            "assumptions": assumptions,
            "error": "Annual rainfall data unavailable.",
        }

    # Runoff volume = rainfall(mm) / 1000 * area(m2) * C
    runoff_m3 = round(annual_rainfall_mm * 0.001 * catchment_area_m2 * coef, 1)
    assumptions.append(
        f"Runoff = {annual_rainfall_mm} mm / 1000 * {catchment_area_m2:.0f} m2 * {coef} "
        f"= {runoff_m3:.1f} m3/year."
    )

    # Score: higher runoff per sq-m of catchment → better
    runoff_per_ha = runoff_m3 / max(catchment_area_m2 * 1e-4, 0.01)
    if runoff_per_ha >= 5000:
        score = 90.0
    elif runoff_per_ha >= 3000:
        score = 78.0
    elif runoff_per_ha >= 1500:
        score = 65.0
    elif runoff_per_ha >= 800:
        score = 50.0
    elif runoff_per_ha >= 400:
        score = 35.0
    else:
        score = 20.0

    confidence = _confidence_level(annual_rainfall_mm, catchment_area_m2)

    return {
        "available": True,
        "catchment_area_m2": round(catchment_area_m2, 2),
        "annual_rainfall_mm": annual_rainfall_mm,
        "runoff_coefficient": coef,
        "estimated_annual_runoff_m3": runoff_m3,
        "water_availability_score": round(score, 1),
        "confidence": confidence,
        "assumptions": assumptions,
        "error": None,
    }
