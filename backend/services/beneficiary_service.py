"""
Beneficiary proximity analysis.

A pond that is hydrologically perfect but far from any village is a bad
recommendation. This service measures how close a candidate pond is to the
people who would actually use it:

  * nearest settlement node (place=village|hamlet|town), and
  * density of residential buildings (building=residential|house|yes) around
    the candidate.

It is a SEMANTICALLY separate query from the hard-exclusion building query:
here buildings/settlements are used for proximity/counting, never for
exclusion.

Source: OpenStreetMap via Overpass, cached locally (same pattern as the
``osm_exclusions_*.json`` cache in land_service) so successive runs on the
same area do not re-hit the network.

Modeling assumption (documented, easy to find and adjust):

    MAX_USEFUL_DISTANCE_M
        The maximum distance (metres) a person will realistically walk to
        collect water, used as the decay horizon for the beneficiary score.
        A candidate at this distance or farther scores 0; at 0 m it scores 1.
        The value 3000 m is a planning-level assumption, not a measured
        fact, and should be reviewed against local practice.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Radius around the analysis centroid within which beneficiary features are
# fetched (metres). Kept larger than the typical analysis area so that
# nearby villages/roads that may sit just outside the DEM boundary are still
# considered.
BENEFICIARY_RADIUS_M = 2000.0

# Distance horizon for the decay score. Beyond this the score is 0.
# (Modeling assumption - see module docstring.)
MAX_USEFUL_DISTANCE_M = 3000.0

# Radius (metres) within which residential buildings are counted.
BUILDINGS_WITHIN_M = 1000.0

# Initial weight for the beneficiary factor in the multi-factor decision
# model. Other weights are redistributed proportionally to keep the total at
# 1.0 (see multifactor_suitability.DEFAULT_WEIGHTS).
BENEFICIARY_WEIGHT = 0.15

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

CACHE_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "cache"
)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _distance_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Great-circle distance in metres (Haversine)."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    )
    return 2.0 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Overpass fetch (cached)
# ---------------------------------------------------------------------------

def _fetch_beneficiary_elements(
    centroid_lat: float,
    centroid_lon: float,
    radius_m: float = BENEFICIARY_RADIUS_M,
) -> tuple[list[dict], str]:
    """Query Overpass for settlements and residential buildings (cached)."""
    cache_str = f"beneficiary|{centroid_lat:.7f}|{centroid_lon:.7f}|{radius_m:.0f}"
    cache_key = hashlib.sha1(cache_str.encode("utf-8")).hexdigest()[:16]
    cache_file = CACHE_DIR / f"osm_beneficiary_{cache_key}.json"

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------
    # Overpass query
    # -----------------------------------------------------
    query = f"""
[out:json][timeout:35];
(
  node["place"~"^(village|hamlet|town)$"](around:{int(radius_m)},{centroid_lat},{centroid_lon});
  way["building"~"^(residential|house|yes)$"](around:{int(radius_m)},{centroid_lat},{centroid_lon})
    ["building"!="apartments"]["building"!="commercial"];
);
out center;
"""

    last_error: Exception | None = None
    available = False

    for endpoint in OVERPASS_ENDPOINTS:
        try:
            with httpx.Client(
                timeout=45.0,
                headers={
                    "User-Agent": "VillagePondPlanningStudentProject/1.0"
                },
            ) as client:
                resp = client.post(endpoint, data={"data": query})
                resp.raise_for_status()
                payload = resp.json()

            available = True

            # Cache the successful result.
            try:
                cache_file.write_text(
                    json.dumps(payload),
                    encoding="utf-8",
                )
            except Exception:
                pass

            return payload.get("elements", []), endpoint

        except Exception as exc:
            last_error = exc

    # -----------------------------------------------------
    # Fall back to a cached result if the network is unavailable.
    # -----------------------------------------------------
    if cache_file.exists():
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            return payload.get("elements", []), f"cached ({cache_file.name})"
        except Exception:
            raise RuntimeError(
                "Beneficiary proximity query failed and no cached data exists: "
                f"{last_error}"
            ) from last_error

    raise RuntimeError(
        "Beneficiary proximity query failed and no cached data exists: "
        f"{last_error}"
    )


# ---------------------------------------------------------------------------
# Feature classification
# ---------------------------------------------------------------------------

def _is_settlement(element: dict) -> bool:
    tags = element.get("tags", {})
    return tags.get("place") in ("village", "hamlet", "town")


def _is_residential_building(element: dict) -> bool:
    tags = element.get("tags", {})
    return tags.get("building") in ("residential", "house", "yes")


def _element_center(element: dict, fallback_center: tuple[float, float] | None = None):
    """Return a (lat, lon) center for a node or way element."""
    if element.get("lat") is not None and element.get("lon") is not None:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center and center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return fallback_center


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_beneficiary_data(
    centroid_lat: float,
    centroid_lon: float,
    radius_m: float = BENEFICIARY_RADIUS_M,
) -> dict:
    """
    Fetch all settlement nodes and residential buildings for the analysis
    area. Called ONCE per analysis (not per candidate).

    Returns a dict with ``available``, ``settlements`` (list of (lat, lon)),
    ``buildings`` (list of (lat, lon)), and source metadata.
    """
    try:
        elements, endpoint = _fetch_beneficiary_elements(
            centroid_lat, centroid_lon, radius_m
        )
    except RuntimeError as exc:
        return {
            "available": False,
            "source": "OpenStreetMap via Overpass",
            "latitude": round(float(centroid_lat), 6),
            "longitude": round(float(centroid_lon), 6),
            "radius_m": radius_m,
            "settlements": [],
            "buildings": [],
            "settlement_count": 0,
            "building_count": 0,
            "error": str(exc),
        }

    settlements: list[tuple[float, float]] = []
    buildings: list[tuple[float, float]] = []

    for el in elements:
        center = _element_center(el, fallback_center=(centroid_lat, centroid_lon))
        if center is None:
            continue
        if _is_settlement(el):
            settlements.append(center)
        elif _is_residential_building(el):
            buildings.append(center)

    available = bool(settlements or buildings)

    return {
        "available": available,
        "source": f"OpenStreetMap via Overpass ({endpoint})",
        "latitude": round(float(centroid_lat), 6),
        "longitude": round(float(centroid_lon), 6),
        "radius_m": radius_m,
        "settlements": settlements,
        "buildings": buildings,
        "settlement_count": len(settlements),
        "building_count": len(buildings),
        "error": None,
    }


def beneficiary_metrics(
    candidate_lat: float,
    candidate_lon: float,
    beneficiary_data: dict,
    use_settlements: bool = True,
    use_buildings: bool = True,
) -> dict:
    """
    Compute proximity metrics and a 0-1 beneficiary score for a single
    candidate against the (already fetched) beneficiary data.

    Returns
    -------
    dict with
        available          : bool
        nearest_settlement_distance_m : float | None
        buildings_within_1km : float | None
        beneficiary_score  : float | None (0-1, None if unavailable)
        basis              : "settlement" | "buildings" | "combined" | "unavailable"
    """
    if not beneficiary_data or beneficiary_data.get("available") is not True:
        return {
            "available": False,
            "nearest_settlement_distance_m": None,
            "buildings_within_1km": None,
            "beneficiary_score": None,
            "basis": "unavailable",
        }

    settlements = beneficiary_data.get("settlements") or []
    buildings = beneficiary_data.get("buildings") or []

    # Nearest settlement distance
    nearest_settlement_distance: float | None = None
    if settlements and use_settlements:
        nearest_settlement_distance = min(
            _distance_m(candidate_lat, candidate_lon, s_lat, s_lon)
            for s_lat, s_lon in settlements
        )

    # Count of residential buildings within BUILDINGS_WITHIN_M
    buildings_within_2km: float | None = None
    if buildings and use_buildings:
        buildings_within_2km = float(
            sum(
                1
                for b_lat, b_lon in buildings
                if _distance_m(candidate_lat, candidate_lon, b_lat, b_lon)
                <= BUILDINGS_WITHIN_M
            )
        )

    # Determine which signal is usable.
    basis: str | None = None
    distance = nearest_settlement_distance
    building_density = buildings_within_2km

    if distance is not None and building_density is not None:
        basis = "combined"
    elif distance is not None:
        basis = "settlement"
    elif building_density is not None:
        basis = "buildings"
    else:
        return {
            "available": False,
            "nearest_settlement_distance_m": None,
            "buildings_within_1km": None,
            "beneficiary_score": None,
            "basis": "unavailable",
        }

    # Decay score from distance.
    # beneficiary_score = clamp(1 - distance / MAX_USEFUL_DISTANCE_M, 0, 1)
    if distance is not None:
        distance_score = max(0.0, min(1.0, 1.0 - distance / MAX_USEFUL_DISTANCE_M))
    else:
        distance_score = None

    # Density bonus from buildings within the proximity radius (0..1).
    if building_density is not None:
        # Assume 25 buildings nearby is effectively 'full occupancy'.
        density_score = min(1.0, building_density / 25.0)
    else:
        density_score = None

    # Combined: need at least one signal; average the available ones so a
    # candidate near a village but with sparse building tags still scores.
    available_scores = [s for s in (distance_score, density_score) if s is not None]
    if not available_scores:
        return {
            "available": False,
            "nearest_settlement_distance_m": nearest_settlement_distance,
            "buildings_within_1km": building_density,
            "beneficiary_score": None,
            "basis": basis,
        }

    score = sum(available_scores) / len(available_scores)
    return {
        "available": True,
        "nearest_settlement_distance_m": (
            round(nearest_settlement_distance, 1)
            if nearest_settlement_distance is not None
            else None
        ),
        "buildings_within_1km": (
            round(building_density, 1)
            if building_density is not None
            else None
        ),
        "beneficiary_score": round(score, 4),
        "basis": basis,
    }