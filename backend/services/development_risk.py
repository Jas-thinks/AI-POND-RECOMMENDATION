"""
Data-driven "future development risk" layer for pond-candidate selection.

This does NOT try to predict exactly where buildings will be built in the
future. Instead it scores how likely an area is to be developed, using data
that is realistically available from existing sources:

    * proximity to and density of existing mapped buildings
    * proximity to and density of roads/streets
    * existing built-up / urban land use (residential, commercial,
      industrial, retail, construction, garages, cemeteries)

The layer produces, for every grid cell:

    * ``high_risk_mask``  - cells where development risk is VERY HIGH.
      These cells are hard-excluded from pond-candidate generation (they can
      never become candidates).
    * ``risk_level``      - per-cell class: very_high / medium / low.
    * ``risk_score_grid`` - a continuous 0-100 score used to *prefer* low-risk
      areas and *penalise* medium-risk areas when ranking candidates.

All inputs come from the same batched Overpass payload already downloaded by
``backend.services.land_service`` inside ``LandFilterResult``. No additional
network request is made, and nothing here is raster-cell-requested.

TODO(user): consider importing binary_dilation from scipy.ndimage for the
proximity bands.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Risk thresholds
# ---------------------------------------------------------------------------

# Number of raster cells of proximity considered "very high" development risk
# around a previously mapped building or road feature. These cells are
# excluded from pond-candidate generation.
VERY_HIGH_PROXIMITY_CELLS = 3

# Number of raster cells beyond the "very high" band that are still flagged as
# "medium" development risk (strongly penalised, but not excluded).
MEDIUM_PROXIMITY_CELLS = 6

# Development-risk score multipliers (0-100). Low-risk cells score 100
# (preferred); medium-risk cells score lower so they rank below low-risk
# cells with an otherwise equal suitability score.
LOW_RISK_SCORE = 100.0
MEDIUM_RISK_SCORE = 40.0
VERY_HIGH_RISK_SCORE = 0.0


@dataclass(frozen=True)
class DevelopmentRiskResult:
    # Boolean grid (same shape as the DEM): True where the cell is at very
    # high development risk and must be excluded from candidate generation.
    high_risk_mask: np.ndarray

    # Float grid (same shape as the DEM) with a 0-100 development-risk score,
    # where 100 = low development risk (preferred) and 0 = very high risk.
    risk_score_grid: np.ndarray

    # Whole-area summary label: 'very_high' / 'medium' / 'low'.
    level: str

    # Counts of cells by risk class for reporting.
    counts: dict

    # Explanatory notes about how the risk level was derived (for the
    # frontend / API consumers). Does not include internal implementation
    # details.
    notes: list[str]


def _dilate(mask: np.ndarray, steps: int) -> np.ndarray:
    """Grow a boolean mask outward by ``steps`` raster cells.

    A cheap morphological proximity operator (4-connected). Pure NumPy, so it
    keeps the whole computation small and fast for render-safe deployments.
    """
    result = mask.copy()
    nrows, ncols = mask.shape
    for _ in range(steps):
        rolled = result
        # 4-connected neighbourhood expansion.
        grown = (np.roll(rolled, 1, axis=0) | np.roll(rolled, -1, axis=0)
                 | np.roll(rolled, 1, axis=1) | np.roll(rolled, -1, axis=1))
        result = grown | result
    return result


def build_development_risk(
    land_filter,
    terrain,
) -> DevelopmentRiskResult:
    """Derive a future-development-risk score from already-fetched OSM data.

    Parameters
    ----------
    land_filter : LandFilterResult
        The result of ``land_service.build_osm_free_land_mask``. Its
        ``osm_elements`` and per-category ``category_masks`` come from the
        same single batched Overpass query and are reused here (no new HTTP).
    terrain : TerrainGrid
        The terrain grid, used for the mask/output shape.

    Returns
    -------
    DevelopmentRiskResult
    """
    valid = land_filter.free_land_mask

    shape = terrain.valid_mask.shape

    # Start with neutral (low-risk) score everywhere.
    risk_score = np.full(shape, LOW_RISK_SCORE, dtype=np.float64)

    # ----- Category masks from land_service --------------------------------
    # These masks already include the configured buffer around each feature,
    # so features that are ruled out by the hard exclusions (water, roads,
    # buildings) are consistently reflected in the risk layer.
    building_mask = land_filter.category_masks.get(
        "building",
        np.zeros(shape, dtype=bool),
    )

    road_mask = land_filter.category_masks.get(
        "road",
        np.zeros(shape, dtype=bool),
    )

    builtup_mask = land_filter.category_masks.get(
        "builtup",
        np.zeros(shape, dtype=bool),
    )

    # Narrow "very high" proximity band around buildings and roads. These
    # cells are outside the buffer used for hard exclusion, but are so close
    # to existing development that the likelihood of future building or road
    # widening is high.
    very_high_near = (
        _dilate(building_mask, VERY_HIGH_PROXIMITY_CELLS)
        | _dilate(road_mask, VERY_HIGH_PROXIMITY_CELLS)
        | builtup_mask
    )

    # Wider "medium" proximity band, still near existing development but not
    # qualifying as very-high risk.
    medium_near = (
        _dilate(building_mask, MEDIUM_PROXIMITY_CELLS)
        | _dilate(road_mask, MEDIUM_PROXIMITY_CELLS)
    ) & ~very_high_near

    # Apply the scores.
    risk_score[medium_near & valid] = MEDIUM_RISK_SCORE
    risk_score[very_high_near & valid] = VERY_HIGH_RISK_SCORE

    # Very-high-risk cells are hard-excluded from candidate generation.
    high_risk_mask = very_high_near & valid

    # Summary for reporting (only over valid terrain).
    counts = {
        "very_high": int((high_risk_mask).sum()),
        "medium": int((medium_near & valid).sum()),
        "low": int(valid.sum() - (high_risk_mask | (medium_near & valid)).sum()),
    }

    if counts["very_high"] > 0:
        level = "very_high"
    elif counts["medium"] > 0:
        level = "medium"
    else:
        level = "low"

    notes = [
        (
            "Future development risk is estimated from existing mapped "
            "buildings, road proximity and built-up land use. It is NOT an "
            "exact prediction of future buildings."
        ),
        (
            "Very-high-risk cells are excluded from pond-candidate "
            "selection; medium-risk cells are ranked lower than low-risk "
            "cells with otherwise similar suitability."
        ),
    ]

    return DevelopmentRiskResult(
        high_risk_mask=high_risk_mask,
        risk_score_grid=risk_score,
        level=level,
        counts=counts,
        notes=notes,
    )