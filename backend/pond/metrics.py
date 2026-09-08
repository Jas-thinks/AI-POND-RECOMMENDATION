from __future__ import annotations
import numpy as np
from scipy.ndimage import label


def estimate_candidate_water_metrics(
    row: int,
    col: int,
    dem: np.ndarray,
    slope_percent: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
    catchment_area_m2: float,
    average_annual_rainfall_mm: float | None,
    runoff_coefficient: float = 0.30,
    pond_radius_m: float = 40.0,
    max_pond_slope_percent: float = 5.0,
    max_pond_depth_m: float = 3.0,
    shape_factor: float = 0.70,
) -> dict:
    """Calculate planning-level pond footprint, storage and runoff metrics."""
    rows, cols = dem.shape
    radius_cells = max(1, int(np.ceil(pond_radius_m / resolution_m)))

    r0 = max(0, row - radius_cells)
    r1 = min(rows, row + radius_cells + 1)
    c0 = max(0, col - radius_cells)
    c1 = min(cols, col + radius_cells + 1)

    rr, cc = np.ogrid[r0:r1, c0:c1]
    distance_m = np.sqrt((rr - row)**2 + (cc - col)**2) * resolution_m

    local_valid = valid_mask[r0:r1, c0:c1] & (distance_m <= pond_radius_m) & np.isfinite(dem[r0:r1, c0:c1])
    local_slope = slope_percent[r0:r1, c0:c1]
    eligible = local_valid & (local_slope <= max_pond_slope_percent)

    local_row = row - r0
    local_col = col - c0
    labels, _ = label(eligible, structure=np.ones((3, 3)))
    comp_id = labels[local_row, local_col]

    if comp_id > 0:
        footprint_mask = labels == comp_id
    else:
        footprint_mask = np.zeros_like(eligible, dtype=bool)
        footprint_mask[local_row, local_col] = True

    footprint_cells = int(footprint_mask.sum())
    cell_area_m2 = resolution_m * resolution_m
    pond_area_m2 = footprint_cells * cell_area_m2

    local_dem = dem[r0:r1, c0:c1]
    footprint_elevs = local_dem[footprint_mask]
    cand_elev = float(dem[row, col])

    local_relief_m = float(np.nanpercentile(footprint_elevs, 90) - cand_elev) if footprint_elevs.size > 0 else 1.5
    local_relief_m = float(np.clip(local_relief_m, 0.5, max_pond_depth_m))

    rec_depth_m = local_relief_m
    storage_vol_m3 = pond_area_m2 * rec_depth_m * shape_factor

    if average_annual_rainfall_mm is not None:
        rain_m = average_annual_rainfall_mm / 1000.0
        annual_runoff_m3 = catchment_area_m2 * rain_m * runoff_coefficient
    else:
        annual_runoff_m3 = 0.0

    runoff_ratio = annual_runoff_m3 / storage_vol_m3 if storage_vol_m3 > 0 else 0.0
    potential_fill_pct = min(100.0, runoff_ratio * 100.0) if storage_vol_m3 > 0 else 0.0

    return {
        "pond_area_m2": round(pond_area_m2, 2),
        "pond_area_hectares": round(pond_area_m2 / 10000.0, 4),
        "recommended_depth_m": round(rec_depth_m, 2),
        "shape_factor": shape_factor,
        "estimated_storage_capacity_m3": round(storage_vol_m3, 1),
        "runoff_coefficient": runoff_coefficient,
        "estimated_annual_runoff_m3": round(annual_runoff_m3, 1),
        "runoff_to_storage_ratio": round(runoff_ratio, 2),
        "potential_fill_percent": round(potential_fill_pct, 1),
        "pond_radius_used_m": pond_radius_m,
        "local_relief_m": round(local_relief_m, 2),
    }
