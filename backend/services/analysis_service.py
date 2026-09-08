from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from rasterio.features import shapes

from scipy.ndimage import binary_dilation

from shapely.geometry import (
    mapping,
    shape,
)

from shapely.ops import (
    transform as shapely_transform,
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
    score_candidate,
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

    Attributes
    ----------
    enabled : bool
        Whether to apply the DEM-derived hydrology safety mask.
    accumulation_percentile : float
        Percentile (0-100) of non-zero flow accumulation used as the
        channel threshold. Cells at or above this percentile are
        treated as drainage channels and excluded.
    buffer_m : float
        Extra radius (metres) applied around detected channels.
    """

    enabled: bool = True
    accumulation_percentile: float = 95.0
    buffer_m: float = 10.0


def build_hydrology_safety_mask(
    accumulation: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
    config: HydrologySafetyConfig | None = None,
) -> np.ndarray:
    """
    Build a boolean mask of DEM-derived drainage channels (streams,
    river corridors) to be treated as HARD exclusions.

    A cell contributes to a channel when its flow accumulation is at
    or above a configurable percentile of the non-zero accumulation
    distribution, dilated by an optional buffer / pond footprint so
    the entire proposed pond stays out of the channel corridor.
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

    # =====================================================
    # STEP 11
    # Calculate catchment + water information for
    # every candidate
    # =====================================================

    candidates = []

    recommended_catchment_geojson = None

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
            # IMPORTANT (Render-safety optimisation):
            # Full BFS catchment raster delineation is expensive
            # and duplicates work already captured by the D8 flow
            # accumulation grid. A cell's ``accumulation`` value is
            # exactly the number of upstream cells draining to it,
            # i.e. its catchment cell count.
            #
            # We therefore run the detailed mask-based delineation
            # for only the highest-ranked ``detailed_catchment_count``
            # candidates (used to build the recommended catchment
            # geometry) and derive the catchment cell count / area
            # from flow accumulation for the remainder. Candidate
            # ranking, scoring and the per-candidate ``catchment``
            # summary structure are completely unchanged.
            # -------------------------------------------------
    
            if rank <= max(1, min(int(detailed_catchment_count), len(candidate_cells))):
    
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

                # Keep the array only for the top-ranked candidate which is
                # turned into the recommended catchment GeoJSON; free detailed
                # masks immediately after counting to limit peak memory.
                if rank != 1:
                    del catchment_mask
            else:
    
                # Cheap source: upstream contributing-cell count.
                catchment_mask = None
    
                cell_count = int(
                    round(
                        float(
                            accumulation[
                                row,
                                column,
                            ]
                        )
                    )
                )
    
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
                }
            )
    
            # Only show top candidate catchment for now
            # to avoid filling entire map with many polygons.
    
            if rank == 1 and catchment_mask is not None:
    
                recommended_catchment_geojson = (
                    _mask_to_geojson(
    
                        catchment_mask,
    
                        terrain.transform,
    
                        terrain.to_wgs84,
                    )
                )

    with timed_stage("Multi-factor analysis"):
        # =====================================================
        # =====================================================
        # STEP 12
        # Multi-factor environmental analysis
        # =====================================================
    
        # Get centroid for regional data lookups
        region_lat = boundary_wgs84.centroid.y
        region_lon = boundary_wgs84.centroid.x
    
        # Soil analysis
        soil_analysis = get_soil_data(region_lat, region_lon)
    
        # Climate / rainfall analysis
        climate_analysis = get_climate_analysis(region_lat, region_lon, rainfall_years)
    
        # Land-use analysis around recommended candidate
        if candidates:
            best = candidates[0]
            land_use_analysis = get_landuse_analysis(
                best["latitude"],
                best["longitude"],
                radius_m=1500.0,
            )
        else:
            land_use_analysis = get_landuse_analysis(region_lat, region_lon, radius_m=1500.0)
    
        # Per-candidate multi-factor scoring
        mf_candidates = []
        for cand in candidates:
            catchment_area = cand.get("catchment", {}).get("area_m2", 0)
    
            # Water availability
            water_avail = estimate_water_availability(
                catchment_area_m2=catchment_area,
                annual_rainfall_mm=average_rainfall_mm,
                runoff_coefficient=runoff_coefficient,
                land_cover_type=land_use_analysis.get("dominant_land_use"),
                soil_permeability=soil_analysis.get("permeability"),
            )
    
            # Accessibility
            accessibility = get_accessibility_analysis(nearest_road_distance_m=None)
    
            # Storage estimation
            storage = estimate_storage(
                surface_area_m2=cand.get("water", {}).get("pond_area_m2"),
                max_depth_m=max_pond_depth_m,
            )
    
            # Constraints
            constraints = evaluate_constraints(
                land_filter_result=land_filter.__dict__,
                land_use_restrictions=land_use_analysis.get("restrictions"),
            )
    
            # Multi-factor score
            mf_score = score_candidate(
                candidate=cand,
                soil_analysis=soil_analysis,
                rainfall_analysis=climate_analysis,
                water_availability=water_avail,
                land_use_analysis=land_use_analysis,
                accessibility_analysis=accessibility,
            )
    
            mf_candidates.append({
                **cand,
                "soil_analysis": soil_analysis,
                "climate_analysis": climate_analysis,
                "water_availability": water_avail,
                "land_use_analysis": land_use_analysis,
                "accessibility_analysis": accessibility,
                "environmental_constraints": constraints,
                "storage_estimation": storage,
                "multi_factor_score": mf_score,
            })
    
        candidates = mf_candidates
    
        # Data confidence assessment
        data_confidence = assess_confidence(
            dem_available=True,
            hydrology_available=True,
            rainfall_available=climate_analysis.get("available"),
            soil_available=soil_analysis.get("available"),
            land_use_available=land_use_analysis.get("available"),
            osm_available=True,
            storage_available=True,
    )

    # =====================================================
    # STEP 12.5
    # Preserve original terrain rank then re-rank by
    # final multi-factor score
    # =====================================================
    for cand in candidates:
        cand["initial_rank"] = cand.get("rank", 0)

    # Sort by multi-factor final_score (descending). Fall back to
    # original terrain suitability score if multi-factor score missing.
    def _sort_key(c):
        mf = c.get("multi_factor_score") or {}
        fs = mf.get("final_score")
        if fs is None:
            return float(c.get("suitability_score") or 0)
        return float(fs)

    candidates.sort(key=_sort_key, reverse=True)

    # Re-assign final_rank (1-based)
    for new_rank, cand in enumerate(candidates, start=1):
        cand["final_rank"] = new_rank
        cand["rank"] = new_rank
        # Keep mf_score reference so frontend can show per-candidate
        # multi-factor without traversing
        if cand.get("multi_factor_score"):
            cand["multi_factor_score"]["final_rank"] = new_rank

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

        "candidates":
            candidates,

        "recommended_candidate_id":
            (
                candidates[0]["candidate_id"]
                if candidates
                else 1
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
