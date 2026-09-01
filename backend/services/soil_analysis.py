"""
Soil analysis service.

Data source: ISRIC SoilGrids API (https://rest.isric.org/soilgrids/2.0/)
            - free, global, ~250 m resolution.

Returns:
    - Sand / silt / clay percentages at selected depth
    - Estimated permeability class
    - Water-retention suitability
    - Suitability score (0-100)
"""

from __future__ import annotations

import httpx


SOILGRIDS_URL = "https://rest.isric.org/soilgrids/2.0/properties/query"

PROPERTIES = ["clay", "sand", "silt"]


K_SAT_TABLE = {
    "clay": (0.01, 0.1),
    "silty clay": (0.01, 0.1),
    "sandy clay": (0.1, 1.0),
    "clay loam": (0.1, 0.5),
    "silty clay loam": (0.05, 0.3),
    "sandy clay loam": (0.5, 2.0),
    "loam": (1.0, 10.0),
    "silt loam": (0.5, 5.0),
    "sandy loam": (5.0, 20.0),
    "loamy sand": (10.0, 40.0),
    "sand": (40.0, 200.0),
}


def _texture_class(sand: float, silt: float, clay: float) -> str:
    """Simple USDA texture-triangle classifier."""
    if clay > 40:
        return "clay"
    if sand > 70:
        if silt + clay < 30:
            return "sand"
        return "loamy sand"
    if sand > 50:
        if clay > 27:
            return "sandy clay loam"
        return "sandy loam"
    if silt + 1.5 * clay < 30:
        if sand + clay > 50:
            return "sandy loam"
        return "loam"
    if silt + 1.5 * clay < 50 and silt < 70:
        if clay >= 20 and clay < 35:
            return "clay loam"
        if silt >= 70 and clay < 30:
            return "silt loam"
        return "silt"
    if clay >= 35 and silt >= 15:
        return "clay"
    if clay >= 25 and clay < 35 and silt < 55:
        return "silty clay loam"
    return "clay loam"


def _permeability(texture: str) -> str:
    low, high = K_SAT_TABLE.get(texture, (1.0, 10.0))
    avg = (low + high) / 2.0
    if avg < 0.1:
        return "very slow (<0.1 mm/h)"
    if avg < 1.0:
        return "slow (0.1-1.0 mm/h)"
    if avg < 10.0:
        return "moderate (1-10 mm/h)"
    if avg < 40.0:
        return "moderately rapid (10-40 mm/h)"
    return "rapid (>40 mm/h)"


def _water_retention(texture: str, clay_pct: float) -> str:
    if clay_pct >= 35:
        return "high"
    if clay_pct >= 25:
        return "moderate-high"
    if clay_pct >= 15:
        return "moderate"
    if clay_pct >= 8:
        return "low-moderate"
    return "low"


def _soil_suitability_score(sand: float, clay: float, silt: float) -> float:
    """Engineering suitability score for pond embankment (0-100)."""
    if clay >= 35:
        base = 85.0
    elif clay >= 30:
        base = 80.0
    elif clay >= 25:
        base = 72.0
    elif clay >= 20:
        base = 65.0
    elif clay >= 15:
        base = 55.0
    elif clay >= 10:
        base = 45.0
    else:
        base = 30.0
    if sand >= 70:
        base -= 20
    elif sand >= 60:
        base -= 12
    elif sand >= 50:
        base -= 6
    if 30 <= silt <= 50:
        base += 3
    return max(0.0, min(100.0, base))


def _risk_level(score: float) -> str:
    if score >= 80:
        return "low"
    if score >= 60:
        return "moderate"
    if score >= 40:
        return "elevated"
    return "high"


def _recommendation(score: float, texture: str) -> str:
    if score >= 80:
        return (
            f"Soil texture ({texture}) is highly suitable for earthen pond "
            "construction. Clay-rich soils provide good compaction and low seepage potential."
        )
    if score >= 60:
        return (
            f"Soil texture ({texture}) is moderately suitable. "
            "Consider soil amendment or liner systems if seepage is a concern."
        )
    if score >= 40:
        return (
            f"Current soil ({texture}) has moderate seepage risk. "
            "Engineering measures (compaction, clay liner) recommended."
        )
    return (
        f"Soil texture ({texture}) has high infiltration risk. "
        "Geomembrane liner or significant soil amendment is strongly recommended."
    )


def get_soil_data(latitude: float, longitude: float, depth: str = "0-30cm") -> dict:
    """
    Query ISRIC SoilGrids for soil texture data at the given coordinates.

    Returns structured result with availability flag, soil fractions,
    derived classes, and suitability score (0-100).
    If the API call fails, returns available: False with an error message.
    """
    if depth == "0-30cm":
        depths_to_query = ["0-5cm", "5-15cm", "15-30cm"]
    else:
        depths_to_query = [depth]

    result_values = {p: [] for p in PROPERTIES}

    params_base = {
        "lon": round(float(longitude), 6),
        "lat": round(float(latitude), 6),
        "property_mean": PROPERTIES,
        "statistics": "mean",
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            for d in depths_to_query:
                params = {**params_base, "depth": d}
                resp = client.get(SOILGRIDS_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                features = data.get("data", {}).get("properties", {}).get("layers", [])
                if not features:
                    raise ValueError(f"No soil data returned for depth {d}.")
                for layer in features:
                    prop_name = layer.get("name")
                    if prop_name in result_values:
                        raw = layer.get("values", {}).get("mean")
                        if raw is not None:
                            result_values[prop_name].append(float(raw))

        def avg_vals(vals):
            if not vals:
                return None
            return round(sum(vals) / len(vals), 2)

        sand_pct = avg_vals(result_values["sand"])
        silt_pct = avg_vals(result_values["silt"])
        clay_pct = avg_vals(result_values["clay"])

        if None in (sand_pct, silt_pct, clay_pct):
            raise ValueError("Missing soil fraction values from SoilGrids.")

        texture = _texture_class(sand_pct, silt_pct, clay_pct)
        score = round(_soil_suitability_score(sand_pct, clay_pct, silt_pct), 1)

        return {
            "available": True,
            "source": "ISRIC SoilGrids 2.0 (global, ~250 m resolution)",
            "latitude": round(float(latitude), 6),
            "longitude": round(float(longitude), 6),
            "depth_used": depth,
            "soil_texture": texture,
            "sand_percent": sand_pct,
            "silt_percent": silt_pct,
            "clay_percent": clay_pct,
            "permeability": _permeability(texture),
            "water_retention": _water_retention(texture, clay_pct),
            "suitability_score": score,
            "risk_level": _risk_level(score),
            "recommendation": _recommendation(score, texture),
            "error": None,
        }

    except Exception as exc:
        return {
            "available": False,
            "source": "ISRIC SoilGrids 2.0",
            "latitude": round(float(latitude), 6),
            "longitude": round(float(longitude), 6),
            "depth_used": depth,
            "soil_texture": None,
            "sand_percent": None,
            "silt_percent": None,
            "clay_percent": None,
            "permeability": None,
            "water_retention": None,
            "suitability_score": None,
            "risk_level": None,
            "recommendation": (
                "Soil data could not be retrieved. "
                "Site-specific geotechnical investigation is strongly recommended "
                "before construction."
            ),
            "error": str(exc),
        }

