"""
Accessibility analysis.

Uses existing infrastructure data (already fetched by land_service) to
evaluate road proximity and construction accessibility for each candidate.

Distance to nearest road:
    < 50 m   -> hard exclusion zone (too close to road)
    50-200 m -> excellent accessibility
    200-500 m -> good
    500-1000 m -> moderate
    1000-2000 m -> poor
    > 2000 m -> very poor (construction cost penalty)
"""

from __future__ import annotations


def _distance_score(distance_m: float | None) -> float | None:
    if distance_m is None:
        return None
    if distance_m < 50:
        return 0.0    # too close - hard exclusion
    if distance_m <= 200:
        return 95.0
    if distance_m <= 500:
        return 80.0
    if distance_m <= 1000:
        return 65.0
    if distance_m <= 2000:
        return 45.0
    return 25.0


def _construction_access(score: float | None) -> str:
    if score is None:
        return "unknown - data unavailable"
    if score >= 90:
        return "excellent - road access within 200 m"
    if score >= 75:
        return "good - road access within 500 m"
    if score >= 55:
        return "moderate - road access within 1 km"
    if score >= 30:
        return "poor - remote site, high construction logistics cost"
    return "very poor - significant access construction required"


def get_accessibility_analysis(
    nearest_road_distance_m: float | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict:
    """
    Evaluate construction accessibility.

    If nearest_road_distance_m is provided, uses it directly.
    Otherwise uses Overpass to find the nearest highway node.

    Returns a score (0-100) and construction access description.
    """
    score = _distance_score(nearest_road_distance_m)
    access_str = _construction_access(score)

    if nearest_road_distance_m is None:
        recommendation = (
            "Road accessibility could not be determined. "
            "Verify proximity to nearest motorable road via satellite "
            "imagery before proceeding with construction planning."
        )
    elif nearest_road_distance_m < 50:
        recommendation = (
            "Pond site is too close to a road. "
            "Minimum setback of 50 m is required to avoid structural "
            "conflict with road infrastructure."
        )
    elif nearest_road_distance_m <= 500:
        recommendation = (
            f"Good accessibility ({nearest_road_distance_m:.0f} m to nearest road). "
            "Standard construction equipment access expected."
        )
    elif nearest_road_distance_m <= 1500:
        recommendation = (
            f"Moderate accessibility ({nearest_road_distance_m:.0f} m to nearest road). "
            "Access road or track may need improvement."
        )
    else:
        recommendation = (
            f"Remote site ({nearest_road_distance_m:.0f} m to nearest road). "
            "Significant access-road construction or logistics planning required. "
            "Construction cost will be substantially higher."
        )

    return {
        "available": nearest_road_distance_m is not None,
        "source": "OpenStreetMap via Overpass" if nearest_road_distance_m is not None else "N/A",
        "nearest_road_distance_m": nearest_road_distance_m,
        "accessibility_score": score,
        "construction_access": access_str,
        "recommendation": recommendation,
        "error": None,
    }
