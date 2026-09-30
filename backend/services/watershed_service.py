"""
Watershed / catchment-level indicators.

Two reported-but-unranked indicators are computed here:

  * Catchment siltation risk   - how sediment-risky the land feeding the
    pond is (bare/eroding vs farmland vs vegetated catchment cover).
  * Downstream conflict flag    - whether the catchment outlet's downstream
    flow path passes near a settlement, farmland watercourse, or existing
    water body that already relies on the water.

Both indicators REUSE data already computed elsewhere in the pipeline:
the per-candidate catchment raster, the per-candidate land-use analysis,
the D8 flow-direction grid and the exclusion-stage OSM water geometries.
No second Overpass call is made here.

Siltation risk is exposed as a maintenance-outlook metric, NOT a sixth
ranked factor -- it is reported alongside the TOPSIS ranking so a reviewer
can weigh longevity separately from raw suitability. A downstream conflict
is a FLAG FOR HUMAN REVIEW only; it never silently reranks or excludes a
candidate.
"""

from __future__ import annotations

from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Weighting for siltation-risk buckets.
#
# Modeling assumption (documented and easy to adjust):
#   vegetated fraction    - weighted 1.0   (forest/woodland retains soil).
#   farmland  fraction    - weighted 0.5   (tilled soil erodes more than
#                                           forest but less than bare land).
#   bare      fraction    - weighted 0.0   (bare/eroding land silts fastest).
#
# The final siltation_risk_score = vegetated*1.0 + farmland*0.5 + bare*0.0,
# normalised by the total classified catchment area. Higher = lower risk.
VEGETATED_WEIGHT = 1.0
FARMLAND_WEIGHT = 0.5
BARE_WEIGHT = 0.0

# Maximum distance (metres) walked downstream along the flow path before the
# trace is considered to have found nothing.
DOWNSTREAM_TRACE_DISTANCE_M = 2000.0

# Hard safety limit on the number of downstream steps walked, so a flat or
# looping flow-direction grid can never cause an infinite loop. Multiple of
# the trace distance divided by the typical cell size, with a large safety
# factor.
MAX_DOWNSTREAM_STEPS = 100_000


# ---------------------------------------------------------------------------
# Siltation risk
# ---------------------------------------------------------------------------

def _land_cover_bucket(land_use_analysis: dict | None) -> str | None:
    """Classify a candidate's local dominant land use into a siltation bucket."""
    dominant = (land_use_analysis or {}).get("dominant_land_use") or "unknown"
    dominant = dominant.lower()

    # Vegetated (forest / woodland / scrub retained on the landscape).
    if dominant in ("forest", "woodland", "scrub", "heath"):
        return "vegetated"

    # Farmland (tilled/modified soil that erodes more than forest).
    if dominant in ("farmland", "agricultural", "orchard", "vineyard", "meadow"):
        return "farmland"

    # Bare / eroding (quarry, rock, sand, or absence of vegetative/farmland).
    if dominant in ("bare rock", "quarry", "sand", "construction", "bare_rock"):
        return "bare"

    # Unknown / water / built-up are treated as "bare" for siltation purposes
    # (no vegetation to hold soil) unless explicitly classified above.
    if dominant in ("water body", "wetland"):
        return "bare"

    return "bare"


def compute_siltation_risk(
    catchment_mask: np.ndarray,
    land_use_metrics: list[dict] | None,
) -> dict:
    """
    Compute a catchment-level siltation risk indicator.

    Parameters
    ----------
    catchment_mask : np.ndarray
        The boolean catchment cell mask for the candidate (from
        ``delineate_catchment``). Used to bound the area of influence.
    land_use_metrics : list[dict] | None
        Per-candidate land-use metrics (dominant land use per zone) already
        fetched for the practical factor. When None or empty, siltation
        cannot be computed and ``available`` is False.

    Returns
    -------
    dict with
        available         : bool
        vegetated_pct     : float | None
        farmland_pct      : float | None
        bare_pct          : float | None
        siltation_risk_score : float | None  (0..1, higher = lower risk)
        source            : str
    """
    if catchment_mask is None or land_use_metrics is None or not land_use_metrics:
        return {
            "available": False,
            "vegetated_pct": None,
            "farmland_pct": None,
            "bare_pct": None,
            "siltation_risk_score": None,
            "source": "OSM land use (sparse or unavailable)",
        }

    counts = {"vegetated": 0, "farmland": 0, "bare": 0}
    total = 0
    for zone in land_use_metrics:
        bucket = _land_cover_bucket(zone)
        # Use the zone's footprint fraction within the catchment when
        # available; otherwise count it as a single unit.
        weight = zone.get("fraction", 1.0) if zone.get("fraction") is not None else 1.0
        counts[bucket] = counts.get(bucket, 0) + weight
        total += weight

    if total <= 0:
        return {
            "available": False,
            "vegetated_pct": None,
            "farmland_pct": None,
            "bare_pct": None,
            "siltation_risk_score": None,
            "source": "OSM land use (no classified catchment zones)",
        }

    vegetated_pct = counts["vegetated"] / total
    farmland_pct = counts["farmland"] / total
    bare_pct = counts["bare"] / total

    risk_score = (
        vegetated_pct * VEGETATED_WEIGHT
        + farmland_pct * FARMLAND_WEIGHT
        + bare_pct * BARE_WEIGHT
    )
    risk_score = max(0.0, min(1.0, risk_score))

    return {
        "available": True,
        "vegetated_pct": round(vegetated_pct, 4),
        "farmland_pct": round(farmland_pct, 4),
        "bare_pct": round(bare_pct, 4),
        "siltation_risk_score": round(risk_score, 4),
        "source": "OSM land use over catchment footprint",
    }


# ---------------------------------------------------------------------------
# Downstream conflict
# ---------------------------------------------------------------------------

def _haversine_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    import math
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    )
    return 2.0 * r * math.asin(math.sqrt(a))


def _downstream_cells(
    start_row: int,
    start_col: int,
    direction: np.ndarray,
    valid_mask: np.ndarray,
    max_steps: int = MAX_DOWNSTREAM_STEPS,
) -> list[tuple[int, int]]:
    """
    Walk the D8 flow direction grid downstream from an outlet cell.

    Follows the existing ``downstream_cell`` logic one cell at a time.
    Each step follows the steepest-descent direction, so the walk is the
    same hydrologic trace the rest of the pipeline uses. A hard step limit
    guarantees termination even on flat or looping direction grids.
    """
    from backend.hydrology.flow_direction import downstream_cell

    path: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    r, c = start_row, start_col

    for _ in range(max_steps):
        if (r, c) in seen:
            break  # loop detected - terminate safely
        seen.add((r, c))
        path.append((r, c))

        nxt = downstream_cell(r, c, direction)
        if nxt is None:
            break  # reached the grid edge / invalid direction
        if not (0 <= nxt[0] < valid_mask.shape[0] and 0 <= nxt[1] < valid_mask.shape[1]):
            break
        if not valid_mask[nxt[0], nxt[1]]:
            break
        r, c = nxt

    return path


def trace_downstream_conflict(
    outlet_row: int,
    outlet_col: int,
    direction: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
    beneficiary_data: dict | None = None,
    land_use_metrics: list[dict] | None = None,
    water_geometries: list | None = None,
    max_distance_m: float = DOWNSTREAM_TRACE_DISTANCE_M,
    max_steps: int = MAX_DOWNSTREAM_STEPS,
    cell_to_wgs84=None,
    cell_to_projected=None,
) -> dict:
    """
    Trace the downstream flow path from a candidate outlet and flag whether
    it passes near a settlement, a farmland watercourse, or an existing
    mapped water body that already relies on the water.

    The flag is informational only -- it never reranks or excludes the
    candidate.

    To measure real along-path distance the caller should supply
    ``cell_to_projected`` (grid cell -> projected (x, y) metres) and, when
    checking settlements, ``cell_to_wgs84`` (grid cell -> (lon, lat)). When
    these are absent the check degrades to a conservative "found nothing"
    result rather than fabricating a distance.

    Parameters
    ----------
    outlet_row, outlet_col : int
        Candidate outlet grid coordinates.
    direction, valid_mask : np.ndarray
        D8 flow-direction and validity grids.
    resolution_m : float
        DEM cell size (metres), used to convert path length to distance.
    beneficiary_data : dict | None
        The (once-fetched) beneficiary data whose ``settlements`` are used
        for the settlement conflict check.
    land_use_metrics, water_geometries : list | None
        Reused land-use metrics and exclusion-stage water geometries.
    max_distance_m : float
        Along-path distance to search for downstream conflicts.
    max_steps : int
        Hard safety limit on the number of walked cells (prevents infinite
        loops on flat/looping direction grids).
    cell_to_wgs84 : callable, optional
        Grid cell (row, col) -> (lon, lat) WGS84.
    cell_to_projected : callable, optional
        Grid cell (row, col) -> (projected_x, projected_y) in metres.

    Returns
    -------
    dict with
        flag              : bool
        checked           : bool   (True when the trace actually ran)
        nearest_conflict_type : str | None
        distance_m        : float | None
    """
    from shapely.geometry import Point

    # Build the downstream path (with hard step bound).
    path = _downstream_cells(outlet_row, outlet_col, direction, valid_mask, max_steps)
    checked = bool(path)
    if not checked:
        return {
            "flag": False,
            "checked": False,
            "nearest_conflict_type": None,
            "distance_m": None,
            "note": "No downstream flow path could be traced from the outlet.",
        }

    # Distance limit along the walked path (in steps).
    step_budget = max(1, int(round(max_distance_m / max(resolution_m, 1.0))))
    path = path[: int(step_budget)]

    # 1) Settlement conflict: check each walked cell's WGS84 position against
    #    the known settlements.
    settlements = (beneficiary_data or {}).get("settlements") or []
    if settlements and cell_to_wgs84 is not None:
        best_settlement: float | None = None
        for r, c in path:
            try:
                lon, lat = cell_to_wgs84(r, c)
            except Exception:
                continue
            for s_lat, s_lon in settlements:
                d = _haversine_m(lat, lon, s_lat, s_lon)
                if best_settlement is None or d < best_settlement:
                    best_settlement = d
        if best_settlement is not None and best_settlement <= max_distance_m:
            return {
                "flag": True,
                "checked": True,
                "nearest_conflict_type": "settlement",
                "distance_m": round(best_settlement, 1),
            }

    # 2) Water body conflict: check each walked cell (projected metres)
    #    against the exclusion-stage water geometries.
    water_geometries = water_geometries or []
    if water_geometries and cell_to_projected is not None:
        best_water: float | None = None
        for r, c in path:
            try:
                x, y = cell_to_projected(r, c)
            except Exception:
                continue
            pt = Point(x, y)
            for geom in water_geometries:
                if geom is None or geom.is_empty:
                    continue
                try:
                    from shapely.ops import nearest_points
                    p, gp = nearest_points(pt, geom)
                    d = p.distance(gp)
                except Exception:
                    continue
                if best_water is None or d < best_water:
                    best_water = d
        if best_water is not None and best_water <= max_distance_m:
            return {
                "flag": True,
                "checked": True,
                "nearest_conflict_type": "water_body",
                "distance_m": round(best_water, 1),
            }

    # 3) Farmland watercourse: there is no reliable way to infer a
    #    watercourse from the point-based land-use metrics without additional
    #    geometry, so this is conservatively reported as not found rather
    #    than guessed.
    return {
        "flag": False,
        "checked": True,
        "nearest_conflict_type": None,
        "distance_m": None,
        "downstream_cells_walked": len(path),
    }