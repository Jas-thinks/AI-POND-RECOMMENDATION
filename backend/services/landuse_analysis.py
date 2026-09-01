"""
Land-use / land-cover analysis.

Uses the Overpass API to look up OSM land features around a candidate
(agricultural land, forest, built-up, etc.).

Returns a land-use distribution, dominant class, and a suitability
score that is integrated into the multi-factor engine.
"""

from __future__ import annotations

import httpx


OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

LANDUSE_MAP = {
    "farmland": "agricultural",
    "farmyard": "agricultural",
    "meadow": "agricultural",
    "orchard": "agricultural",
    "vineyard": "agricultural",
    "paddy": "agricultural",
    "plant_nursery": "agricultural",
    "forest": "forest",
    "wood": "forest",
    "scrub": "scrub",
    "heath": "scrub",
    "grass": "grassland",
    "grassland": "grassland",
    "residential": "built-up",
    "commercial": "built-up",
    "industrial": "built-up",
    "retail": "built-up",
    "cemetery": "built-up",
    "construction": "built-up",
    "reservoir": "water body",
    "basin": "water body",
    "wetland": "wetland",
    "salt_pond": "water body",
    "quarry": "bare rock",
    "sand": "bare rock",
    "bare_rock": "bare rock",
    "military": "restricted",
    "protected": "protected",
    "nature_reserve": "protected",
}

LANDUSE_SUITABILITY = {
    "agricultural": 85.0,
    "grassland": 80.0,
    "scrub": 65.0,
    "forest": 55.0,
    "built-up": 15.0,
    "bare rock": 35.0,
    "water body": 0.0,
    "wetland": 25.0,
    "protected": 20.0,
    "restricted": 5.0,
    "unknown": 60.0,
}


def _classify(tags: dict) -> str | None:
    landuse = tags.get("landuse")
    if landuse and landuse in LANDUSE_MAP:
        return LANDUSE_MAP[landuse]
    natural = tags.get("natural")
    if natural in ("wood", "forest", "scrub"):
        return "forest"
    if natural in ("water",):
        return "water body"
    if natural in ("wetland",):
        return "wetland"
    if natural in ("grassland", "meadow"):
        return "grassland"
    if natural in ("bare_rock",):
        return "bare rock"
    if tags.get("building"):
        return "built-up"
    return None



def _query_for_landuse(lat: float, lon: float, radius_m: float) -> str:
    r = int(radius_m)
    return (
        "[out:json][timeout:25];"
        f"(way(around:{r},{lat},{lon})[\"landuse\"];"
        f"relation(around:{r},{lat},{lon})[\"landuse\"];"
        f"way(around:{r},{lat},{lon})[\"natural\"];"
        f"relation(around:{r},{lat},{lon})[\"natural\"];"
        ");out tags;"
    )


def get_landuse_analysis(latitude: float, longitude: float, radius_m: float = 1500) -> dict:
    bbox_query = _query_for_landuse(latitude, longitude, radius_m)
    data: dict | None = None
    last_error: str | None = None
    for url in OVERPASS_URLS:
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, data={"data": bbox_query})
                resp.raise_for_status()
                data = resp.json()
                break
        except Exception as exc:
            last_error = str(exc)
            continue

    if data is None:
        return {
            "available": False,
            "source": "OpenStreetMap via Overpass",
            "latitude": round(float(latitude), 6),
            "longitude": round(float(longitude), 6),
            "radius_m": radius_m,
            "dominant_land_use": None,
            "land_use_distribution": {},
            "suitability_score": None,
            "restrictions": [],
            "recommendation": "Land-use data could not be retrieved.",
            "error": last_error,
        }

    elements = data.get("elements", [])
    distribution: dict[str, int] = {}
    restrictions: list[str] = []
    for el in elements:
        tags = el.get("tags", {})
        category = _classify(tags)
        if not category:
            continue
        distribution[category] = distribution.get(category, 0) + 1
        if category == "protected":
            name = tags.get("name") or tags.get("protect_class") or "protected area"
            restrictions.append(f"Protected area nearby: {name}")
        if category == "water body":
            restrictions.append("Existing water body nearby")

    if not distribution:
        return {
            "available": True,
            "source": "OpenStreetMap via Overpass",
            "latitude": round(float(latitude), 6),
            "longitude": round(float(longitude), 6),
            "radius_m": radius_m,
            "dominant_land_use": "unknown",
            "land_use_distribution": {},
            "suitability_score": 60.0,
            "restrictions": [],
            "recommendation": "No tagged land-use features found. Score reflects neutral baseline.",
            "error": None,
        }

    total = sum(distribution.values())
    dominant = max(distribution, key=distribution.get)
    dominant_score = LANDUSE_SUITABILITY.get(dominant, 60.0)
    weighted = sum(
        LANDUSE_SUITABILITY.get(cls, 60.0) * (count / total)
        for cls, count in distribution.items()
    )
    score = round(dominant_score * 0.6 + weighted * 0.4, 1)

    if score >= 80:
        rec = f"Predominantly {dominant} - highly suitable."
    elif score >= 60:
        rec = f"Predominantly {dominant} - moderately suitable."
    elif score >= 40:
        rec = f"Predominantly {dominant} - limited suitability."
    else:
        rec = f"Predominantly {dominant} - generally unsuitable."

    return {
        "available": True,
        "source": "OpenStreetMap via Overpass",
        "latitude": round(float(latitude), 6),
        "longitude": round(float(longitude), 6),
        "radius_m": radius_m,
        "dominant_land_use": dominant,
        "land_use_distribution": distribution,
        "suitability_score": score,
        "restrictions": restrictions,
        "recommendation": rec,
        "error": None,
    }
