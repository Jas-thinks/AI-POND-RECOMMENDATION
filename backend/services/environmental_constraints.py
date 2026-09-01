"""
Environmental constraints framework.

Preserves existing hard-exclusion logic (water bodies, streams, roads,
buildings — already handled in land_service) and adds a modular soft-
constraint layer for future data sources (protected areas, wetlands, etc.).

This module does NOT fabricate constraints. Constraints are only added
when real data is available.
"""

from __future__ import annotations


def evaluate_constraints(
    land_filter_result: dict | None = None,
    land_use_restrictions: list[str] | None = None,
    protected_area_nearby: bool = False,
    protected_area_name: str | None = None,
    wetland_nearby: bool = False,
) -> dict:
    """
    Evaluate environmental constraints for a candidate site.

    Parameters
    ----------
    land_filter_result : dict | None
        The land_service LandFilterResult as a dict (feature_counts,
        excluded_cell_count, free_cell_count).
    land_use_restrictions : list[str] | None
        Restrictions found by landuse_analysis.
    protected_area_nearby : bool
        True only if verified protected-area data is available.
    protected_area_name : str | None
        Name of the protected area if known.
    wetland_nearby : bool
        True only if verified wetland data is available.

    Returns
    -------
    dict
        Structured constraint report with hard_constraints, soft_constraints,
        warnings, and overall clearance status.
    """
    hard_constraints: list[str] = []
    soft_constraints: list[str] = []
    warnings: list[str] = []

    # Existing exclusions (already applied upstream)
    if land_filter_result:
        counts = land_filter_result.get("feature_counts", {})
        excluded = land_filter_result.get("excluded_cell_count", 0)
        free = land_filter_result.get("free_cell_count", 1)
        if counts.get("water", 0) > 0:
            hard_constraints.append(
                f"Existing water bodies detected ({counts['water']} features) - "
                "exclusion already applied."
            )
        if counts.get("waterway", 0) > 0:
            hard_constraints.append(
                f"Streams/drains detected ({counts['waterway']} features) - "
                "exclusion already applied."
            )
        if counts.get("road", 0) > 0:
            hard_constraints.append(
                f"Roads detected ({counts['road']} features) - "
                "exclusion already applied."
            )
        if counts.get("building", 0) > 0:
            hard_constraints.append(
                f"Buildings detected ({counts['building']} features) - "
                "exclusion already applied."
            )
        exclusion_pct = round(excluded / (excluded + free) * 100, 1) if (excluded + free) > 0 else 0
        if exclusion_pct > 50:
            warnings.append(
                f"High exclusion density ({exclusion_pct}% of cells excluded). "
                "Remaining viable land area is limited."
            )

    # Land-use derived restrictions
    if land_use_restrictions:
        for r in land_use_restrictions:
            if "protected" in r.lower():
                soft_constraints.append(r)
            else:
                warnings.append(r)

    # Protected area (only if confirmed)
    if protected_area_nearby:
        name_str = f" ({protected_area_name})" if protected_area_name else ""
        soft_constraints.append(
            f"Protected area{name_str} may restrict construction. "
            "Official permission required before proceeding."
        )

    # Wetland (only if confirmed)
    if wetland_nearby:
        soft_constraints.append(
            "Wetland detected nearby. "
            "Construction may be subject to environmental clearance."
        )

    # Overall clearance
    if hard_constraints:
        clearance = "RESTRICTED - existing exclusions apply"
    elif soft_constraints:
        clearance = "CAUTION - environmental restrictions nearby"
    else:
        clearance = "CLEAR - no major environmental constraints detected"

    return {
        "hard_constraints": hard_constraints,
        "soft_constraints": soft_constraints,
        "warnings": warnings,
        "overall_clearance": clearance,
    }
