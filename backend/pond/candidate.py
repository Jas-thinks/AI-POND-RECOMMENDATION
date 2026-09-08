from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from scipy.ndimage import binary_erosion, maximum_filter


@dataclass(frozen=True)
class CandidateCell:
    row: int
    col: int
    score: float


def _candidate_score_grid(
    dem: np.ndarray,
    slope_percent: np.ndarray,
    accumulation: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
    max_slope_percent: float = 8.0,
    min_boundary_distance_m: float = 30.0,
    min_accumulation_percentile: float = 85.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a 0..1 suitability score grid for hydrological pond candidates."""
    valid = valid_mask & np.isfinite(dem) & np.isfinite(slope_percent) & np.isfinite(accumulation)
    iterations = max(1, int(round(min_boundary_distance_m / resolution_m)))
    interior = binary_erosion(valid, iterations=iterations, border_value=0)

    acc_values = accumulation[interior]
    if acc_values.size == 0:
        raise ValueError("No valid flow-accumulation cells are available for candidate selection.")

    acc_thresh = float(np.nanpercentile(acc_values, min_accumulation_percentile))
    candidate_mask = interior & (slope_percent <= max_slope_percent) & (accumulation >= acc_thresh)

    acc = np.where(candidate_mask, accumulation, np.nan)
    elev = np.where(candidate_mask, dem, np.nan)
    slope = np.where(candidate_mask, slope_percent, np.nan)

    log_acc = np.log1p(acc)
    amin, amax = np.nanmin(log_acc), np.nanmax(log_acc)
    acc_score = (log_acc - amin) / (amax - amin + 1e-12) if amax > amin else np.where(candidate_mask, 1.0, 0.0)

    emin, emax = np.nanmin(elev), np.nanmax(elev)
    low_elev_score = (emax - elev) / (emax - emin + 1e-12) if emax > emin else np.where(candidate_mask, 1.0, 0.0)

    slope_score = np.clip(1.0 - (slope / max_slope_percent), 0.0, 1.0)

    score = 0.65 * acc_score + 0.20 * low_elev_score + 0.15 * slope_score
    score[~candidate_mask] = 0.0

    return score, candidate_mask


def find_pond_candidates(
    dem: np.ndarray,
    slope_percent: np.ndarray,
    accumulation: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
    max_candidates: int = 20,
    max_slope_percent: float = 8.0,
    min_boundary_distance_m: float = 30.0,
    min_candidate_spacing_m: float = 100.0,
    min_accumulation_percentile: float = 85.0,
) -> list[CandidateCell]:
    score_grid, candidate_mask = _candidate_score_grid(
        dem,
        slope_percent,
        accumulation,
        valid_mask,
        resolution_m,
        max_slope_percent=max_slope_percent,
        min_boundary_distance_m=min_boundary_distance_m,
        min_accumulation_percentile=min_accumulation_percentile,
    )

    spacing_cells = max(1, int(round(min_candidate_spacing_m / resolution_m)))
    window = 2 * spacing_cells + 1

    safe_score = np.where(candidate_mask, score_grid, -np.inf)
    local_max = maximum_filter(safe_score, size=window, mode="constant", cval=-np.inf)
    peak_mask = candidate_mask & (score_grid > 0) & (score_grid == local_max)

    peak_indices = np.argwhere(peak_mask)
    if peak_indices.size == 0:
        best_flat = np.nanargmax(safe_score)
        r, c = np.unravel_index(best_flat, dem.shape)
        return [CandidateCell(row=int(r), col=int(c), score=float(round(score_grid[r, c] * 100.0, 2)))]

    raw_candidates = [
        CandidateCell(row=int(r), col=int(c), score=float(round(score_grid[r, c] * 100.0, 2)))
        for r, c in peak_indices
    ]
    raw_candidates.sort(key=lambda item: item.score, reverse=True)

    selected: list[CandidateCell] = []
    for cand in raw_candidates:
        if len(selected) >= max_candidates:
            break
        if not any(max(abs(cand.row - s.row), abs(cand.col - s.col)) < spacing_cells for s in selected):
            selected.append(cand)

    return selected


def choose_pond_candidate(
    dem: np.ndarray,
    slope_percent: np.ndarray,
    accumulation: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
    max_slope_percent: float = 8.0,
    min_boundary_distance_m: float = 30.0,
) -> tuple[int, int, float]:
    candidates = find_pond_candidates(
        dem,
        slope_percent,
        accumulation,
        valid_mask,
        resolution_m,
        max_candidates=1,
        max_slope_percent=max_slope_percent,
        min_boundary_distance_m=min_boundary_distance_m,
    )
    best = candidates[0]
    return best.row, best.col, best.score
