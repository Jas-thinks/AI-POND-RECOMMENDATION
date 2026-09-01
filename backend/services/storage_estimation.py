"""
Pond storage estimation.

Estimates preliminary pond storage volume from terrain-derived
depression geometry. Values are planning-level only and require
field survey and geotechnical validation.

Formula (simplified):
    Volume = Surface Area * Average Depth * Shape Factor

Where:
    Surface Area  - terrain-derived connected depression area
    Average Depth  - configurable default depth (max_depth / 2)
    Shape Factor   - 0.5 for natural depressions, 0.7 for bowl-shaped
"""

from __future__ import annotations


SHAPE_FACTOR_DEPRESSION = 0.55  # natural depression
SHAPE_FACTOR_BOWL = 0.70        # bowl-shaped impoundment


def estimate_storage(
    surface_area_m2: float | None,
    max_depth_m: float = 3.0,
    shape_factor: float | None = None,
) -> dict:
    """
    Estimate pond storage volume.

    Parameters
    ----------
    surface_area_m2 : float | None
        The connected low-lying terrain area in square metres.
    max_depth_m : float
        The maximum design depth (m).
    shape_factor : float | None
        Optional override. If None, defaults to SHAPE_FACTOR_DEPRESSION.

    Returns
    -------
    dict
        Storage estimate dict or unavailable payload.
    """
    if surface_area_m2 is None or surface_area_m2 <= 0:
        return {
            "available": False,
            "estimated_surface_area_m2": None,
            "estimated_average_depth_m": None,
            "estimated_max_depth_m": max_depth_m,
            "estimated_storage_volume_m3": None,
            "shape_factor": shape_factor,
            "confidence": "low",
            "disclaimer": (
                "Surface area could not be derived. "
                "Detailed field survey and geotechnical validation required."
            ),
            "error": "No surface area provided.",
        }

    sf = shape_factor if shape_factor is not None else SHAPE_FACTOR_DEPRESSION
    avg_depth = max_depth_m * 0.6
    volume = surface_area_m2 * avg_depth * sf
    confidence = "medium" if surface_area_m2 >= 5000 else "low"

    return {
        "available": True,
        "estimated_surface_area_m2": round(surface_area_m2, 1),
        "estimated_average_depth_m": round(avg_depth, 2),
        "estimated_max_depth_m": max_depth_m,
        "estimated_storage_volume_m3": round(volume, 1),
        "shape_factor": sf,
        "confidence": confidence,
        "disclaimer": (
            "Preliminary terrain-based estimate. "
            "Detailed field survey and geotechnical validation required."
        ),
        "error": None,
    }
