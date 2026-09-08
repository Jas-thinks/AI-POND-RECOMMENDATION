from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json

import httpx
import numpy as np
from rasterio.features import geometry_mask
from shapely.geometry import (
    GeometryCollection,
    LineString,
    Polygon,
    mapping,
)
from shapely.ops import (
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


# We keep two servers so that if one Overpass server
# is temporarily unavailable, we can try another one.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


# ---------------------------------------------------------
# Configurable exclusion buffers
# ---------------------------------------------------------

@dataclass(frozen=True)
class BufferConfig:
    """Distances used to set pond candidates back from mapped features.

    All values are in metres and represent the *additional* set-back applied
    beyond the proposed pond footprint (so the whole estimated pond stays
    clear of the feature). Defaults are sized to a coarse raster
    (e.g. 10-30 m cells) without needlessly rejecting large areas on dense
    maps:

        road_buffer_m      : extra set-back from road/street centre lines.
        building_buffer_m  : extra set-back from building footprints.
        safety_buffer_m    : generic set-back for water/waterway features.
    """

    road_buffer_m: float = 15.0
    building_buffer_m: float = 10.0
    safety_buffer_m: float = 10.0


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

    # Raw Overpass elements (ways/relations with tags + geometry) used to build
    # the exclusion mask. Exposed so downstream layers (e.g. the data-driven
    # "future development risk" score) can reuse the same already-fetched data
    # without making an additional HTTP request.
    osm_elements: list = field(default_factory=list)

    # Per-category boolean exclusion masks (same shape as the DEM) so callers
    # can reason about *why* a cell was excluded. Keys: water, waterway, road,
    # building, builtup.
    category_masks: dict = field(default_factory=dict)


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
        builtup   (development indicator, not a hard exclusion by itself)
    """

    if tags.get("building"):
        return "building"

    if tags.get("highway"):
        return "road"

    if tags.get("landuse") in {
        "residential",
        "commercial",
        "industrial",
        "retail",
        "cemetery",
        "construction",
        "garages",
    }:
        return "builtup"

    if tags.get("natural") in {
        "water",
        "wetland",
    }:
        return "water"

    if tags.get("landuse") in {
        "reservoir",
        "basin",
    }:
        return "water"

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
[out:json][timeout:35];
(
  way["natural"="water"]({bbox});
  relation["natural"="water"]({bbox});

  way["natural"="wetland"]({bbox});
  relation["natural"="wetland"]({bbox});

  way["water"]({bbox});
  relation["water"]({bbox});

  way["waterway"="riverbank"]({bbox});
  relation["waterway"="riverbank"]({bbox});

  way["waterway"~"^(river|stream|canal|drain|ditch)$"]({bbox});

  way["landuse"~"^(reservoir|basin)$"]({bbox});
  relation["landuse"~"^(reservoir|basin)$"]({bbox});

  way["building"]({bbox});

  way["highway"]({bbox});
);
out geom;
"""

    last_error: Exception | None = None

    # -----------------------------------------------------
    # Try Overpass servers
    # -----------------------------------------------------

    for endpoint in OVERPASS_ENDPOINTS:

        try:

            with httpx.Client(
                timeout=45.0,
                headers={
                    "User-Agent":
                    "VillagePondPlanningStudentProject/1.0"
                },
            ) as client:

                response = client.post(
                    endpoint,
                    data={
                        "data": query
                    },
                )

                response.raise_for_status()

                payload = response.json()

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

        except Exception as exc:

            last_error = exc

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
        f"Last error: {last_error}"
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
    safety_buffer_m: float = 10.0,
    road_buffer_m: float = 15.0,
    building_buffer_m: float = 10.0,
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

    if category == "water":

        # Water bodies are already hard exclusions; the set-back keeps the
        # whole pond footprint clear of the water's edge.
        distance = (
            pond_radius_m
            + safety_buffer_m
        )

    elif category == "building":

        # Configurable building safety buffer (default 10 m) beyond the pond
        # footprint so a pond is not recommended immediately beside a building.
        distance = (
            pond_radius_m
            + max(
                building_buffer_m,
                safety_buffer_m,
            )
        )

    elif category == "road":

        # OSM roads are normally centre lines, not full road polygons.
        # Configurable road safety buffer (default 15 m) keeps the pond
        # footprint clear of both the carriageway and its verge.
        distance = (
            pond_radius_m
            + max(
                road_buffer_m,
                safety_buffer_m,
            )
        )

    elif category == "waterway":

        waterway_type = tags.get(
            "waterway"
        )

        # Approximate half-width where OSM gives
        # only the river/stream centre line.
        half_width = {

            "river": 20.0,

            "canal": 8.0,

            "stream": 4.0,

            "drain": 3.0,

            "ditch": 2.0,

        }.get(
            waterway_type,
            5.0,
        )

        distance = (
            pond_radius_m
            + safety_buffer_m
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
    buffer: BufferConfig | None = None,
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

    if buffer is None:
        buffer = DEFAULT_BUFFER_CONFIG

    # -----------------------------------------------------
    # Download OpenStreetMap features
    # -----------------------------------------------------

    try:
        elements, endpoint = (
            _download_osm_elements(
                boundary_wgs84
            )
        )
    except Exception as exc:
        # Graceful degradation: when Overpass API is unreachable
        # we cannot apply feature filtering. Return a "no filter"
        # result where all valid cells are considered free, but
        # record a clear note so the system remains usable for
        # planning purposes and the failure is visible to users.
        import numpy as _np
        full_mask = _np.ones(
            terrain.valid_mask.shape,
            dtype=bool,
        )
        return LandFilterResult(
            free_land_mask=full_mask,
            exclusion_geojson=None,
            source="OSM filter unavailable (Overpass API down)",
            feature_counts={
                "water": 0,
                "waterway": 0,
                "road": 0,
                "building": 0,
            },
            excluded_cell_count=0,
            free_cell_count=int(terrain.valid_mask.sum()),
            notes=[
                "OpenStreetMap land-filter could not be applied: "
                + str(exc)[:200],
                "All valid DEM cells were treated as buildable. "
                "Field verification still required for rivers, "
                "roads and buildings.",
            ],
        )

    feature_counts = {

        "water": 0,

        "waterway": 0,

        "road": 0,

        "building": 0,

        "builtup": 0,
    }

    buffered_geometries = []

    # Per-category clipped exclusion geometries (projected CRS) so we can build
    # category-specific masks for the "why was this excluded" / risk layers.
    proj_categories: dict[str, list] = {}

    # -----------------------------------------------------
    # Convert every OSM object into an exclusion polygon
    # -----------------------------------------------------

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
                buffer.safety_buffer_m,
            road_buffer_m=
                buffer.road_buffer_m,
            building_buffer_m=
                buffer.building_buffer_m,
        )

        if (
            buffered is None
            or buffered.is_empty
        ):
            continue

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

        proj_categories.setdefault(
            category,
            [],
        ).append(
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

    # -----------------------------------------------------
    # Per-category exclusion masks
    #
    # Built from the same projected, boundary-clipped, buffered
    # geometries used for the combined mask, so the category masks
    # are perfectly consistent. These let downstream layers answer
    # "why was a cell excluded?" without re-rasterising anything.
    # -----------------------------------------------------

    category_masks: dict[str, np.ndarray] = {}

    for category, geometries in proj_categories.items():

        if not geometries:
            continue

        geometry_union = unary_union(
            geometries
        )

        category_masks[category] = geometry_mask(
            [
                mapping(
                    geometry_union
                )
            ],
            out_shape=
                terrain.valid_mask.shape,
            transform=
                terrain.transform,
            invert=True,
            all_touched=True,
        )

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

        osm_elements=
            elements,

        category_masks=
            category_masks,

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
    )
