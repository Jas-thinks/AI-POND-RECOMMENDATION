from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from rasterio.features import shapes

from scipy.ndimage import binary_dilation
from scipy.ndimage import distance_transform_edt

from shapely.geometry import (
    mapping,
    Point,
    shape,
)

from shapely.ops import (
    transform as shapely_transform,
    nearest_points,
    unary_union,
)

from backend.hydrology.catchment import (
    delineate_catchment,
)

from backend.hydrology.flow_accumulation import (
    calculate_flow_accumulation,
)

from backend.hydrology.flow_direction import (
    calculate_flow_direction,
)

from backend.hydrology.sink_fill import (
    fill_sinks_priority_flood,
)

from backend.pond.candidate import (
    find_pond_candidates,
)

from backend.pond.metrics import (
    estimate_candidate_water_metrics,
)

from backend.services.kml_service import (
    parse_contour_file,
)

from backend.services.land_service import (
    BufferConfig,
    build_osm_free_land_mask,
    compute_exclusion_clearance,
    DEFAULT_BUFFER_CONFIG,
)

from backend.services.rainfall_service import (
    get_historical_rainfall,
)

from backend.terrain.dem_generator import (
    build_dem,
)

from backend.services.soil_analysis import (
    get_soil_data,
)

from backend.services.rainfall_analysis import (
    get_climate_analysis,
)

from backend.services.water_availability import (
    estimate_water_availability,
)

from backend.services.landuse_analysis import (
    get_landuse_analysis,
)

from backend.services.accessibility_analysis import (
    get_accessibility_analysis,
)

from backend.services.environmental_constraints import (
    evaluate_constraints,
)

from backend.services.storage_estimation import (
    estimate_storage,
)

from backend.services.multifactor_suitability import (
    diversify_top_n,
    practical_raw_score,
    rank_candidates_topsis,
)

from backend.services.beneficiary_service import (
    beneficiary_metrics,
    fetch_beneficiary_data,
)

from backend.services.watershed_service import (
    compute_siltation_risk,
    trace_downstream_conflict,
)

from backend.services.data_confidence import (
    assess_confidence,
)

from backend.terrain.slope import (
    calculate_slope_percent,
)

from backend.utils.timing import (
    _emit as _emit_timing,
)

from backend.utils.timing import (
    timed_stage,
)


# ---------------------------------------------------------
# DEM-derived hydrology safety configuration
# ---------------------------------------------------------

@dataclass(frozen=True)
class HydrologySafetyConfig:
    """
    Dem-derived safety exclusions derived from the D8 flow
    accumulation grid.

    OSM is not guaranteed to contain every small stream. The DEM
    flow-accumulation grid reveals strong/obvious drainage channels
    that are treated as hard exclusion corridors.

    The threshold is intentionally set to the extreme tail of the
    non-zero accumulation distribution (99.5th percentile) so that
    only major, well-defined drainage/channel cores are excluded.
    Broad high-accumulation but non-channel areas remain available
    for candidate selection, preserving large contributing catchments
    while still preventing ponds from being placed directly on a
    channel centre.

    Attributes
    ----------
    enabled : bool
        Whether to apply the DEM-derived hydrology safety mask.
    accumulation_percentile : float
        Percentile (0-100) of non-zero flow accumulation used as the
        channel-core threshold. Cells at or above this percentile are
        treated as drainage channels and excluded.
    buffer_m : float
        Extra radius (metres) applied around detected channels.
    """

    enabled: bool = True
    accumulation_percentile: float = 99.5
    buffer_m: float = 10.0


def build_hydrology_safety_mask(
    accumulation: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
    config: HydrologySafetyConfig | None = None,
) -> np.ndarray:
    """
    Build a boolean mask of DEM-derived drainage channel cores
    (streams, river centres) to be treated as HARD exclusions.

    A cell contributes to a channel core when its flow accumulation is at
    or above a configurable percentile of the non-zero accumulation
    distribution, dilated by an optional buffer / pond footprint so
    the entire proposed pond stays out of the channel core.
    """
    if config is None:
        config = HydrologySafetyConfig()

    if not config.enabled:
        return np.zeros_like(valid_mask, dtype=bool)

    channel_mask = np.zeros_like(valid_mask, dtype=bool)

    acc_nonzero = accumulation[valid_mask & np.isfinite(accumulation) & (accumulation > 0)]
    if acc_nonzero.size == 0:
        return channel_mask

    threshold = float(np.nanpercentile(acc_nonzero, config.accumulation_percentile))
    channel_mask = (
        valid_mask
        & np.isfinite(accumulation)
        & (accumulation >= threshold)
    )

    if not channel_mask.any():
        return channel_mask

    # Dilate channel cores into a conservative exclusion corridor that
    # also covers the proposed pond footprint radius.
    radius_cells = max(1, int(round(config.buffer_m / resolution_m)))
    if radius_cells > 1:
        channel_mask = binary_dilation(
            channel_mask,
            iterations=radius_cells,
            border_value=0,
        )
        channel_mask &= valid_mask

    return channel_mask


# ---------------------------------------------------------
# Candidate footprint validation
# ---------------------------------------------------------

def candidate_footprint_in_buildable_mask(
    row: int,
    col: int,
    pond_radius_m: float,
    resolution_m: float,
    buildable_mask: np.ndarray,
) -> bool:
    """
    Verify that the full circular footprint of a proposed pond is
    contained inside the buildable (allowed) mask.

    This is stricter than testing only the candidate centre point:
    it guarantees the candidate does not overlap a mapped river/road/
    building/water feature nor any DEM-derived drainage channel.

    Parameters
    ----------
    row, col : int
        Candidate grid coordinates.
    pond_radius_m : float
        Proposed pond radius (metres).
    resolution_m : float
        DEM grid resolution (metres per cell).
    buildable_mask : np.ndarray
        The single authoritative allowed-land mask (valid DEM AND OSM
        free land AND hydrology-safe).

    Returns
    -------
    bool
        True only if the entire circular footprint is buildable.
    """
    rows, cols = buildable_mask.shape
    radius_cells = max(1, int(np.ceil(pond_radius_m / resolution_m)))

    r0 = max(0, row - radius_cells)
    r1 = min(rows, row + radius_cells + 1)
    c0 = max(0, col - radius_cells)
    c1 = min(cols, col + radius_cells + 1)

    rr, cc = np.ogrid[r0:r1, c0:c1]
    distance_m = np.sqrt((rr - row) ** 2 + (cc - col) ** 2) * resolution_m

    in_disk = distance_m <= pond_radius_m
    window = buildable_mask[r0:r1, c0:c1]

    # All cells inside the pond disk must be buildable.
    return bool(np.all(window[in_disk]))


# ---------------------------------------------------------
# Hydrology factor: per-candidate catchment + drainage proximity
# ---------------------------------------------------------

DRAINAGE_CHANNEL_PERCENTILE = 95.0


def build_channel_distance_grid(
    accumulation: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
    percentile: float = DRAINAGE_CHANNEL_PERCENTILE,
) -> np.ndarray | None:
    """
    Build a grid whose value at every cell is the distance (metres) to the
    nearest strong DEM-derived drainage channel (a cell whose flow
    accumulation is at or above the configured percentile of the non-zero
    distribution).

    Returns None when no drainage channel can be identified.
    """
    acc_nonzero = accumulation[
        valid_mask & np.isfinite(accumulation) & (accumulation > 0)
    ]
    if acc_nonzero.size == 0:
        return None

    threshold = float(np.nanpercentile(acc_nonzero, percentile))
    channel_mask = valid_mask & np.isfinite(accumulation) & (accumulation >= threshold)

    if not channel_mask.any():
        return None

    # distance_transform_edt measures distance to the nearest background
    # (0) cell. Passing ~channel_mask makes the channels the background,
    # so the result is the distance (in cells) to the nearest channel.
    distance_cells = distance_transform_edt(~channel_mask)
    return distance_cells * resolution_m


def hydrology_score(
    catchment_cell_count: int,
    resolution_m: float,
    channel_distance_m: float | None,
) -> float | None:
    """
    Score candidate hydrology from its own delineated catchment size and
    its proximity to strong drainage channels.

    Bigger catchments score higher; being at a safe distance from a
    drainage channel (water supply without sitting on the channel core)
    scores higher than being on top of it or far from any drainage.
    Returns None only if the catchment could not be derived.
    """
    if catchment_cell_count is None or catchment_cell_count <= 0:
        return None

    cell_area_m2 = resolution_m * resolution_m
    area_m2 = float(catchment_cell_count) * cell_area_m2
    area_ha = area_m2 / 10_000.0

    # Catchment size component (0-50): larger contributing area is better.
    size_component = min(50.0, 15.0 * math.log10(1.0 + area_ha))

    # Channel-proximity component (0-50): a moderate, safe distance from a
    # drainage channel provides water supply without the pond sitting on or
    # undercutting the channel core.
    if channel_distance_m is None:
        proximity = 20.0
    elif channel_distance_m < 50.0:
        proximity = 12.0 + channel_distance_m / 5.0
    elif channel_distance_m <= 250.0:
        proximity = 46.0 + (channel_distance_m - 50.0) / 40.0
    elif channel_distance_m <= 400.0:
        proximity = 50.0
    elif channel_distance_m <= 1200.0:
        proximity = 50.0 - (channel_distance_m - 400.0) / 80.0
    else:
        proximity = max(10.0, 40.0 - (channel_distance_m - 1200.0) / 200.0)

    return round(min(100.0, size_component + proximity), 2)


# ---------------------------------------------------------
# Water / storage factor: limited by the smaller of supply and capacity
# ---------------------------------------------------------

def water_storage_score(
    water_availability: dict | None,
    storage_estimation: dict | None,
) -> float | None:
    """
    Normalise the limiting volume min(runoff supply, storage capacity)
    onto a 0-100 score. If either quantity is unavailable the factor is
    dropped (returns None) rather than substituted with a fabricated value.
    """
    runoff = (water_availability or {}).get("estimated_annual_runoff_m3")
    storage = (storage_estimation or {}).get("estimated_storage_volume_m3")

    if not isinstance(runoff, (int, float)) or not isinstance(storage, (int, float)):
        return None

    limiting = min(float(runoff), float(storage))
    if limiting <= 0:
        return None

    if limiting >= 5000:
        score = 90.0
    elif limiting >= 3000:
        score = 82.0
    elif limiting >= 1500:
        score = 72.0
    elif limiting >= 800:
        score = 62.0
    elif limiting >= 400:
        score = 48.0
    elif limiting >= 200:
        score = 32.0
    else:
        score = 18.0

    return round(score, 1)


# ---------------------------------------------------------
# Accessibility: true nearest-road distance from OSM road geometry
# ---------------------------------------------------------

def nearest_road_distance_m(
    candidate_x: float,
    candidate_y: float,
    road_geometries: list,
) -> float | None:
    """
    Compute the distance (metres) from a candidate to the nearest OSM road,
    using shapely.ops.nearest_points against the raw road geometries already
    fetched for the hard-exclusion analysis.

    Returns None when no road geometry is available.
    """
    if not road_geometries:
        return None

    point = Point(candidate_x, candidate_y)
    best: float | None = None

    for road in road_geometries:
        if road.is_empty:
            continue
        try:
            nearest = nearest_points(point, road)
            d = nearest[0].distance(nearest[1])
        except Exception:
            # Invalid geometry: ignore this road rather than failing the
            # whole candidate.
            continue
        if best is None or d < best:
            best = d

    return best


# ---------------------------------------------------------
# Convert catchment raster mask into GeoJSON
# ---------------------------------------------------------

def _mask_to_geojson(
    mask,
    transform,
    to_wgs84,
):

    geometries = []

    data = mask.astype(
        np.uint8
    )

    for geometry, value in shapes(

        data,

        mask=mask,

        transform=transform,
    ):

        if value == 1:

            geometries.append(
                shape(
                    geometry
                )
            )

    if not geometries:
        return None

    merged = unary_union(
        geometries
    )

    wgs84_geometry = (
        shapely_transform(
            to_wgs84.transform,
            merged,
        )
    )

    return mapping(
        wgs84_geometry
    )


# ---------------------------------------------------------
# MAIN ANALYSIS
# ---------------------------------------------------------

def analyze_contour_file(

    file_bytes: bytes,

    filename: str,

    resolution_m: float = 10.0,

    max_candidates: int = 20,

    rainfall_years: int = 5,

    runoff_coefficient: float = 0.30,

    pond_radius_m: float = 40.0,

    max_pond_depth_m: float = 3.0,

    max_candidate_slope_percent: float = 8.0,

    min_candidate_spacing_m: float = 100.0,

    min_accumulation_percentile: float = 85.0,

    detailed_catchment_count: int = 5,

    max_cells: int | None = None,

    max_dimension: int | None = None,

    buffer_config: BufferConfig | None = None,

    hydrology_safety: HydrologySafetyConfig | None = None,

) -> dict:

    _t_start = time.perf_counter()
    _emit_timing("Starting contour analysis", 0.0)

    # =====================================================
    # STEP 1
    # Parse KML
    # =====================================================

    with timed_stage("KML parsing"):
        parsed = parse_contour_file(
            file_bytes,
            filename,
        )

    # =====================================================
    # STEP 2
    # Contours → DEM
    # =====================================================

    with timed_stage("DEM generation"):
        terrain = build_dem(
            parsed,
            resolution_m=
                resolution_m,
            max_cells=
                max_cells if max_cells is not None
                else 150_000,
            max_dimension=
                max_dimension if max_dimension is not None
                else 1_500,
        )

    original_dem = terrain.dem

    # =====================================================
    # STEP 3
    # Fill artificial DEM sinks
    # =====================================================

    with timed_stage("Sink filling"):
        filled_dem = (
            fill_sinks_priority_flood(
                original_dem,
                terrain.valid_mask,
            )
        )

    # =====================================================
    # STEP 4
    # Calculate slope
    # =====================================================

    with timed_stage("Slope calculation"):
        slope = (
            calculate_slope_percent(
                filled_dem,
                terrain.valid_mask,
                terrain.resolution_m,
            )
        )

    # =====================================================
    # STEP 5
    # D8 flow direction
    # =====================================================

    with timed_stage("Flow direction"):
        direction = (
            calculate_flow_direction(
                filled_dem,
                terrain.valid_mask,
                terrain.resolution_m,
            )
        )

    # =====================================================
    # STEP 6
    # Flow accumulation
    # =====================================================

    with timed_stage("Flow accumulation"):
        accumulation = (
            calculate_flow_accumulation(
                direction,
                terrain.valid_mask,
            )
        )

    # =====================================================
    # STEP 7
    # Convert analysis boundary to WGS84
    # =====================================================

    boundary_wgs84 = (
        shapely_transform(
            terrain.to_wgs84.transform,
            terrain.boundary_projected,
        )
    )

    # =====================================================
    # STEP 8
    # HARD EXCLUSION MASKS
    #
    # Build a single authoritative "buildable/allowed-land" mask:
    #
    #   buildable = valid DEM
    #               AND OSM free land (no water / waterway / road / building)
    #               AND hydrology-safe land (no DEM-derived drainage
    #                                         channel corridor)
    #
    # Hard exclusion is applied BEFORE any scoring so a candidate on a
    # river, water body, road or building can never reach the final
    # recommendations.
    # =====================================================

    try:
        land_filter = (
            build_osm_free_land_mask(
                terrain,
                boundary_wgs84,
                pond_radius_m=
                    pond_radius_m,
                safety_buffer_m=
                    10.0,
                buffer_config=
                    buffer_config,
            )
        )
    except RuntimeError as exc:
        # Fail safely: we must NOT pretend all DEM cells are buildable
        # when OSM exclusion data is unavailable.
        _emit_timing("Total (failed)", time.perf_counter() - _t_start)
        raise ValueError(str(exc)) from exc

    if buffer_config is None:
        buffer_config = DEFAULT_BUFFER_CONFIG

    if hydrology_safety is None:
        hydrology_safety = HydrologySafetyConfig()

    # DEM-derived drainage channels (streams, river corridors) are
    # treated as hard exclusions: OSM does not always contain them.
    with timed_stage("Hydrology safety mask"):
        hydrology_excluded_mask = (
            build_hydrology_safety_mask(
                accumulation,
                terrain.valid_mask,
                terrain.resolution_m,
                hydrology_safety,
            )
        )

    # ---- Authoritative buildable mask ---------------------
    buildable_mask = (
        land_filter.free_land_mask
        & ~hydrology_excluded_mask
    )

    if not buildable_mask.any():
        raise ValueError(
            "Hard land/hydrology constraints removed the entire "
            "analysis area. No buildable pond location remains."
        )

    # =====================================================
    # STEP 9
    # Find pond candidates ONLY within the authoritative
    # buildable mask
    # =====================================================

    with timed_stage("Candidate selection"):
        candidate_cells = (
            find_pond_candidates(
                filled_dem,
                slope,
                accumulation,
                buildable_mask,
                terrain.resolution_m,
                max_candidates=
                    max_candidates,
                max_slope_percent=
                    max_candidate_slope_percent,
                min_candidate_spacing_m=
                    min_candidate_spacing_m,
                min_accumulation_percentile=
                    min_accumulation_percentile,
            )
        )

    if not candidate_cells:
        raise ValueError(
            "No pond candidates remain after "
            "excluding mapped rivers/water bodies, "
            "DEM-derived drainage channels, "
            "roads and buildings."
        )

    initial_candidate_count = len(candidate_cells)

    # =====================================================
    # STEP 9.5
    # CANDIDATE FOOTPRINT VALIDATION
    #
    # A candidate is a real pond footprint, not a point. Before it is
    # accepted, verify that the whole proposed footprint is inside the
    # buildable mask (not merely its centre point).
    # =====================================================

    before_footprint = len(candidate_cells)
    footprint_valid_candidates = []
    for c in candidate_cells:
        if candidate_footprint_in_buildable_mask(
            c.row,
            c.col,
            pond_radius_m,
            terrain.resolution_m,
            buildable_mask,
        ):
            footprint_valid_candidates.append(c)

    rejected_by_footprint = before_footprint - len(footprint_valid_candidates)

    if not footprint_valid_candidates:
        raise ValueError(
            "All candidate pond footprints overlap a hard-excluded "
            "feature (river, water body, road, building or drainage "
            "channel) after applying safety buffers. No valid location "
            "remains; try a smaller pond radius or a different area."
        )

    candidate_cells = footprint_valid_candidates
    del footprint_valid_candidates, before_footprint

    # =====================================================
    # STEP 10
    # Rainfall
    # =====================================================

    rain_longitude = (
        boundary_wgs84.centroid.x
    )

    rain_latitude = (
        boundary_wgs84.centroid.y
    )

    with timed_stage("Rainfall"):
        rainfall = (
            get_historical_rainfall(

                rain_latitude,

                rain_longitude,

                rainfall_years,
            )
        )

    average_rainfall_mm = (
        rainfall.get(
            "average_annual_rainfall_mm"
        )
    )

    # Distance (metres) of every DEM cell to the nearest strong DEM-derived
    # drainage channel, used by the per-candidate hydrology factor.
    channel_distance_grid = (
        build_channel_distance_grid(
            accumulation,
            terrain.valid_mask,
            terrain.resolution_m,
        )
    )

    # =====================================================
    # STEP 10.5
    # Regional climate / rainfall analysis (computed once at the
    # analysis-area centroid; it is a regional data source and is not
    # recomputed per candidate).
    # =====================================================

    region_lat = boundary_wgs84.centroid.y
    region_lon = boundary_wgs84.centroid.x

    with timed_stage("Climate analysis"):
        climate_analysis = get_climate_analysis(
            region_lat,
            region_lon,
            rainfall_years,
        )

    # =====================================================
    # STEP 10.6
    # Beneficiary proximity data (computed once at the analysis-area
    # centroid, not per candidate). This is a separate semantic query from
    # the hard-exclusion building pass: residences/settlements are used for
    # proximity counting, never for exclusion.
    # =====================================================

    region_lat = boundary_wgs84.centroid.y
    region_lon = boundary_wgs84.centroid.x

    with timed_stage("Beneficiary proximity"):
        beneficiary_data = fetch_beneficiary_data(
            region_lat,
            region_lon,
        )

    # =====================================================
    # STEP 11
    # Calculate catchment + water + multi-factor information
    # for every candidate
    # =====================================================

    candidates = []

    recommended_catchment_geojson = None

    # =====================================================
    # STEP 11.5
    # Pre-fetch the network-bound per-candidate analyses in parallel.
    #
    # get_soil_data and get_landuse_analysis each make a blocking remote
    # HTTP request. Running them serially inside the candidate loop costs
    # ~2 network round-trips per candidate and dominates wall-clock. They
    # are fully independent across candidates, so we batch them with a
    # thread pool: the OSMSafe/land-use servers are the slow part and
    # overlap cleanly. Results are keyed by grid cell so the loop below
    # just reads the prepared lookup.
    # =====================================================

    candidate_geos = {}
    for cell in candidate_cells:
        _cx = float(terrain.xs[cell.col])
        _cy = float(terrain.ys[cell.row])
        _lon, _lat = terrain.to_wgs84.transform(_cx, _cy)
        candidate_geos[(cell.row, cell.col)] = (_lat, _lon, _cx, _cy)

    # Resolve shared module-level helpers so the pool workers reference them
    # by name (threads share the interpreter; this is for clarity only).
    _soil_fn = get_soil_data
    _landuse_fn = get_landuse_analysis
    _road_fn = nearest_road_distance_m
    _clearance_fn = compute_exclusion_clearance
    _road_geoms = land_filter.road_geometries
    _raw_geoms = land_filter.raw_geometries_by_category

    def _prefetch(cell_key):
        lat, lon, x, y = candidate_geos[cell_key]
        soil = _soil_fn(lat, lon)
        landuse = _landuse_fn(lat, lon, radius_m=1500.0)
        road = _road_fn(x, y, _road_geoms)
        clearance = _clearance_fn(
            candidate_point=Point(x, y),
            raw_geometries_by_category=_raw_geoms,
            pond_radius_m=pond_radius_m,
            safety_buffer_m=10.0,
            buffer_config=buffer_config,
        )
        return soil, landuse, road, clearance

    # A bounded thread pool that issues the remote soil + land-use requests
    # and the CPU-heavy per-candidate geometry computations concurrently
    # across all candidate cells. Results are combined back into per-cell
    # lookups before the main loop starts.
    from concurrent.futures import ThreadPoolExecutor

    soil_lookup: dict = {}
    landuse_lookup: dict = {}
    road_lookup: dict = {}
    clearance_lookup: dict = {}

    keys = list(candidate_geos.keys())
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(keys)))) as ex:
        results = ex.map(_prefetch, keys)
        for cell_key, (soil_res, landuse_res, road_res, clearance_res) in zip(
            keys, results
        ):
            soil_lookup[cell_key] = soil_res
            landuse_lookup[cell_key] = landuse_res
            road_lookup[cell_key] = road_res
            clearance_lookup[cell_key] = clearance_res

    with timed_stage("Candidate evaluation"):

        for rank, cell in enumerate(
                candidate_cells,
                start=1,
            ):
    
            row = cell.row
            column = cell.col

            # -------------------------------------------------
            # FINAL HARD VALIDATION
            #
            # Belt-and-braces: a candidate must not be inside DEM
            # exclusion, inside buildable (OSM) land, or inside the
            # hydrology safety mask. This re-checks the full pond
            # footprint (not just the centre) so that no candidate
            # with a hard-exclusion violation is ever reported, even
            # if it received a high score.
            # -------------------------------------------------

            if (
                not buildable_mask[row, column]
                or not candidate_footprint_in_buildable_mask(
                    row,
                    column,
                    pond_radius_m,
                    terrain.resolution_m,
                    buildable_mask,
                )
            ):
                raise ValueError(
                    "A candidate failed the final hard-exclusion "
                    "validation (overlaps mapped river/water/road/"
                    "building or a DEM-derived drainage channel). "
                    "This location cannot be recommended."
                )

            # -------------------------------------------------
            # Catchment
            #
            # Delineate the full D8 catchment for EVERY candidate so the
            # cascade/water metrics are per-candidate and exact. The
            # detailed mask is also what feeds the hydrology factor, so we
            # no longer shortcut it to a handful of top-ranked cells.
            #
            # The mask array is kept only for the top-ranked candidate,
            # which is turned into the recommended catchment GeoJSON; the
            # remaining masks are freed immediately after counting to limit
            # peak memory.
            # -------------------------------------------------

            catchment_mask = (
                delineate_catchment(
                    direction,
                    terrain.valid_mask,
                    row,
                    column,
                )
            )

            cell_count = int(
                catchment_mask.sum()
            )

            # NOTE: the catchment_mask is deliberately kept alive here. It is
            # needed again for the per-candidate siltation-risk computation
            # later in the loop (after the land-use analysis is available).
            # It is freed explicitly once that metric is computed.

            area_m2 = float(
                cell_count
                * terrain.resolution_m
                * terrain.resolution_m
            )

            # -------------------------------------------------
            # Grid → longitude / latitude
            # -------------------------------------------------
    
            x = float(
                terrain.xs[column]
            )
    
            y = float(
                terrain.ys[row]
            )
    
            longitude, latitude = (
                terrain.to_wgs84.transform(
                    x,
                    y,
                )
            )
    
            # -------------------------------------------------
            # Water storage + runoff
            #
            # IMPORTANT:
            # Use free land mask while estimating pond
            # footprint.
            # -------------------------------------------------
    
            water = (
                estimate_candidate_water_metrics(
    
                    row,
    
                    column,
    
                    filled_dem,
    
                    slope,
    
                    land_filter.free_land_mask,
    
                    terrain.resolution_m,
    
                    area_m2,
    
                    average_rainfall_mm,
    
                    runoff_coefficient=
                        runoff_coefficient,
    
                    pond_radius_m=
                        pond_radius_m,
    
                    max_pond_depth_m=
                        max_pond_depth_m,
                )
            )

            candidate_id = rank

            # -------------------------------------------------
            # Multi-factor analysis is computed per candidate so soil,
            # land-use, water availability, storage and accessibility are
            # evaluated at the actual candidate location rather than once
            # at a regional centroid. Road distance comes from the OSM road
            # geometry already fetched for the hard-exclusion pass.
            # -------------------------------------------------

            # Per-candidate soil texture (pre-fetched in parallel in STEP 11.5).
            soil_analysis_cand = soil_lookup[(row, column)]

            # Per-candidate land use / land cover (pre-fetched in parallel).
            land_use_analysis_cand = landuse_lookup[(row, column)]

            # Real nearest-road distance from OSM road geometry (metres) -- precomputed
            # in parallel in STEP 11.5 using the candidate's projected coords.
            road_distance_m = road_lookup[(row, column)]

            accessibility = get_accessibility_analysis(
                nearest_road_distance_m=road_distance_m,
                latitude=latitude,
                longitude=longitude,
            )

            # Water availability / runoff supply
            water_avail = estimate_water_availability(
                catchment_area_m2=area_m2,
                annual_rainfall_mm=average_rainfall_mm,
                runoff_coefficient=runoff_coefficient,
                land_cover_type=land_use_analysis_cand.get("dominant_land_use"),
                soil_permeability=soil_analysis_cand.get("permeability"),
            )

            # Storage estimation
            storage = estimate_storage(
                surface_area_m2=water.get("pond_area_m2"),
                max_depth_m=max_pond_depth_m,
            )

            # Hydrology factor: own catchment size + drainage proximity.
            candidate_channel_distance_m = None
            if channel_distance_grid is not None:
                candidate_channel_distance_m = float(
                    channel_distance_grid[row, column]
                )
            hydrology_score_val = hydrology_score(
                cell_count,
                terrain.resolution_m,
                candidate_channel_distance_m,
            )

            # Water/storage factor: limited by the smaller of supply and
            # capacity.
            water_score_val = water_storage_score(water_avail, storage)

            # Soft environmental constraints
            constraints = evaluate_constraints(
                land_filter_result=land_filter.__dict__,
                land_use_restrictions=land_use_analysis_cand.get("restrictions"),
            )

            # -------------------------------------------------
            # Benfeficiary proximity metric (per candidate).
            #
            # Uses the beneficiary data fetched once at the region centroid
            # and computes the proximity of THIS candidate's location to
            # settlements / residential buildings. The 0-1 score is derived
            # via a distance decay (see beneficiary_service.MAX_USEFUL_DISTANCE_M).
            # -------------------------------------------------

            beneficiary_metrics_cand = beneficiary_metrics(
                candidate_lat=latitude,
                candidate_lon=longitude,
                beneficiary_data=beneficiary_data,
            )
            beneficiary_score_val = beneficiary_metrics_cand.get("beneficiary_score")

            # -------------------------------------------------
            # Catchment siltation risk (reported, not ranked).
            #
            # Reuses the candidate's own catchment raster (already
            # delineated above) and the per-candidate land-use data. The
            # metric classifies the catchment's land cover into vegetated /
            # farmland / bare buckets and produces a maintenance-outlook
            # score. It is exposed alongside the ranking, NOT folded into
            # TOPSIS as a sixth weighted factor.
            # -------------------------------------------------

            catchment_landcover = compute_siltation_risk(
                catchment_mask=catchment_mask,
                land_use_metrics=[land_use_analysis_cand],
            )

            # Identify downstream conflict by walking the D8 grid forward
            # from the candidate outlet and checking for settlements / water
            # bodies / farmland watercourses along the path. This is a flag
            # for human review and never reranks the candidate.
            downstream_conflict = trace_downstream_conflict(
                outlet_row=row,
                outlet_col=column,
                direction=direction,
                valid_mask=terrain.valid_mask,
                resolution_m=terrain.resolution_m,
                beneficiary_data=beneficiary_data,
                land_use_metrics=[land_use_analysis_cand],
                water_geometries=land_filter.raw_geometries_by_category.get("water"),
                cell_to_wgs84=(
                    lambda r, c: terrain.to_wgs84.transform(
                        float(terrain.xs[c]),
                        float(terrain.ys[r]),
                    )
                ),
                cell_to_projected=(
                    lambda r, c: (float(terrain.xs[c]), float(terrain.ys[r]))
                ),
            )

            # Only the top-ranked candidate's catchment is turned into the
            # recommended GeoJSON to avoid filling the map with many polygons.
            # Capture it here while ``catchment_mask`` is still live.
            if rank == 1 and catchment_mask is not None:
                recommended_catchment_geojson = _mask_to_geojson(
                    catchment_mask,
                    terrain.transform,
                    terrain.to_wgs84,
                )

            # The catchment mask is no longer needed after the siltation
            # metric is computed; free it to bound peak memory.
            del catchment_mask

            # Exclusion-clearance for this candidate -- precomputed in parallel in
            # STEP 11.5 against the raw (un-buffered) OSM geometries already
            # fetched for the hard-exclusion mask, using the same buffer
            # constants. No second Overpass call and no new numbers here.
            exclusion_clearance = clearance_lookup[(row, column)]

            # -------------------------------------------------
            # Six raw factor scores (0-100) for the TOPSIS decision
            # matrix. Per-candidate and fully independent:
            #   terrain       - DEM slope/elevation/relief suitability.
            #   hydrology     - own catchment size + drainage proximity.
            #   water_storage - min(runoff supply, storage capacity).
            #   practical     - soil, land use and road accessibility.
            #   beneficiary   - proximity to settlements/buildings.
            # -------------------------------------------------

            practical_raw = practical_raw_score(
                soil_analysis_cand,
                land_use_analysis_cand,
                accessibility,
            )

            factor_scores = {
                "terrain": cell.score,
                "hydrology": hydrology_score_val,
                "water_storage": water_score_val,
                "practical": practical_raw,
                "beneficiary": beneficiary_score_val,
            }
            candidates.append(
                {
                    "candidate_id":
                        candidate_id,

                    "rank":
                        rank,

                    "latitude":
                        float(latitude),

                    "longitude":
                        float(longitude),

                    "elevation_m":
                        round(
                            float(
                                filled_dem[
                                    row,
                                    column,
                                ]
                            ),
                            3,
                        ),

                    "slope_percent":
                        round(
                            float(
                                slope[
                                    row,
                                    column,
                                ]
                            ),
                            3,
                        ),

                    "flow_accumulation_cells":
                        int(
                            round(
                                float(
                                    accumulation[
                                        row,
                                        column,
                                    ]
                                )
                            )
                        ),

                    "suitability_score":
                        round(
                            float(
                                cell.score
                            ),
                            2,
                        ),

                    "catchment": {

                        "cell_count":
                            cell_count,

                        "area_m2":
                            round(
                                area_m2,
                                2,
                            ),

                        "area_hectares":
                            round(
                                area_m2
                                / 10_000.0,
                                4,
                            ),
                    },

                    "water":
                        water,

                    "land_status": (
                        "Feature-clear according to "
                        "OpenStreetMap: candidate footprint "
                        "does not overlap mapped water/"
                        "waterways, roads or buildings. "
                        "Legal ownership still requires "
                        "official verification."
                    ),

                    # Per-candidate multi-factor fields
                    "soil_analysis":
                        soil_analysis_cand,

                    "climate_analysis":
                        climate_analysis,

                    "water_availability":
                        water_avail,

                    "land_use_analysis":
                        land_use_analysis_cand,

                    "accessibility_analysis":
                        accessibility,

                    "environmental_constraints":
                        constraints,

                    "storage_estimation":
                        storage,

                    "hydrology_score":
                        hydrology_score_val,

                    "water_storage_score":
                        water_score_val,

                    "beneficiary_metrics":
                        beneficiary_metrics_cand,

                    "beneficiary_score":
                        beneficiary_score_val,

                    "catchment_landcover":
                        catchment_landcover,

                    "downstream_conflict":
                        downstream_conflict,

                    "exclusion_clearance":
                        exclusion_clearance,

                    "factor_scores":
                        factor_scores,
                }
            )

    # =====================================================
    # STEP 12.5
    # Preserve original terrain rank, then rank all survivors by TOPSIS
    # closeness to the ideal solution. rank_candidates_topsis builds the
    # weighted decision matrix, computes S_plus / S_minus / C_i, reorders
    # the list by descending closeness and fills in the ranking fields.
    # =====================================================
    for cand in candidates:
        cand["initial_rank"] = cand.get("rank", 0)

    with timed_stage("TOPSIS ranking"):
        candidates = rank_candidates_topsis(candidates)

    # Re-assign final_rank (1-based) on the TOPSIS-ordered list and keep the
    # multi_factor_score reference in sync for the frontend.
    for new_rank, cand in enumerate(candidates, start=1):
        cand["final_rank"] = new_rank
        cand["rank"] = new_rank
        if cand.get("multi_factor_score"):
            cand["multi_factor_score"]["final_rank"] = new_rank

    # =====================================================
    # STEP 12.6
    # Spatially diversify the final top-N.
    #
    # The TOPSIS ranking is ~100 m peak spacing, which can leave the top few
    # results clustered in one small area. We keep the rank order but select
    # a more separated subset: walk the ranked list and keep a candidate only
    # if it is more than MIN_FINAL_SEPARATION_M from every already-selected
    # one. The skipped candidates stay visible under ``nearby_alternatives``
    # rather than being dropped.
    # =====================================================
    with timed_stage("Spatial diversification"):
        top_n = max(1, int(max_candidates))
        selected, nearby_alternatives, remaining, diversified_skipped = (
            diversify_top_n(
                candidates,
                top_n=top_n,
            )
        )

    # =====================================================
    # STEP 12.7
    # Site justification ("the proof") for EVERY candidate in the final
    # top-N.
    #
    # A structured, audit-friendly object explaining why each candidate
    # received its final score. Every candidate (rank i) is compared
    # factor-by-factor against the NEXT-ranked candidate (rank i+1), showing
    # the full chain of proofs: why #1 beats #2, why #2 beats #3, etc. The
    # last-ranked candidate in the returned set has no "next" and its
    # comparison field is left null. No natural-language paragraph is
    # generated here: every number traces back to a real calculation.
    # =====================================================

    def _confidence_for(data_result, fallback="unavailable"):
        """Map an analysis result's availability/confidence to a label."""
        if not data_result or data_result.get("available") is not True:
            return fallback
        conf = data_result.get("confidence")
        if conf in ("high", "medium", "low"):
            return conf
        return "high"

    def _build_chain_margin(this_cf, next_cf):
        """Per-factor weighted-contribution (v_ij) deltas between a candidate
        (rank i) and the next-ranked candidate (rank i+1). These are the exact
        values TOPSIS ranks on, so the margin shows why rank i beat rank i+1."""
        next_by_factor = {f["factor"]: f for f in next_cf}
        margins = []
        for f in this_cf:
            other = next_by_factor.get(f["factor"])
            if (
                other is None
                or f.get("contribution") is None
                or other.get("contribution") is None
            ):
                continue
            delta = round(float(f["contribution"]) - float(other["contribution"]), 4)
            if delta > 0:
                favors = "this_candidate"
            elif delta < 0:
                favors = "next_rank"
            else:
                favors = "tie"
            margins.append({
                "factor": f["factor"],
                "delta": delta,
                "favors": favors,
            })
        return margins

    for idx, cand in enumerate(selected):
        mf = cand.get("multi_factor_score") or {}
        consolidated = mf.get("consolidated_factors", [])

        env = cand.get("environmental_constraints") or {}
        hard_passed = env.get("hard_constraints") or []

        # Every candidate except the last has a "next_rank" to compare
        # against (rank i vs rank i+1). The last has none.
        if idx + 1 < len(selected):
            next_cand = selected[idx + 1]
            next_cf = (
                (next_cand.get("multi_factor_score") or {})
                .get("consolidated_factors", [])
            )
            margin = _build_chain_margin(consolidated, next_cf) if next_cf else None
        else:
            margin = None

        # Destructure helper for clarity.
        b_metrics = cand.get("beneficiary_metrics") or {}

        justification = {
            "candidate_id": cand.get("candidate_id"),
            "final_score": mf.get("final_score"),
            "factor_breakdown": consolidated,
            "hard_constraints_passed": hard_passed,
            "margin_vs_next_rank": margin,
            "exclusion_clearance": cand.get("exclusion_clearance") or [],
            "catchment_landcover": cand.get("catchment_landcover") or {},
            "downstream_conflict": cand.get("downstream_conflict") or {},
            "beneficiary": {
                "nearest_settlement_distance_m": (
                    b_metrics.get("nearest_settlement_distance_m")
                ),
                "buildings_within_1km": b_metrics.get("buildings_within_1km"),
                "beneficiary_score": b_metrics.get("beneficiary_score"),
                "basis": b_metrics.get("basis"),
            },
            "data_confidence": {
                "soil": _confidence_for(cand.get("soil_analysis")),
                "rainfall": _confidence_for(
                    cand.get("climate_analysis") or climate_analysis
                ),
                "land_use": _confidence_for(cand.get("land_use_analysis")),
            },
            "topsis": {
                "distance_to_ideal_best": mf.get("S_plus"),
                "distance_to_ideal_worst": mf.get("S_minus"),
                "closeness_coefficient": mf.get("C_i"),
            },
        }
        cand["site_justification"] = justification

    # =====================================================
    # STEP 12.8
    # Top-level summary (from the recommended candidate) and overall
    # data-confidence assessment.
    # =====================================================

    if selected:
        soil_analysis = selected[0].get("soil_analysis")
        land_use_analysis = selected[0].get("land_use_analysis")
    else:
        soil_analysis = None
        land_use_analysis = None

    data_confidence = assess_confidence(
        dem_available=True,
        hydrology_available=True,
        rainfall_available=bool(climate_analysis.get("available")),
        soil_available=bool((soil_analysis or {}).get("available")),
        land_use_available=bool((land_use_analysis or {}).get("available")),
        osm_available=bool(land_filter.osm_data_available),
        storage_available=any(
            (c.get("storage_estimation") or {}).get("available")
            for c in selected
        ),
    )

    # =====================================================
    # STEP 13
    # Terrain summary
    # =====================================================

    elevations = sorted(
        {
            contour.elevation_m
            for contour
            in parsed.contours
        }
    )

    # =====================================================
    # STEP 13
    # Terrain summary
    # =====================================================

    elevations = sorted(
        {
            contour.elevation_m
            for contour
            in parsed.contours
        }
    )

    interval = min(
        (
            next_elevation
            - elevation

            for elevation,
            next_elevation

            in zip(
                elevations,
                elevations[1:],
            )

            if next_elevation
            > elevation
        ),

        default=None,
    )

    # =====================================================
    # FINAL JSON RESPONSE
    # =====================================================

    _emit_timing("Total", time.perf_counter() - _t_start)

    _result = {

        "status":
            "success",

        "filename":
            filename,

        # -------------------------------------------------
        # Terrain
        # -------------------------------------------------

        "terrain": {

            "contour_line_count":
                len(
                    parsed.contours
                ),

            "minimum_elevation_m":
                float(
                    np.nanmin(
                        original_dem
                    )
                ),

            "maximum_elevation_m":
                float(
                    np.nanmax(
                        original_dem
                    )
                ),

            "contour_interval_m":
                (
                    float(interval)
                    if interval is not None
                    else None
                ),

            "grid_resolution_m":
                round(
                    float(
                        terrain.resolution_m
                    ),
                    3,
                ),

            "grid_rows":
                int(
                    original_dem.shape[0]
                ),

            "grid_columns":
                int(
                    original_dem.shape[1]
                ),
        },

        # -------------------------------------------------
        # Rainfall
        # -------------------------------------------------

        "rainfall":
            rainfall,

        # -------------------------------------------------
        # Land filter
        # -------------------------------------------------

        "land_filter": {

            "available":
                True,

            "source":
                land_filter.source,

            "excluded_feature_counts":
                land_filter.feature_counts,

            "excluded_cell_count":
                land_filter.excluded_cell_count,

            "free_cell_count":
                land_filter.free_cell_count,

            # Per-category OSM-excluded cell counts (diagnostics)
            "excluded_water_cells":
                land_filter.excluded_water_cells,

            "excluded_waterway_cells":
                land_filter.excluded_waterway_cells,

            "excluded_road_cells":
                land_filter.excluded_road_cells,

            "excluded_building_cells":
                land_filter.excluded_building_cells,

            # DEM-derived hydrology / total buildable diagnostics
            "hydrology_excluded_cell_count":
                int(hydrology_excluded_mask.sum()),

            "buildable_cell_count":
                int(buildable_mask.sum()),

            "total_dem_cells":
                int(terrain.valid_mask.sum()),

            "notes":
                land_filter.notes,
        },

        # Multi-factor environmental analyses
        "soil_analysis": soil_analysis,

        "climate_analysis": climate_analysis,

        "land_use_analysis": land_use_analysis,

        "data_confidence": data_confidence,

        # -------------------------------------------------
        # Candidates
        # -------------------------------------------------

        # The full TOPSIS-ranked list (all survivors).
        "candidates":
            candidates,

        # The spatially-diversified final top-N (what is actually
        # recommended to the frontend).
        "selected_candidates":
            selected,

        # High-ranked candidates set aside because they sit within the
        # separation radius of a selected candidate - kept visible rather
        # than silently dropped.
        "nearby_alternatives":
            nearby_alternatives,

        # Count of candidates skipped by the diversification pass.
        "diversified_skipped":
            diversified_skipped,

        "recommended_candidate_id":
            (
                selected[0]["candidate_id"]
                if selected
                else (candidates[0]["candidate_id"] if candidates else 1)
            ),

        "recommended_catchment_geojson":
            recommended_catchment_geojson,

        "boundary_geojson":
            mapping(
                boundary_wgs84
            ),

        # -------------------------------------------------
        # Candidate configuration
        # -------------------------------------------------

        "candidate_generation": {

            "returned_candidates":
                len(
                    candidates
                ),

            "max_candidates_requested":
                int(
                    max_candidates
                ),

            "minimum_spacing_m":
                float(
                    min_candidate_spacing_m
                ),

            "maximum_slope_percent":
                float(
                    max_candidate_slope_percent
                ),

            "minimum_accumulation_percentile":
                float(
                    min_accumulation_percentile
                ),

            # Candidate pipeline diagnostics
            "initial_candidates":
                initial_candidate_count,

            "rejected_by_hard_footprint":
                rejected_by_footprint,

            "valid_after_footprint_validation":
                len(candidate_cells),

            "final_scored_candidates":
                len(candidates),
        },

        # -------------------------------------------------
        # Explain methodology in API
        # -------------------------------------------------

        "method": {

            "dem":
                (
                    "Contour interpolation "
                    "(linear with nearest-edge fill)"
                ),

            "sink_handling":
                (
                    "Priority-Flood "
                    "depression filling"
                ),

            "flow_direction":
                (
                    "D8 steepest-descent"
                ),

            "flow_accumulation":
                (
                    "Upstream contributing-cell count"
                ),

            "candidate_generation":
                (
                    "Local maxima of hydrological "
                    "suitability restricted to a single "
                    "authoritative buildable mask "
                    "(valid DEM AND OSM feature-clear "
                    "land AND DEM-derived drainage-safe "
                    "land), with configurable minimum "
                    "spacing. Hard exclusions are applied "
                    "before any scoring."
                ),

            "land_filter":
                (
                    "OpenStreetMap/Overpass water, waterway, "
                    "road and building features are treated "
                    "as HARD exclusions and buffered by the "
                    "proposed pond footprint radius before "
                    "candidate selection."
                ),

            "hydrology_safety":
                (
                    "DEM-derived flow-accumulation channels "
                    "are treated as hard exclusions with a "
                    "configurable drainage percentile and "
                    "safety buffer, so unmapped streams are "
                    "also avoided."
                ),

            "footprint_validation":
                (
                    "Every candidate's full circular pond "
                    "footprint is required to be entirely "
                    "inside the buildable mask before it is "
                    "accepted, with a final hard-validation "
                    "pass before reporting recommendations."
                ),

            "catchment":
                (
                    "Reverse traversal of D8 drainage "
                    "graph from each candidate outlet"
                ),

            "rainfall":
                (
                    "Historical daily precipitation "
                    "aggregated to annual totals using "
                    "Open-Meteo at analysis-area centroid"
                ),

            "runoff":
                (
                    "Average annual rainfall × "
                    "catchment area × runoff coefficient"
                ),

            "storage":
                (
                    "Connected gentle local footprint × "
                    "recommended excavation depth × "
                    "geometric shape factor"
                ),

            "soil":
                (
                    "ISRIC SoilGrids 2.0 at ~250 m resolution. "
                    "Score based on clay/sand/silt fractions."
                ),

            "climate":
                (
                    "Open-Meteo historical daily precipitation. "
                    "Seasonality and reliability derived from annual series."
                ),

            "land_use":
                (
                    "OpenStreetMap landuse/natural tags via Overpass. "
                    "Dominant land-use class drives suitability score."
                ),

            "accessibility":
                (
                    "OSM highway proximity. "
                    "Scores penalised for very remote or too-close placement."
                ),

            "multi_factor_scoring":
                (
                    "Weighted average of available factors. "
                    "Unavailable factors are removed and weights redistributed proportionally."
                ),
        },
        # -------------------------------------------------

        "limitations": [

            (
                "Candidate sites are hydrological "
                "planning candidates, not final "
                "construction approvals."
            ),

            (
                "Mapped rivers/water bodies, roads "
                "and buildings are automatically "
                "excluded using OpenStreetMap; "
                "unmapped features can still exist."
            ),

            (
                "Feature-clear land is not proof "
                "of government ownership or legal "
                "availability; official land records "
                "and field inspection are still required."
            ),

            (
                "Storage capacity is a planning-level "
                "geometric estimate and requires field "
                "survey, soil/geotechnical checks and "
                "civil-engineering design."
            ),

            (
                "Runoff volume depends on the selected "
                "runoff coefficient and should be "
                "refined with local land-cover and "
                "soil data."
            ),

            (
                "Multi-factor scores are planning-level. "
                "If a factor is unavailable (e.g. soil, climate, land-use), "
                "its weight is redistributed among available factors."
            ),

            (
                "Soil texture is from SoilGrids at ~250 m resolution; "
                "site-specific geotechnical investigation is required."
            ),

            (
                "Climate suitability uses long-term historical rainfall, "
                "not current weather. Year-to-year variability remains."
            ),

            (
                "Land-use classification relies on OpenStreetMap tags "
                "which may be sparse in rural areas."
            ),

            (
                "Final pond construction requires civil-engineering design, "
                "geotechnical investigation and statutory approval."
            ),
        ],
    }

    return _result
