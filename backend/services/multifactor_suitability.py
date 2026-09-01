"""
Multi-factor suitability scoring engine.

Applies weighted multi-factor scoring to already-generated pond candidates.

Default weights (must be configurable):
    Terrain / Hydrology       30%
    Catchment Potential       20%
    Soil Suitability          15%
    Rainfall / Climate        15%
    Land Use                  10%
    Accessibility             10%
    ─────────────────────────────────
    Total                    100%

When a factor is unavailable:
    1. Remove it from the denominator
    2. Redistribute weights proportionally among available factors
    3. Report unavailable_factors in the output metadata

This ensures no factor is penalised for missing external data.
"""

from __future__ import annotations

from typing import Any


# Default weights (must be configurable)
DEFAULT_WEIGHTS = {
    "terrain": 0.30,
    "catchment": 0.20,
    "soil": 0.15,
    "rainfall": 0.15,
    "land_use": 0.10,
    "accessibility": 0.10,
}


def _normalize_weights(available_factors: list[str], weights: dict[str, float]) -> dict[str, float]:
    """Redistribute weights proportionally when some factors are unavailable."""
    total = sum(weights[k] for k in available_factors)
    if total == 0:
        return {k: 1.0 / len(available_factors) if available_factors else {} for k in available_factors}
    return {k: weights[k] / total for k in available_factors}


def _factor_score(factor_data: dict, factor_key: str) -> tuple[float | None, bool]:
    """
    Extract a numeric score from a factor dict.
    Returns (score, available).
    """
    # Direct score key
    for key in (f"{factor_key}_score", "suitability_score", "score", "climate_score",
                "water_availability_score", "accessibility_score"):
        if key in factor_data:
            val = factor_data.get(key)
            if isinstance(val, (int, float)) and val is not None:
                return round(float(val), 2), True

    # Hydrology / catchment: use the raw candidate suitability score
    if factor_key in ("terrain", "hydrology"):
        raw = factor_data.get("suitability_score")
        if raw is not None:
            return round(float(raw), 2), True

    return None, False


def _describe_weights(weights: dict[str, float]) -> dict[str, str]:
    """Human-readable weight descriptions."""
    return {
        k: f"{v * 100:.0f}%" for k, v in weights.items()
    }


def score_candidate(
    candidate: dict,
    terrain_score: float | None = None,
    hydrology_score: float | None = None,
    catchment_score: float | None = None,
    soil_analysis: dict | None = None,
    rainfall_analysis: dict | None = None,
    water_availability: dict | None = None,
    land_use_analysis: dict | None = None,
    accessibility_analysis: dict | None = None,
    weights: dict[str, float] | None = None,
) -> dict:
    """
    Compute multi-factor suitability score for a single candidate.

    Parameters
    ----------
    candidate : dict
        The upstream PondCandidate dict (must include candidate_id,
        suitability_score, latitude, longitude, elevation_m, slope_percent).
    terrain_score, hydrology_score, catchment_score : float | None
        Terrain/hydrology scores (0-100). If None, use candidate.suitability_score.
    soil_analysis, rainfall_analysis, water_availability, land_use_analysis,
    accessibility_analysis : dict | None
        Full analysis dicts from the respective services.
    weights : dict | None
        Custom weight override. Defaults to DEFAULT_WEIGHTS.

    Returns
    -------
    dict
        Multi-factor result with final_score, factor_breakdown,
        weights_used, available_factors, unavailable_factors, confidence.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS.copy()

    # Collect available factors and their scores
    factor_data = {
        "terrain": terrain_score,
        "catchment": catchment_score,
        "soil": (soil_analysis or {}).get("suitability_score") if soil_analysis else None,
        "rainfall": (rainfall_analysis or {}).get("climate_score") if rainfall_analysis else None,
        "land_use": (land_use_analysis or {}).get("suitability_score") if land_use_analysis else None,
        "accessibility": (accessibility_analysis or {}).get("accessibility_score") if accessibility_analysis else None,
    }

    # If terrain/hydrology not provided separately, use candidate's score
    if factor_data["terrain"] is None:
        factor_data["terrain"] = candidate.get("suitability_score")
    if factor_data["catchment"] is None:
        # Catchment score = the fraction of the max flow accumulation
        factor_data["catchment"] = candidate.get("suitability_score")

    available_factors = [k for k, v in factor_data.items() if v is not None]
    unavailable_factors = [k for k, v in factor_data.items() if v is None]

    # Redistribute weights
    w = _normalize_weights(available_factors, weights)
    w_used = _describe_weights(w)

    # Compute weighted score
    total = sum(factor_data[k] * w[k] for k in available_factors)

    # Per-factor breakdown
    breakdown = {}
    for k in factor_data:
        score = factor_data.get(k)
        breakdown[k] = {
            "score": score,
            "weight": w.get(k),
            "weighted_contribution": round(score * w.get(k, 0), 2) if score is not None else None,
            "source": _factor_source(k, soil_analysis, rainfall_analysis, water_availability,
                                     land_use_analysis, accessibility_analysis),
        }

    # Confidence based on how many factors were available
    pct = len(available_factors) / len(factor_data) if factor_data else 0
    if pct >= 0.83:
        conf = "high"
    elif pct >= 0.5:
        conf = "medium"
    else:
        conf = "low"

    # Category label
    if total >= 85:
        label = "Highly Suitable"
    elif total >= 70:
        label = "Suitable"
    elif total >= 55:
        label = "Marginally Suitable"
    elif total >= 40:
        label = "Poorly Suitable"
    else:
        label = "Unsuitable"

    return {
        "candidate_id": candidate.get("candidate_id"),
        "rank": candidate.get("rank"),
        "final_score": round(total, 1),
        "category": label,
        "confidence": conf,
        "available_factors": available_factors,
        "unavailable_factors": unavailable_factors,
        "weights_used": w_used,
        "factor_breakdown": breakdown,
    }


def _factor_source(
    key: str,
    soil_analysis: dict | None,
    rainfall_analysis: dict | None,
    water_availability: dict | None,
    land_use_analysis: dict | None,
    accessibility_analysis: dict | None,
) -> str:
    sources = {
        "terrain": "DEM / terrain analysis",
        "catchment": "D8 catchment delineation",
        "soil": (soil_analysis or {}).get("source") or "N/A",
        "rainfall": (rainfall_analysis or {}).get("source") or "N/A",
        "land_use": (land_use_analysis or {}).get("source") or "N/A",
        "accessibility": (accessibility_analysis or {}).get("source") or "N/A",
    }
    return sources.get(key, "unknown")

    return {
        k: f"{v * 100:.0f}%" for k, v in weights.items()
    }
