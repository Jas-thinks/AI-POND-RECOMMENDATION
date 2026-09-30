"""
Multi-factor suitability scoring engine — TOPSIS ranking.

The pipeline scores every qualifying candidate on five independent,
mutually-exclusive factors that mirror the system design:

    Terrain         - slope, elevation, relief derived from the DEM.
    Hydrology       - own catchment (delineated per candidate) and
                      proximity to strong DEM-derived drainage channels.
    Water storage   - limited by the smaller of annual runoff supply and
                      achievable storage capacity.
    Practical       - soil, land use and road accessibility.
    Beneficiary     - proximity to the settlements and residential
                      buildings that would actually use the pond.

Each factor is genuinely independent: a factor is never silently
substituted with a copy of another factor's score, and a missing external
input (e.g. rainfall, soil, OSM settlement data) is dropped from the weight
sum rather than replaced with a fabricated default.

Ranking uses TOPSIS (Technique for Order of Preference by Similarity to
Ideal Solution), a named multi-criteria decision method:

    1. Build a decision matrix: rows = surviving candidates, columns = the
       five factor scores (normalised to 0-1).
    2. Apply the weights (renormalised per candidate for any missing
       factor) to build the weighted normalised matrix  v_ij = w_j * x_ij.
    3. Determine the ideal best  A+  (column maximum) and ideal worst
       A-  (column minimum) of the weighted matrix.
    4. For each candidate compute the Euclidean distance to A+ (S_plus) and
       to A- (S_minus).
    5. Closeness coefficient  C_i = S_minus / (S_plus + S_minus)  is the
       final score (0-1, 1 = closest to ideal).
    6. Rank candidates by C_i descending.

When a factor is unavailable for a candidate:
    1. Remove it from that candidate's denominator.
    2. Redistribute weights proportionally among the available factors.
    3. Report unavailable_factors in the output metadata.

The closeness coefficient, distances and per-factor weighted contributions
are kept arithmetically exact so a reviewer can reproduce them by hand.
"""

from __future__ import annotations

import math

from typing import Any


# Default weights for the five consolidated factors (must be configurable).
#
# The beneficiary factor is given an initial 0.15 weight. The original four
# factors are scaled down proportionally (× 0.85) so the five weights still
# sum to exactly 1.0:
#   terrain 0.30→0.255, hydrology 0.25→0.2125, water_storage 0.25→0.2125,
#   practical 0.20→0.17, beneficiary 0.15.
DEFAULT_WEIGHTS = {
    "terrain": 0.255,
    "hydrology": 0.2125,
    "water_storage": 0.2125,
    "practical": 0.17,
    "beneficiary": 0.15,
}

# Canonical factor ordering used by the decision matrix.
FACTORS = ("terrain", "hydrology", "water_storage", "practical", "beneficiary")

# Minimum separation (metres) enforced between candidates in the final
# top-N selection. Spatially distinct options are preferred over a cluster
# of nearly identical nearby sites.
MIN_FINAL_SEPARATION_M = 400.0

# Maximum separation (metres) considered "nearby" when assigning
# alternatives that were skipped by diversification to a candidate.
NEARBY_ALTERNATIVE_RADIUS_M = 1000.0


def _normalize_weights(available_factors: list[str], weights: dict[str, float]) -> dict[str, float]:
    """Redistribute weights proportionally when some factors are unavailable."""
    total = sum(weights[k] for k in available_factors)
    if total == 0:
        return {k: 1.0 / len(available_factors) if available_factors else {} for k in available_factors}
    return {k: weights[k] / total for k in available_factors}


def _describe_weights(weights: dict[str, float]) -> dict[str, str]:
    """Human-readable weight descriptions."""
    return {k: f"{v * 100:.0f}%" for k, v in weights.items()}


def _is_available(result: dict | None, value_key: str) -> bool:
    """Whether an analysis result actually provided a usable score."""
    if not result:
        return False
    if result.get("available") is False:
        return False
    val = result.get(value_key)
    return isinstance(val, (int, float)) and val is not None


def practical_raw_score(
    soil_analysis: dict | None,
    land_use_analysis: dict | None,
    accessibility_analysis: dict | None,
) -> float | None:
    """
    Aggregate the practical sub-factors (soil, land use, accessibility)
    into a single 0-100 raw score. Only available sub-factors are
    averaged; missing ones are ignored (never fabricated).
    """
    available_scores: list[float] = []
    if _is_available(soil_analysis, "suitability_score"):
        available_scores.append(float(soil_analysis["suitability_score"]))
    if _is_available(land_use_analysis, "suitability_score"):
        available_scores.append(float(land_use_analysis["suitability_score"]))
    if _is_available(accessibility_analysis, "accessibility_score"):
        available_scores.append(float(accessibility_analysis["accessibility_score"]))
    if not available_scores:
        return None
    return round(sum(available_scores) / len(available_scores), 2)


def _round(value: float | None, ndigits: int = 4) -> float | None:
    """Round a value unless it is None."""
    if value is None:
        return None
    return round(float(value), ndigits)


def _category_label(score_0_1: float) -> str:
    """Map a 0-1 closeness coefficient to a suitability category label."""
    score = score_0_1 * 100.0
    if score >= 85:
        return "Highly Suitable"
    if score >= 70:
        return "Suitable"
    if score >= 55:
        return "Marginally Suitable"
    if score >= 40:
        return "Poorly Suitable"
    return "Unsuitable"


def _confidence_label(available_count: int) -> str:
    pct = available_count / len(FACTORS) if FACTORS else 0
    if pct >= 0.83:
        return "high"
    if pct >= 0.5:
        return "medium"
    return "low"


def rank_candidates_topsis(
    candidates: list[dict],
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """
    Rank candidates by TOPSIS closeness to the ideal solution.

    Parameters
    ----------
    candidates : list[dict]
        Each candidate dict must carry a ``factor_scores`` key mapping the
        four factor names to a 0-100 score (or None when unavailable).
    weights : dict | None
        Custom weight override. Defaults to DEFAULT_WEIGHTS.

    Returns
    -------
    list[dict]
        The input candidate list, reordered by descending closeness
        coefficient. Each candidate's ``multi_factor_score`` is populated
        with the TOPSIS geometry:
            - S_plus  : Euclidean distance to the ideal best (A+).
            - S_minus : Euclidean distance to the ideal worst (A-).
            - C_i     : closeness coefficient = S_minus / (S_plus + S_minus).
            - final_score : the closeness coefficient (0-1).
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS.copy()

    if not candidates:
        return candidates

    # ------------------------------------------------------------------
    # 1. Build the decision matrix from each candidate's raw factor scores.
    #    Raw scores are 0-100; normalise them to 0-1.
    # ------------------------------------------------------------------
    rows: list[dict] = []
    for cand in candidates:
        factor_scores = cand.get("factor_scores") or {}

        raw = {f: factor_scores.get(f) for f in FACTORS}
        available = [
            f for f in FACTORS
            if isinstance(raw.get(f), (int, float)) and raw.get(f) is not None
        ]

        # Per-candidate renormalised weights over available factors.
        w = _normalize_weights(available, weights)

        # x_ij in 0-1.
        x = {
            f: (float(raw[f]) / 100.0 if raw[f] is not None else 0.0)
            for f in FACTORS
        }
        # Weighted normalised matrix v_ij = w_j * x_ij.
        v = {f: w.get(f, 0.0) * x[f] for f in FACTORS}

        rows.append({
            "_candidate": cand,
            "raw": raw,
            "available": available,
            "unavailable": [f for f in FACTORS if f not in available],
            "w": w,
            "x": x,
            "v": v,
        })

    # ------------------------------------------------------------------
    # 3. Ideal best A+ = max of each weighted column; ideal worst A- = min.
    #    Higher is always better for all four factors, so these are the
    #    straightforward column extrema of the weighted matrix.
    # ------------------------------------------------------------------
    pos_ideal = {f: max(row["v"][f] for row in rows) for f in FACTORS}
    neg_ideal = {f: min(row["v"][f] for row in rows) for f in FACTORS}

    # ------------------------------------------------------------------
    # 4-5. Euclidean distances and closeness coefficients.
    # ------------------------------------------------------------------
    for row in rows:
        s_plus = math.sqrt(sum((row["v"][f] - pos_ideal[f]) ** 2 for f in FACTORS))
        s_minus = math.sqrt(sum((row["v"][f] - neg_ideal[f]) ** 2 for f in FACTORS))
        denom = s_plus + s_minus
        c_i = (s_minus / denom) if denom > 0 else 0.0
        row["S_plus"] = s_plus
        row["S_minus"] = s_minus
        row["C_i"] = c_i

    # ------------------------------------------------------------------
    # 6. Rank by closeness coefficient descending.
    # ------------------------------------------------------------------
    rows.sort(key=lambda r: r["C_i"], reverse=True)

    # ------------------------------------------------------------------
    # Populate each candidate's multi_factor_score with the TOPSIS model.
    # ------------------------------------------------------------------
    ranked: list[dict] = []
    for new_rank, row in enumerate(rows, start=1):
        cand = row["_candidate"]
        cand_id = cand.get("candidate_id")
        raw = row["raw"]
        available = row["available"]
        unavailable = row["unavailable"]
        w = row["w"]
        v = row["v"]
        c_i = row["C_i"]

        # Detailed sub-factor breakdown for the frontend. Contributions use
        # the weighted normalised values v_ij actually used in TOPSIS.
        breakdown = {
            "terrain": {
                "score": _round(raw.get("terrain"), 2),
                "weight": _round(w.get("terrain")),
                "weighted_contribution": _round(v.get("terrain")),
                "source": "DEM / terrain analysis",
            },
            "hydrology": {
                "score": _round(raw.get("hydrology"), 2),
                "weight": _round(w.get("hydrology")),
                "weighted_contribution": _round(v.get("hydrology")),
                "source": "D8 catchment delineation + flow-accumulation proximity",
            },
            "catchment": {
                "score": _round(raw.get("hydrology"), 2),
                "weight": _round(w.get("hydrology")),
                "weighted_contribution": _round(v.get("hydrology")),
                "source": "D8 catchment delineation",
            },
            "water_storage": {
                "score": _round(raw.get("water_storage"), 2),
                "weight": _round(w.get("water_storage")),
                "weighted_contribution": _round(v.get("water_storage")),
                "source": "Runoff supply vs storage capacity",
            },
            "soil": {
                "score": _sub_score(cand, "soil_analysis", "suitability_score"),
                "weight": None,
                "weighted_contribution": None,
                "source": (cand.get("soil_analysis") or {}).get("source") or "N/A",
            },
            "rainfall": {
                "score": _sub_score(cand, "climate_analysis", "climate_score"),
                "weight": None,
                "weighted_contribution": None,
                "source": (cand.get("climate_analysis") or {}).get("source") or "N/A",
            },
            "land_use": {
                "score": _sub_score(cand, "land_use_analysis", "suitability_score"),
                "weight": None,
                "weighted_contribution": None,
                "source": (cand.get("land_use_analysis") or {}).get("source") or "N/A",
            },
            "accessibility": {
                "score": _sub_score(cand, "accessibility_analysis", "accessibility_score"),
                "weight": None,
                "weighted_contribution": None,
                "source": (cand.get("accessibility_analysis") or {}).get("source") or "N/A",
            },
            "beneficiary": {
                "score": _sub_score(cand, "beneficiary_analysis", "beneficiary_score"),
                "weight": _round(w.get("beneficiary")),
                "weighted_contribution": _round(v.get("beneficiary")),
                "source": (cand.get("beneficiary_analysis") or {}).get("source") or "N/A",
            },
        }

        consolidated_factors = [
            {
                "factor": factor,
                "raw_score": _round(raw.get(factor), 2),
                "weight_used": w.get(factor),
                "contribution": _round(v.get(factor)),
                "available": factor in available,
            }
            for factor in FACTORS
        ]

        cand["multi_factor_score"] = {
            "candidate_id": cand_id,
            "rank": new_rank,
            "final_score": _round(c_i),
            "category": _category_label(c_i),
            "confidence": _confidence_label(len(available)),
            "available_factors": available,
            "unavailable_factors": unavailable,
            "weights_used": _describe_weights(w),
            "factor_breakdown": breakdown,
            "consolidated_factors": consolidated_factors,
            "S_plus": _round(row["S_plus"]),
            "S_minus": _round(row["S_minus"]),
            "C_i": _round(c_i),
        }

        # Re-set ranking bookkeeping on the candidate.
        cand["final_rank"] = new_rank
        cand["rank"] = new_rank
        ranked.append(cand)

    return ranked


def _sub_score(cand: dict, analysis_key: str, score_key: str) -> float | None:
    """Extract a sub-factor score from a candidate's analysis dict."""
    result = cand.get(analysis_key) or {}
    if result.get("available") is False:
        return None
    val = result.get(score_key)
    if isinstance(val, (int, float)) and val is not None:
        return _round(float(val), 2)
    return None


def _haversine_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    import math as _m
    r = 6371000.0
    phi1, phi2 = _m.radians(lat1), _m.radians(lat2)
    dphi = _m.radians(lat2 - lat1)
    dlam = _m.radians(lon2 - lon1)
    a = (
        _m.sin(dphi / 2.0) ** 2
        + _m.cos(phi1) * _m.cos(phi2) * _m.sin(dlam / 2.0) ** 2
    )
    return 2.0 * r * _m.asin(_m.sqrt(a))


def diversify_top_n(
    ranked_candidates: list[dict],
    min_separation_m: float = MIN_FINAL_SEPARATION_M,
    nearby_radius_m: float = NEARBY_ALTERNATIVE_RADIUS_M,
    top_n: int | None = None,
) -> tuple[list[dict], list[dict], list[dict], int]:
    """
    Select a spatially-diversified final top-N from the TOPSIS-ranked list.

    Greedily walk the ranked list, keeping a candidate only if it is more
    than ``min_separation_m`` from every already-selected candidate. This
    does NOT change the underlying TOPSIS ranking or scores — it only
    changes which candidates appear in the final top-N so the recommended
    set is genuinely distinct, not a tight cluster of near-identical sites.

    Parameters
    ----------
    ranked_candidates : list[dict]
        Candidates already ordered by descending TOPSIS closeness.
    min_separation_m : float
        Minimum distance (metres) a candidate must keep from already
        selected candidates to be included in the diversified top-N.
    nearby_radius_m : float
        Radius (metres) used to attach a skipped "nearby alternative" to the
        selected candidate it is clustered with.
    top_n : int | None
        Maximum number of diversified candidates to keep (the final top-N).
        When None, keep as many as pass the separation test.

    Returns
    -------
    (selected, nearby_alternatives, remaining, skipped_count)

    selected             : list[dict]  diversified top-N in rank order.
    nearby_alternatives  : list[dict]  high-ranked candidates set aside
                                       because they sit within the radius of
                                       a selected candidate.
    remaining            : list[dict]  any other candidates not selected and
                                       not clustered as nearby.
    skipped_count        : int         how many candidates were skipped by
                                       this diversification step.
    """
    selected: list[dict] = []
    nearby_alternatives: list[dict] = []
    remaining: list[dict] = []
    skipped: int = 0

    for cand in ranked_candidates:
        lat = cand.get("latitude")
        lon = cand.get("longitude")
        if lat is None or lon is None:
            remaining.append(cand)
            continue

        def distance_to(others):
            return min(
                (_haversine_m(lat, lon, s.get("latitude"), s.get("longitude")) for s in others),
                default=None,
            )

        # Determine whether this candidate is within the separation distance
        # of a candidate already selected.
        nearest_selected = distance_to(selected)

        if nearest_selected is not None and nearest_selected <= min_separation_m:
            # It is too close to a selected candidate. Keep it visible as a
            # nearby alternative rather than dropping it silently.
            skipped += 1
            if nearest_selected <= nearby_radius_m:
                nearby_alternatives.append(cand)
            else:
                remaining.append(cand)
            continue

        # It is spatially distinct from every selected candidate. If we have
        # room in the final top-N, select it; otherwise it becomes a
        # remaining candidate.
        if top_n is None or len(selected) < top_n:
            selected.append(cand)
        else:
            remaining.append(cand)

    return selected, nearby_alternatives, remaining, skipped


def score_candidate(
    candidate: dict,
    terrain_score: float | None = None,
    hydrology_score: float | None = None,
    water_storage_score: float | None = None,
    soil_analysis: dict | None = None,
    rainfall_analysis: dict | None = None,
    water_availability: dict | None = None,
    storage_estimation: dict | None = None,
    land_use_analysis: dict | None = None,
    accessibility_analysis: dict | None = None,
    beneficiary_score: float | None = None,
    weights: dict[str, float] | None = None,
) -> dict:
    """
    Compatibility wrapper that assembles the five raw factor scores for a
    single candidate and returns a single-candidate placeholder result.

    Full TOPSIS ranking across a set of candidates is performed by
    ``rank_candidates_topsis``; this helper exists so callers that still
    build candidates one at a time can pre-compute the factor inputs. The
    returned dict carries the five ``factor_scores`` used to build the
    decision matrix, plus the per-candidate availability metadata.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS.copy()

    if terrain_score is None:
        terrain_score = candidate.get("suitability_score")

    practical = practical_raw_score(soil_analysis, land_use_analysis, accessibility_analysis)

    factor_scores = {
        "terrain": terrain_score if isinstance(terrain_score, (int, float)) else None,
        "hydrology": hydrology_score if isinstance(hydrology_score, (int, float)) else None,
        "water_storage": water_storage_score if isinstance(water_storage_score, (int, float)) else None,
        "practical": practical,
        "beneficiary": beneficiary_score if isinstance(beneficiary_score, (int, float)) else None,
    }

    available = [k for k, v in factor_scores.items() if v is not None]
    unavailable = [k for k, v in factor_scores.items() if v is None]
    w = _normalize_weights(available, weights)

    return {
        "candidate_id": candidate.get("candidate_id"),
        "rank": candidate.get("rank"),
        "final_score": None,  # populated by rank_candidates_topsis
        "factor_scores": factor_scores,
        "available_factors": available,
        "unavailable_factors": unavailable,
        "weights_used": _describe_weights(w),
        "confidence": _confidence_label(len(available)),
    }