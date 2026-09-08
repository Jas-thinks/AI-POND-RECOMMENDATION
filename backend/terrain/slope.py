from __future__ import annotations
import numpy as np


def calculate_slope_percent(
    dem: np.ndarray,
    valid_mask: np.ndarray,
    resolution_m: float,
) -> np.ndarray:
    """Calculate slope percentage from DEM grid."""
    out = np.copy(dem).astype(float)
    mean_val = float(np.nanmean(dem[valid_mask])) if np.any(valid_mask) else 0.0
    out[~valid_mask] = mean_val
    gy, gx = np.gradient(out, resolution_m)
    slope_rad = np.arctan(np.sqrt(gx**2 + gy**2))
    slope_pct = np.tan(slope_rad) * 100.0
    slope_pct[~valid_mask] = np.nan
    return slope_pct
