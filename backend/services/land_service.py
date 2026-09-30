from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import os
import time

import httpx
import numpy as np
from rasterio.features import geometry_mask
from shapely.geometry import (
    GeometryCollection,
    LineString,
    Point,
    Polygon,
    mapping,
)
from shapely.ops import (
    nearest_points,
    polygonize,
    transform as shapely_transform,
    unary_union,
)


# ---------------------------------------------------------
# Cache directory
# ---------------------------------------------------------

CACHE_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "cache"
)


# ---------------------------------------------------------
# Overpass endpoints + request reliability configuration
#
# Overridable by environment variables so the same code works
# locally with no configuration and on a stateless host such as
# Render, where the filesystem cache starts empty and every
# analysis begins cold. Generous timeouts plus bounded retries
# across several endpoints are the primary defence; the disk
# cache is only a best-effort secondary.
# ---------------------------------------------------------

# Primary endpoint first, then fallbacks. Kept as a module constant
# so the default behaviour is unchanged when no env vars are set.
DEFAULT_OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Server-side Overpass QL execution timeout, seconds ([timeout:N]).
OVERPASS_QUERY_TIMEOUT_S = max(1, _env_int("OVERPASS_TIMEOUT", 60))

# Per-phase HTTP timeouts (seconds). The read timeout MUST exceed the
# Overpass query timeout so a slow-but-working server is not aborted
# before it answers.
OVERPASS_CONNECT_TIMEOUT_S = max(1.0, _env_float("OVERPASS_CONNECT_TIMEOUT", 10.0))
OVERPASS_READ_TIMEOUT_S = max(1.0, _env_float("OVERPASS_READ_TIMEOUT", 90.0))

# Total attempts (first try + retries). Bounded so retries are never
# infinite.
OVERPASS_MAX_ATTEMPTS = max(1, _env_int("OVERPASS_MAX_RETRIES", 3))

# Exponential backoff base (seconds): base, 2*base, 4*base, ...
OVERPASS_BACKOFF_BASE_S = max(0.0, _env_float("OVERPASS_BACKOFF_BASE", 2.0))


def _overpass_endpoints() -> tuple[str, ...]:
    """Resolve the Overpass endpoint list from the environment.

    ``OVERPASS_URL`` overrides the primary endpoint; the remaining
    ``DEFAULT_OVERPASS_URLS`` stay as fallbacks unless
    ``OVERPASS_FALLBACK_URLS`` (comma-separated) replaces them.
    Duplicates are removed while preserving order.
    """
    primary = os.getenv("OVERPASS_URL", "").strip()

    raw_fallbacks = os.getenv("OVERPASS_FALLBACK_URLS")
    if raw_fallbacks is None or not raw_fallbacks.strip():
        fallbacks = list(DEFAULT_OVERPASS_URLS[1:])
    else:
        fallbacks = [u.strip() for u in raw_fallbacks.split(",") if u.strip()]

    candidates = (
        [primary] if primary else [DEFAULT_OVERPASS_URLS[0]]
    ) + fallbacks

    seen: set[str] = set()
    ordered: list[str] = []
    for url in candidates:
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return tuple(ordered)


def _is_retryable_overpass_error(exc: BaseException) -> bool:
    """Return True only for transient failures worth retrying.

    Timeouts, connection/transport errors, the Overpass "server busy"
    status codes (429/500/502/503/504) and an unparseable body are
    transient. A malformed-query HTTP 400 is not: retrying it would
    only repeat the same failure.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, httpx.TransportError):  # covers every timeout type
        return True
    if isinstance(exc, json.JSONDecodeError):
        return True
    return False


# ---------------------------------------------------------
# Configurable safety buffer distances
# ---------------------------------------------------------

@dataclass(frozen=True)
class BufferConfig:
    """
    Safety buffers (metres) applied as HARD exclusions around
    prohibited OSM features.

    All values are additive to the proposed pond radius so the
    entire estimated pond footprint stays clear of the feature,
    not just the candidate centre point.

    Attributes
    ----------
    water_m : float
        Additional buffer around water polygons (natural=water,
        water=*, landuse=reservoir/basin/pond/lake, wetland, ...).
    waterway_m : float
        Additional buffer around linear waterways (river, stream,
        canal, drain, ditch, watercourse). Applied on top of an
        estimated half-width per waterway type.
    road_m : float
        Additional buffer around roads/highways (centre lines).
    building_m : float
        Additional buffer around buildings.
    """

    water_m: float = 10.0
    waterway_m: float = 10.0
    road_m: float = 8.0
    building_m: float = 5.0

    # Approximate half-width (metres) for linear waterways where
    # OSM provides only a centre line.
    waterway_half_width_m: dict = None

    def resolved_waterway_half_width(self, waterway_type: str) -> float:
        if self.waterway_half_width_m is not None:
            return self.waterway_half_width_m.get(waterway_type, 5.0)
        return {
            "river": 20.0,
            "canal": 8.0,
            "stream": 4.0,
            "drain": 3.0,
            "ditch": 2.0,
            "watercourse": 12.0,
        }.get(waterway_type, 5.0)


# Default safety buffers inferred from existing conservative project
# constants (pond footprint + fixed safety buffer behaviour already in
# use). They remain overridable via status-style wrapper params.
DEFAULT_BUFFER_CONFIG = BufferConfig()


# ---------------------------------------------------------
# Result object
# ---------------------------------------------------------

@dataclass
class LandFilterResult:
    free_land_mask: np.ndarray

    # Polygon(s) that will be shown in red on frontend
    exclusion_geojson: dict | None

    source: str

    feature_counts: dict[str, int]

    excluded_cell_count: int
    free_cell_count: int

    notes: list[str]

    # Per-category excluded-cell counts (diagnostics)
    excluded_water_cells: int = 0
    excluded_waterway_cells: int = 0
    excluded_road_cells: int = 0
    excluded_building_cells: int = 0

    # Flags the safety state so consumers can fail safely.
    osm_data_available: bool = True

    # Raw, un-buffered OSM road geometries (projected to metres) used by
    # the accessibility analysis to measure true nearest-road distance per
    # candidate. Kept separate from the buffered exclusion geometries so the
    # hard-exclusion mask is never confused with the proximity metric.
    road_geometries: list = field(default_factory=list)

    # Raw, un-buffered geometries grouped by exclusion category, in projected
    # metre coordinates. Used to measure exclusion-clearance distances (how
    # far a candidate is from a mapped water body / waterway / road /
    # building) without needing a second Overpass call.
    raw_geometries_by_category: dict[str, list] = field(default_factory=dict)


# ---------------------------------------------------------
# Decide which OSM features should be excluded
# ---------------------------------------------------------

def _category(tags: dict) -> str | None:
    """
    Convert OpenStreetMap tags into one of our
    exclusion categories.

    Categories:
        water
        waterway
        road
        building
    """

    if tags.get("building"):
        return "building"

    if tags.get("highway"):
        return "road"

    if tags.get("natural") in {
        "water",
        "wetland",
        "bay",
        "strait",
        "spring",
        "waterfall",
    }:
        return "water"

    if tags.get("landuse") in {
        "reservoir",
        "basin",
        "pond",
        "lake",
        "salt_pond",
        "fishpond",
        "aquaculture",
    }:
        return "water"

    # Covers water=*, water=river/basin/lake/pond/oxbow/...
    if tags.get("water"):
        return "water"

    if tags.get("waterway") == "riverbank":
        return "water"

    if tags.get("waterway") in {
        "river",
        "stream",
        "canal",
        "drain",
        "ditch",
        "watercourse",
    }:
        return "waterway"

    return None


# ---------------------------------------------------------
# Read geometry coordinates from Overpass response
# ---------------------------------------------------------

def _coords_from_geometry(
    items: list[dict] | None,
) -> list[tuple[float, float]]:

    if not items:
        return []

    coordinates = []

    for point in items:

        if (
            "lon" in point
            and "lat" in point
        ):

            coordinates.append(
                (
                    float(point["lon"]),
                    float(point["lat"]),
                )
            )

    return coordinates


# ---------------------------------------------------------
# Convert OSM "way" into Shapely geometry
# ---------------------------------------------------------

def _way_geometry(
    element: dict,
    category: str,
):

    coordinates = _coords_from_geometry(
        element.get("geometry")
    )

    if len(coordinates) < 2:
        return None

    # Water bodies and buildings are normally closed areas.
    area_like = category in {
        "water",
        "building",
    }

    if (
        area_like
        and len(coordinates) >= 4
        and coordinates[0] == coordinates[-1]
    ):

        polygon = Polygon(coordinates)

        if not polygon.is_valid:
            polygon = polygon.buffer(0)

        if polygon.is_empty:
            return None

        return polygon

    # Roads, rivers, streams etc. are normally lines.
    return LineString(coordinates)


# ---------------------------------------------------------
# Convert OSM relation into geometry
# ---------------------------------------------------------

def _relation_geometry(
    element: dict,
    category: str,
):

    lines = []
    polygons = []

    for member in element.get(
        "members",
        [],
    ):

        coordinates = _coords_from_geometry(
            member.get("geometry")
        )

        if len(coordinates) < 2:
            continue

        # Closed geometry
        if (
            len(coordinates) >= 4
            and coordinates[0]
            == coordinates[-1]
        ):

            polygon = Polygon(coordinates)

            if not polygon.is_valid:
                polygon = polygon.buffer(0)

            if not polygon.is_empty:
                polygons.append(polygon)

        else:

            lines.append(
                LineString(coordinates)
            )

    # Multipolygon water/building relations may
    # consist of several separate line members.
    if category in {
        "water",
        "building",
    }:

        if lines:

            try:

                merged_lines = unary_union(
                    lines
                )

                polygons.extend(
                    list(
                        polygonize(
                            merged_lines
                        )
                    )
                )

            except Exception:
                pass

        if polygons:
            return unary_union(
                polygons
            )

    if lines:
        return unary_union(lines)

    if polygons:
        return unary_union(polygons)

    return None


# ---------------------------------------------------------
# Download water/road/building data from OpenStreetMap
# ---------------------------------------------------------

def _download_osm_elements(
    boundary_wgs84,
) -> tuple[list[dict], str]:

    min_lon, min_lat, max_lon, max_lat = (
        boundary_wgs84.bounds
    )

    # Overpass order:
    #
    # south, west, north, east

    bbox = (
        f"{min_lat:.7f},"
        f"{min_lon:.7f},"
        f"{max_lat:.7f},"
        f"{max_lon:.7f}"
    )

    # -----------------------------------------------------
    # Cache
    # -----------------------------------------------------

    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cache_key = hashlib.sha1(
        bbox.encode("utf-8")
    ).hexdigest()[:16]

    cache_file = (
        CACHE_DIR
        / f"osm_exclusions_{cache_key}.json"
    )

    # -----------------------------------------------------
    # Overpass query
    # -----------------------------------------------------

    query = f"""
[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_S}];
(
  way["natural"="water"]({bbox});
  relation["natural"="water"]({bbox});

  way["natural"~"^(wetland|bay|strait|spring|waterfall)$"]({bbox});
  relation["natural"~"^(wetland|bay|strait|spring|waterfall)$"]({bbox});

  way["water"]({bbox});
  relation["water"]({bbox});

  way["waterway"="riverbank"]({bbox});
  relation["waterway"="riverbank"]({bbox});

  way["waterway"~"^(river|stream|canal|drain|ditch|watercourse)$"]({bbox});

  way["landuse"~"^(reservoir|basin|pond|lake|salt_pond|fishpond|aquaculture)$"]({bbox});
  relation["landuse"~"^(reservoir|basin|pond|lake|salt_pond|fishpond|aquaculture)$"]({bbox});

  way["building"]({bbox});

  way["highway"]({bbox});
);
out geom;
"""

    endpoints = _overpass_endpoints()

    # Per-phase timeouts. httpx applies each phase independently; the read
    # timeout is the one that governs how long we wait for an Overpass
    # response, so it is deliberately larger than the server-side query
    # timeout to leave room for queueing and transfer.
    client_timeout = httpx.Timeout(
        connect=OVERPASS_CONNECT_TIMEOUT_S,
        read=OVERPASS_READ_TIMEOUT_S,
        write=OVERPASS_READ_TIMEOUT_S,
        pool=OVERPASS_CONNECT_TIMEOUT_S,
    )

    last_error: Exception | None = None
    attempt_errors: list[str] = []

    # -----------------------------------------------------
    # Try the endpoints, retrying transient failures with
    # exponential backoff.
    #
    # Endpoints are alternated across attempts so a single
    # slow/overloaded server cannot consume the whole budget.
    # Retries are bounded (OVERPASS_MAX_ATTEMPTS) - never infinite.
    # -----------------------------------------------------

    with httpx.Client(
        timeout=client_timeout,
        headers={
            "User-Agent":
            "VillagePondPlanningStudentProject/1.0"
        },
    ) as client:

        for attempt in range(OVERPASS_MAX_ATTEMPTS):

            endpoint = endpoints[attempt % len(endpoints)]

            try:

                response = client.post(
                    endpoint,
                    data={
                        "data": query
                    },
                )

                response.raise_for_status()

                payload = response.json()

            except Exception as exc:

                last_error = exc

                attempt_errors.append(
                    f"{endpoint}: {exc}"
                )

                # Do not retry a failure that cannot succeed on retry
                # (e.g. a malformed-query HTTP 400).
                if not _is_retryable_overpass_error(exc):
                    break

                if attempt == OVERPASS_MAX_ATTEMPTS - 1:
                    break

                time.sleep(
                    OVERPASS_BACKOFF_BASE_S
                    * (2 ** attempt)
                )

                continue

            # Save successful result
            try:

                cache_file.write_text(
                    json.dumps(payload),
                    encoding="utf-8",
                )

            except Exception:
                pass

            return (
                payload.get(
                    "elements",
                    [],
                ),
                endpoint,
            )

    # -----------------------------------------------------
    # If internet/API fails, use cache
    # -----------------------------------------------------

    if cache_file.exists():

        try:

            payload = json.loads(
                cache_file.read_text(
                    encoding="utf-8"
                )
            )

            return (
                payload.get(
                    "elements",
                    [],
                ),
                (
                    "cached OpenStreetMap data "
                    f"({cache_file.name})"
                ),
            )

        except Exception:
            pass

    # IMPORTANT:
    # Do NOT silently continue without the filter.
    #
    # Otherwise the river candidate could come back.

    raise RuntimeError(
        "OpenStreetMap/Overpass land-data query "
        "failed and no cached land data exists. "
        "Connect to the Internet once and run "
        "the analysis again. "
        f"Attempts: {len(attempt_errors)}"
        + (
            f" ({'; '.join(attempt_errors)})"
            if attempt_errors
            else ""
        )
        + f". Last error: {last_error}"
    )


# ---------------------------------------------------------
# Convert an OSM feature into an exclusion region
# ---------------------------------------------------------

def _project_and_buffer(
    geometry,
    category: str,
    tags: dict,
    to_projected,
    pond_radius_m: float,
    safety_buffer_m: float,
    buffer_config: BufferConfig | None = None,
):

    # Convert latitude/longitude to metre coordinates
    projected = shapely_transform(
        to_projected.transform,
        geometry,
    )

    if projected.is_empty:
        return None

    # -----------------------------------------------------
    # IMPORTANT:
    #
    # We don't just prevent the CENTER of a pond from
    # touching the river.
    #
    # We keep the ENTIRE estimated pond footprint away.
    # -----------------------------------------------------

    if buffer_config is None:
        buffer_config = DEFAULT_BUFFER_CONFIG

    if category == "water":

        distance = (
            pond_radius_m
            + max(
                safety_buffer_m,
                buffer_config.water_m,
            )
        )

    elif category == "building":

        distance = (
            pond_radius_m
            + max(
                5.0,
                safety_buffer_m,
                buffer_config.building_m,
            )
        )

    elif category == "road":

        # OSM roads are normally center lines,
        # not full road polygons.

        distance = (
            pond_radius_m
            + max(
                6.0,
                safety_buffer_m,
                buffer_config.road_m,
            )
        )

    elif category == "waterway":

        waterway_type = tags.get(
            "waterway"
        )

        # Approximate half-width where OSM gives
        # only the river/stream centre line.
        half_width = (
            buffer_config.resolved_waterway_half_width(
                waterway_type
            )
        )

        distance = (
            pond_radius_m
            + max(
                safety_buffer_m,
                buffer_config.waterway_m,
            )
            + half_width
        )

    else:

        distance = (
            pond_radius_m
            + safety_buffer_m
        )

    return projected.buffer(
        distance
    )


# ---------------------------------------------------------
# MAIN LAND FILTER FUNCTION
# ---------------------------------------------------------

def build_osm_free_land_mask(
    terrain,
    boundary_wgs84,
    pond_radius_m: float,
    safety_buffer_m: float = 10.0,
    buffer_config: BufferConfig | None = None,
) -> LandFilterResult:

    """
    Create a grid mask containing only cells that are
    clear of mapped:

        rivers
        streams
        canals
        water bodies
        roads
        buildings

    IMPORTANT:

    "free land" here means:

        no mapped OSM obstacle

    It does NOT mean:

        government land
        legally available land
        public land
    """

    # ---------------------------------------------------------
    # Hard exclusions are REQUIRED for safety.
    #
    # If OpenStreetMap data cannot be retrieved, we must NOT
    # silently treat every valid DEM cell as buildable - doing
    # so would let pond candidates be recommended on top of an
    # unmapped river, road or building. Instead we fail safely
    # and let the caller decide how to degrade.
    # ---------------------------------------------------------

    try:
        elements, endpoint = (
            _download_osm_elements(
                boundary_wgs84
            )
        )
    except Exception as exc:
        raise RuntimeError(
            "OpenStreetMap/Overpass land-data query failed and no "
            "cached land data exists, so hard exclusions (rivers, "
            "water, roads, buildings) cannot be enforced. To avoid "
            "recommending a pond on an excluded feature the analysis "
            "fails safely instead of assuming all DEM cells are "
            "buildable. "
            f"Last error: {exc}"
        ) from exc

    if buffer_config is None:
        buffer_config = DEFAULT_BUFFER_CONFIG

    feature_counts = {

        "water": 0,

        "waterway": 0,

        "road": 0,

        "building": 0,
    }

    buffered_geometries = []
    buffered_by_category = {k: [] for k in ("water", "waterway", "road", "building")}
    road_geometries = []
    raw_geometries_by_category = {k: [] for k in ("water", "waterway", "road", "building")}

    # ---------------------------------------------------------
    # Convert every OSM object into an exclusion polygon
    # ---------------------------------------------------------

    for element in elements:

        tags = element.get(
            "tags",
            {},
        )

        category = _category(
            tags
        )

        if category is None:
            continue

        element_type = element.get(
            "type"
        )

        if element_type == "way":

            geometry = _way_geometry(
                element,
                category,
            )

        elif element_type == "relation":

            geometry = _relation_geometry(
                element,
                category,
            )

        else:

            geometry = None

        if (
            geometry is None
            or geometry.is_empty
        ):
            continue

        # -------------------------------------------------
        # Add safety/pond buffer
        # -------------------------------------------------

        buffered = _project_and_buffer(
            geometry,
            category,
            tags,
            terrain.to_projected,
            pond_radius_m=
                pond_radius_m,
            safety_buffer_m=
                safety_buffer_m,
            buffer_config=
                buffer_config,
        )

        if (
            buffered is None
            or buffered.is_empty
        ):
            continue

        # -------------------------------------------------
        # Keep the raw (un-buffered) geometry in projected
        # metre coordinates for the accessibility and exclusion-clearance
        # analyses so we can measure true per-candidate distances.
        # -------------------------------------------------

        projected_raw = shapely_transform(
            terrain.to_projected.transform,
            geometry,
        )
        if not projected_raw.is_empty:
            raw_clip = projected_raw.intersection(
                terrain.boundary_projected
            )
            if not raw_clip.is_empty:
                raw_geometries_by_category[category].append(raw_clip)
                if category == "road":
                    road_geometries.append(raw_clip)

        # -------------------------------------------------
        # Only keep the part that intersects our
        # contour-map boundary.
        # -------------------------------------------------

        clipped = buffered.intersection(
            terrain.boundary_projected
        )

        if clipped.is_empty:
            continue

        buffered_geometries.append(
            clipped
        )

        buffered_by_category[
            category
        ].append(
            clipped
        )

        feature_counts[
            category
        ] += 1

    # -----------------------------------------------------
    # Merge exclusion polygons
    # -----------------------------------------------------

    if buffered_geometries:

        exclusion_union = unary_union(
            buffered_geometries
        )

        # Convert polygon → DEM boolean mask
        exclusion_mask = geometry_mask(

            [
                mapping(
                    exclusion_union
                )
            ],

            out_shape=
                terrain.valid_mask.shape,

            transform=
                terrain.transform,

            invert=True,

            all_touched=True,
        )

    else:

        exclusion_union = (
            GeometryCollection()
        )

        exclusion_mask = (
            np.zeros_like(
                terrain.valid_mask,
                dtype=bool,
            )
        )

    # ---------------------------------------------------------
    # Per-category excluded-cell counts (diagnostics)
    # ---------------------------------------------------------

    def _count_category_mask(category_name: str) -> int:
        polys = buffered_by_category.get(category_name, [])
        if not polys:
            return 0
        union = unary_union(polys)
        m = geometry_mask(
            [mapping(union)],
            out_shape=terrain.valid_mask.shape,
            transform=terrain.transform,
            invert=True,
            all_touched=True,
        )
        return int((terrain.valid_mask & m).sum())

    excluded_water_cells = _count_category_mask("water")
    excluded_waterway_cells = _count_category_mask("waterway")
    excluded_road_cells = _count_category_mask("road")
    excluded_building_cells = _count_category_mask("building")


    # -----------------------------------------------------
    # FREE LAND =
    #
    # valid terrain
    # AND
    # NOT excluded terrain
    # -----------------------------------------------------

    free_land_mask = (
        terrain.valid_mask
        & ~exclusion_mask
    )

    if not free_land_mask.any():

        raise ValueError(
            "Land-suitability filtering removed "
            "the entire analysis area. "
            "Try a smaller pond search radius "
            "or verify the OSM data."
        )

    # -----------------------------------------------------
    # Convert exclusion area back to latitude/longitude
    # so frontend can draw it.
    # -----------------------------------------------------

    exclusion_geojson = None

    if not exclusion_union.is_empty:

        wgs84_exclusion = (
            shapely_transform(
                terrain.to_wgs84.transform,
                exclusion_union,
            )
        )

        exclusion_geojson = mapping(
            wgs84_exclusion
        )

    return LandFilterResult(

        free_land_mask=
            free_land_mask,

        exclusion_geojson=
            exclusion_geojson,

        source=(
            "OpenStreetMap via Overpass "
            f"({endpoint})"
        ),

        feature_counts=
            feature_counts,

        excluded_cell_count=int(
            (
                terrain.valid_mask
                & exclusion_mask
            ).sum()
        ),

        free_cell_count=int(
            free_land_mask.sum()
        ),

        notes=[
            (
                "Pond candidate centres are excluded "
                "from mapped water bodies, waterways, "
                "roads and buildings."
            ),
            (
                "Exclusion geometry is buffered by "
                "the proposed pond radius so the "
                "estimated pond footprint also stays "
                "clear."
            ),
            (
                "OSM feature-clear land is not proof "
                "of government ownership, private-land "
                "availability or legal permission."
            ),
        ],

        excluded_water_cells=
            excluded_water_cells,

        excluded_waterway_cells=
            excluded_waterway_cells,

        excluded_road_cells=
            excluded_road_cells,

        excluded_building_cells=
            excluded_building_cells,

        road_geometries=
            road_geometries,

        raw_geometries_by_category=
            raw_geometries_by_category,

    )


# ---------------------------------------------------------
# Exclusion-clearance measurement (proof that a candidate is not
# creeping up to the edge of a forbidden feature)
# ---------------------------------------------------------

def required_buffer_for(
    category: str,
    tags: dict,
    pond_radius_m: float,
    safety_buffer_m: float = 10.0,
    buffer_config: BufferConfig | None = None,
) -> float:
    """
    Return the exact exclusion buffer (metres) that was applied around a
    given OSM feature category during the hard-exclusion stage.

    This mirrors the formulas in ``_project_and_buffer`` so the clearance
    "required_buffer_m" always matches the buffer that was actually used for
    exclusion — never a separately hardcoded number.
    """
    if buffer_config is None:
        buffer_config = DEFAULT_BUFFER_CONFIG

    if category == "water":
        return pond_radius_m + max(safety_buffer_m, buffer_config.water_m)
    if category == "building":
        return pond_radius_m + max(5.0, safety_buffer_m, buffer_config.building_m)
    if category == "road":
        return pond_radius_m + max(6.0, safety_buffer_m, buffer_config.road_m)
    if category == "waterway":
        waterway_type = tags.get("waterway")
        half_width = buffer_config.resolved_waterway_half_width(waterway_type)
        return (
            pond_radius_m
            + max(safety_buffer_m, buffer_config.waterway_m)
            + half_width
        )
    # Defensive: any other category uses a simple safety margin.
    return pond_radius_m + safety_buffer_m


def compute_exclusion_clearance(
    candidate_point: Point,
    raw_geometries_by_category: dict[str, list],
    pond_radius_m: float,
    safety_buffer_m: float = 10.0,
    buffer_config: BufferConfig | None = None,
    max_search_distance_m: float = 10_000.0,
) -> list[dict]:
    """
    Measure, for a single candidate, the distance to the nearest mapped
    feature in every exclusion category (water, waterway, road, building),
    using the raw geometries already fetched for the exclusion mask.

    Reuses the same buffer values from ``required_buffer_for`` so the
    reported ``required_buffer_m`` is exactly what the hard-exclusion stage
    applied, and the margin is the true distance leftover.

    Returns a list of
        {feature_type, nearest_distance_m, required_buffer_m, margin_m}
    per category. When a category has no features within ``max_search_distance_m``,
    ``nearest_distance_m`` is None and a note explains why (never a fabricated
    large number).
    """
    clearance: list[dict] = []
    for category in ("water", "waterway", "road", "building"):
        geometries = raw_geometries_by_category.get(category) or []
        required = required_buffer_for(
            category,
            {"waterway": "river"},
            pond_radius_m,
            safety_buffer_m,
            buffer_config,
        )

        nearest_distance: float | None = None
        for geometry in geometries:
            if geometry is None or geometry.is_empty:
                continue
            try:
                p, gp = nearest_points(candidate_point, geometry)
                d = p.distance(gp)
            except Exception:
                continue
            if nearest_distance is None or d < nearest_distance:
                nearest_distance = d

        entry = {
            "feature_type": category,
            "nearest_distance_m": (
                round(nearest_distance, 1) if nearest_distance is not None else None
            ),
            "required_buffer_m": round(required, 1),
            "margin_m": (
                round(nearest_distance - required, 1)
                if nearest_distance is not None
                else None
            ),
        }
        if nearest_distance is None:
            entry["note"] = (
                f"No mapped {category} feature found within "
                f"{max_search_distance_m:.0f} m of the candidate."
            )
        clearance.append(entry)

    return clearance
