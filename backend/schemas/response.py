from typing import Any

from pydantic import BaseModel, Field


# =========================================================
# Terrain
# =========================================================

class TerrainSummary(BaseModel):
    contour_line_count: int
    minimum_elevation_m: float
    maximum_elevation_m: float
    contour_interval_m: float | None
    grid_resolution_m: float
    grid_rows: int
    grid_columns: int


# =========================================================
# Rainfall
# =========================================================

class RainfallYear(BaseModel):
    year: int
    rainfall_mm: float
    valid_days: int


class RainfallSummary(BaseModel):
    available: bool
    source: str
    latitude: float
    longitude: float
    period: str
    average_annual_rainfall_mm: float | None
    yearly: list[RainfallYear]
    error: str | None


# =========================================================
# Land Filter
# =========================================================

class LandFilterSummary(BaseModel):
    available: bool
    source: str
    excluded_feature_counts: dict[str, int]
    excluded_cell_count: int
    free_cell_count: int
    notes: list[str]


# =========================================================
# Catchment
# =========================================================

class CatchmentSummary(BaseModel):
    cell_count: int
    area_m2: float
    area_hectares: float


# =========================================================
# Pond / Water Calculation
# =========================================================

class WaterMetrics(BaseModel):
    pond_area_m2: float
    pond_area_hectares: float
    recommended_depth_m: float
    shape_factor: float

    estimated_storage_capacity_m3: float

    runoff_coefficient: float

    estimated_annual_runoff_m3: float | None

    runoff_to_storage_ratio: float | None

    potential_fill_percent: float | None

    pond_radius_used_m: float
    local_relief_m: float


# =========================================================
# Candidate
# =========================================================

class PondCandidate(BaseModel):
    candidate_id: int
    rank: int

    latitude: float
    longitude: float

    elevation_m: float
    slope_percent: float

    flow_accumulation_cells: int

    suitability_score: float = Field(
        ge=0,
        le=100,
    )

    catchment: CatchmentSummary

    water: WaterMetrics

    land_status: str


# =========================================================
# Candidate Generation Summary
# =========================================================

class CandidateGenerationSummary(BaseModel):
    returned_candidates: int
    max_candidates_requested: int
    minimum_spacing_m: float
    maximum_slope_percent: float
    minimum_accumulation_percentile: float


# =========================================================
# Full API Response
# =========================================================

class AnalysisResponse(BaseModel):
    status: str
    filename: str

    terrain: TerrainSummary

    rainfall: RainfallSummary

    land_filter: LandFilterSummary

    excluded_areas_geojson: dict[str, Any] | None = None

    candidates: list[dict[str, Any]]

    recommended_candidate_id: int

    recommended_catchment_geojson: dict[str, Any] | None

    boundary_geojson: dict[str, Any]

    candidate_generation: CandidateGenerationSummary

    method: dict[str, str]

    limitations: list[str]

    # Multi-factor fields (per-candidate, populated by analysis_service)
    soil_analysis: dict[str, Any] | None = None
    climate_analysis: dict[str, Any] | None = None
    land_use_analysis: dict[str, Any] | None = None
    data_confidence: dict[str, Any] | None = None

    # Optional location search metadata (for analyzeLocation route)
    location_search: dict[str, Any] | None = None
    source_dem: dict[str, Any] | None = None
    generated_contours: dict[str, Any] | None = None
    generated_files: dict[str, Any] | None = None


# =========================================================
# Multi-Factor Analysis Extensions
# =========================================================

class SoilAnalysis(BaseModel):
    available: bool
    source: str | None
    latitude: float | None
    longitude: float | None
    depth_used: str | None
    soil_texture: str | None
    sand_percent: float | None
    silt_percent: float | None
    clay_percent: float | None
    permeability: str | None
    water_retention: str | None
    suitability_score: float | None
    risk_level: str | None
    recommendation: str | None
    error: str | None


class RainfallAnalysis(BaseModel):
    available: bool
    source: str | None
    latitude: float | None
    longitude: float | None
    period: str | None
    annual_rainfall_mm: float | None
    monsoon_rainfall_mm: float | None
    rainfall_reliability: str | None
    seasonality: str | None
    climate_score: float | None
    recommendation: str | None
    error: str | None


class WaterAvailability(BaseModel):
    available: bool
    catchment_area_m2: float | None
    annual_rainfall_mm: float | None
    runoff_coefficient: float | None
    estimated_annual_runoff_m3: float | None
    water_availability_score: float | None
    confidence: str | None
    assumptions: list[str]
    error: str | None


class LandUseAnalysis(BaseModel):
    available: bool
    source: str | None
    latitude: float | None
    longitude: float | None
    radius_m: float | None
    dominant_land_use: str | None
    land_use_distribution: dict[str, int]
    suitability_score: float | None
    restrictions: list[str]
    recommendation: str | None
    error: str | None


class AccessibilityAnalysis(BaseModel):
    available: bool
    source: str | None
    nearest_road_distance_m: float | None
    accessibility_score: float | None
    construction_access: str | None
    recommendation: str | None
    error: str | None


class EnvironmentalConstraints(BaseModel):
    hard_constraints: list[str]
    soft_constraints: list[str]
    warnings: list[str]
    overall_clearance: str


class StorageEstimation(BaseModel):
    available: bool
    estimated_surface_area_m2: float | None
    estimated_average_depth_m: float | None
    estimated_max_depth_m: float | None
    estimated_storage_volume_m3: float | None
    shape_factor: float | None
    confidence: str | None
    disclaimer: str | None
    error: str | None


class MultiFactorScore(BaseModel):
    final_score: float
    category: str
    confidence: str
    available_factors: list[str]
    unavailable_factors: list[str]
    weights_used: dict[str, str]
    factor_breakdown: dict[str, dict]


class DataConfidence(BaseModel):
    overall_confidence: str
    data_sources_available: list[str]
    data_sources_missing: list[str]
    limitations: list[str]
