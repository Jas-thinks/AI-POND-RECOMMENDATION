"""
Ground-truth validation for the pond-site ranking pipeline.

This script is NOT part of the API request path. It is a standalone
verification tool that feeds known real-world sites through the exact same
Stage-1-onward pipeline used for real requests (DEM fetch -> contour
generation -> analyze_contour_file -> TOPSIS ranking) and checks whether
the ranking "agrees" with reality:

  * existing_pond points should land in the top 30% of the TOPSIS ranking;
  * known_bad_site points should be hard-excluded OR land in the bottom 30%.

Run it directly:

    python -m backend.validation.ground_truth_check

It writes a `validation_report.json` (next to this file) and prints a
summary table.

IMPORTANT: The GROUND_TRUTH list below is intentionally EMPTY. Real
coordinates (known existing ponds and known-bad sites near IIT Bhilai, or
from the reference project) must be supplied manually — nothing is
fabricated or guessed here.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pyproj import Geod

# ---------------------------------------------------------------------------
# Ground-truth reference points.
#
# Format: {lat, lon, label, note}
#     label: "existing_pond"  -> should rank in the top 30%.
#            "known_bad_site" -> should be hard-excluded or rank in the
#                                bottom 30%.
#
# TODO: fill in with real coordinates (existing ponds and known-bad sites
# near IIT Bhilai, or from your friend's reference project).
# ---------------------------------------------------------------------------
GROUND_TRUTH: list[dict] = []

# Directory where this module lives; the report is written next to it.
MODULE_DIR = Path(__file__).resolve().parent
WORK_DIR = MODULE_DIR / "_work"
REPORT_PATH = MODULE_DIR / "validation_report.json"

# Ranking threshold (fraction of candidates ranked above) treated as "top".
TOP_PERCENTILE = 0.30
BOTTOM_PERCENTILE = 1.0 - TOP_PERCENTILE

# Pipeline parameters mirroring the API defaults, so validation uses the
# same configuration as real requests.
RESOLUTION_M = 30.0
MAX_CANDIDATES = 20
POND_RADIUS_M = 40.0
MAX_POND_DEPTH_M = 3.0
ANALYSIS_RADIUS_M = 3000.0
CONTOUR_INTERVAL_M = 5.0
RUNOFF_COEFFICIENT = 0.30
RAINFALL_YEARS = 5


@dataclass
class PointRun:
    lat: float
    lon: float
    label: str
    note: str
    excluded: bool = False
    excluded_reason: str | None = None
    nearest_candidate_rank: int | None = None
    candidate_count: int = 0
    closeness_score: float | None = None
    rank_percentile: float | None = None
    candidate_id: int | None = None
    error: str | None = None


def _bbox_from_center(latitude: float, longitude: float, radius_m: float):
    """Approximate square WGS84 bounding box around a point (metres)."""
    geod = Geod(ellps="WGS84")
    east_lon, _, _ = geod.fwd(longitude, latitude, 90.0, radius_m)
    west_lon, _, _ = geod.fwd(longitude, latitude, 270.0, radius_m)
    _, north_lat, _ = geod.fwd(longitude, latitude, 0.0, radius_m)
    _, south_lat, _ = geod.fwd(longitude, latitude, 180.0, radius_m)
    return south_lat, north_lat, west_lon, east_lon


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2.0) ** 2
    )
    return 2.0 * r * math.asin(math.sqrt(a))


def run_point(point: dict, work_dir: Path) -> PointRun:
    """Run a single ground-truth point through the real pipeline."""
    lat = float(point["lat"])
    lon = float(point["lon"])
    label = point["label"]
    note = point.get("note", "")

    run = PointRun(lat=lat, lon=lon, label=label, note=note)

    # ------------------------------------------------------------------
    # 1. Bounding box around the point (Mode A geometry).
    # ------------------------------------------------------------------
    south, north, west, east = _bbox_from_center(lat, lon, ANALYSIS_RADIUS_M)

    # ------------------------------------------------------------------
    # Resolve the service functions lazily so importing this module never
    # triggers network work or heavy imports at module load.
    # ------------------------------------------------------------------
    from backend.services.analysis_service import analyze_contour_file
    from backend.services.contour_generation_service import generate_contours_from_dem
    from backend.services.opentopography_service import fetch_dem_for_bounding_box

    dem_path = work_dir / f"{lat:.6f}_{lon:.6f}_dem.tif"
    kml_path = work_dir / f"{lat:.6f}_{lon:.6f}_contours.kml"
    geojson_path = work_dir / f"{lat:.6f}_{lon:.6f}_contours.geojson"

    try:
        # ------------------------------------------------------------------
        # 2. Fetch DEM and generate contours (Mode A Stage 1).
        # ------------------------------------------------------------------
        fetch_dem_for_bounding_box(
            south=south,
            north=north,
            west=west,
            east=east,
            output_path=dem_path,
        )
        generated = generate_contours_from_dem(
            dem_path=dem_path,
            kml_path=kml_path,
            geojson_path=geojson_path,
            contour_interval_m=CONTOUR_INTERVAL_M,
        )

        # ------------------------------------------------------------------
        # 3. Run the shared pipeline (Stage 1 onward is identical to a real
        #    analyzeContour / analyzeLocation request).
        # ------------------------------------------------------------------
        result = analyze_contour_file(
            file_bytes=generated.kml_path.read_bytes(),
            filename=generated.kml_path.name,
            resolution_m=RESOLUTION_M,
            max_candidates=MAX_CANDIDATES,
            rainfall_years=RAINFALL_YEARS,
            runoff_coefficient=RUNOFF_COEFFICIENT,
            pond_radius_m=POND_RADIUS_M,
            max_pond_depth_m=MAX_POND_DEPTH_M,
        )

        candidates = result.get("candidates") or []
        run.candidate_count = len(candidates)

        if not candidates:
            # The pipeline produced no candidates; grounds cannot be scored,
            # which is recorded (not a hard exclusion by itself).
            run.excluded = False
            run.excluded_reason = "no candidates produced for this location"
            return run

        # ------------------------------------------------------------------
        # 4. Find the candidate nearest to the ground-truth point — that is
        #    how the pipeline scores this exact real-world location.
        # ------------------------------------------------------------------
        best = min(
            candidates,
            key=lambda c: _haversine_m(lat, lon, c["latitude"], c["longitude"]),
        )
        run.candidate_id = best.get("candidate_id")
        run.closeness_score = best.get("multi_factor_score", {}).get("final_score")
        run.nearest_candidate_rank = best.get("rank")
        if run.candidate_count > 0 and run.nearest_candidate_rank is not None:
            # rank_percentile = fraction of candidates ranked ABOVE it, so
            # the #1 candidate has percentile 0.0 and the last has ~1.0.
            run.rank_percentile = (
                (run.nearest_candidate_rank - 1) / run.candidate_count
            )
        return run

    except ValueError as exc:
        # Hard-exclusion / no-buildable-land path: analyse_contour_file
        # raises ValueError when hard constraints remove everything.
        message = str(exc).lower()
        run.excluded = True
        run.excluded_reason = message
        return run
    except Exception as exc:  # noqa: BLE001 - report any pipeline failure
        run.error = f"{type(exc).__name__}: {exc}"
        return run


def summarise(runs: list[PointRun]) -> dict:
    """Compute aggregate validation metrics from per-point runs."""
    pond_runs = [r for r in runs if r.label == "existing_pond"]
    bad_runs = [r for r in runs if r.label == "known_bad_site"]

    def top30(r: PointRun) -> bool:
        return (
            not r.excluded
            and r.rank_percentile is not None
            and r.rank_percentile < TOP_PERCENTILE
        )

    def bottom30(r: PointRun) -> bool:
        return (
            not r.excluded
            and r.rank_percentile is not None
            and r.rank_percentile >= BOTTOM_PERCENTILE
        )

    ponds_in_top = [r for r in pond_runs if top30(r)]
    bads_caught = [
        r for r in bad_runs if r.excluded or bottom30(r)
    ]

    return {
        "existing_pond_count": len(pond_runs),
        "existing_pond_in_top_30pct": len(ponds_in_top),
        "existing_pond_top_30pct_rate": (
            len(ponds_in_top) / len(pond_runs) if pond_runs else None
        ),
        "known_bad_site_count": len(bad_runs),
        "known_bad_site_excluded_or_bottom_30pct": len(bads_caught),
        "known_bad_site_catch_rate": (
            len(bads_caught) / len(bad_runs) if bad_runs else None
        ),
    }


def print_summary(runs: list[PointRun], summary: dict) -> None:
    print("\n=== GROUND-TRUTH VALIDATION REPORT ===")
    print(
        f"{'Lat':>10} {'Lon':>10} {'Label':<14} {'Excl':<5} "
        f"{'C_i':>8} {'Rank':>5} {'Pctile':>7} {'Error'}"
    )
    print("-" * 80)
    for r in runs:
        excl = "YES" if r.excluded else "no"
        ci = f"{r.closeness_score:.3f}" if r.closeness_score is not None else "-"
        rk = str(r.nearest_candidate_rank) if r.nearest_candidate_rank is not None else "-"
        pct = f"{r.rank_percentile:.2f}" if r.rank_percentile is not None else "-"
        err = (r.error or r.excluded_reason or "")[:40]
        print(
            f"{r.lat:10.5f} {r.lon:10.5f} {r.label:<14} {excl:<5} "
            f"{ci:>8} {rk:>5} {pct:>7} {err}"
        )
    print("-" * 80)
    print("Existing ponds in top 30%: "
          f"{summary['existing_pond_in_top_30pct']}/{summary['existing_pond_count']}"
          "  (rate={:.0%})".format(summary["existing_pond_top_30pct_rate"] or 0))
    print("Known-bad sites excluded or bottom 30%: "
          f"{summary['known_bad_site_excluded_or_bottom_30pct']}/"
          f"{summary['known_bad_site_count']}"
          "  (rate={:.0%})".format(summary["known_bad_site_catch_rate"] or 0))
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the pond-site ranking pipeline against "
                    "known real-world locations."
    )
    parser.add_argument(
        "--points",
        type=str,
        default=None,
        help="Optional path to a JSON list of {lat, lon, label, note} dicts. "
             "When omitted, the embedded GROUND_TRUTH list is used.",
    )
    args = parser.parse_args(argv)

    points = GROUND_TRUTH
    if args.points:
        with open(args.points, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, list):
            points = loaded
        else:
            print(f"Error: {args.points} must contain a JSON list.", file=sys.stderr)
            return 2

    if not points:
        print(
            "No ground-truth points are configured yet.\n"
            "Fill in the GROUND_TRUTH list in this file (or pass --points) "
            "with real existing ponds and known-bad sites, then re-run.",
            file=sys.stderr,
        )
        return 1

    work_dir = WORK_DIR
    work_dir.mkdir(parents=True, exist_ok=True)

    runs: list[PointRun] = []
    for i, point in enumerate(points, start=1):
        print(f"[{i}/{len(points)}] Running {point.get('label')} "
              f"at {point.get('lat')}, {point.get('lon')} ...")
        t0 = time.perf_counter()
        run = run_point(point, work_dir)
        run.error = (run.error or "")
        elapsed = time.perf_counter() - t0
        print(f"    -> {'excluded' if run.excluded else 'scored'} "
              f"in {elapsed:.1f}s")
        runs.append(run)

    summary = summarise(runs)
    print_summary(runs, summary)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "thresholds": {
            "top_percentile": TOP_PERCENTILE,
            "bottom_percentile": BOTTOM_PERCENTILE,
        },
        "summary": summary,
        "points": [
            {
                "lat": r.lat,
                "lon": r.lon,
                "label": r.label,
                "note": r.note,
                "excluded": r.excluded,
                "excluded_reason": r.excluded_reason,
                "candidate_count": r.candidate_count,
                "candidate_id": r.candidate_id,
                "nearest_candidate_rank": r.nearest_candidate_rank,
                "closeness_score": r.closeness_score,
                "rank_percentile": r.rank_percentile,
                "error": r.error,
            }
            for r in runs
        ],
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Report written to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())