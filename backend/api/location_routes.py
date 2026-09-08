from pathlib import Path
import uuid
from fastapi import APIRouter, Form, HTTPException
from backend.schemas.response import AnalysisResponse
from backend.services.analysis_service import analyze_contour_file
from backend.services.contour_generation_service import generate_contours_from_dem
from backend.services.geocoding_service import resolve_location
from backend.services.opentopography_service import fetch_dem_for_bounding_box

BASE_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = BASE_DIR / "data" / "generated"

router = APIRouter()


@router.post("/analyzeLocation", response_model=AnalysisResponse)
async def analyze_location(
    location_name: str = Form(...),
    analysis_radius_m: float = Form(3000.0),
    contour_interval_m: float = Form(5.0),
    resolution_m: float = Form(30.0),
    max_candidates: int = Form(20),
    rainfall_years: int = Form(5),
    runoff_coefficient: float = Form(0.30),
    pond_radius_m: float = Form(40.0),
    max_pond_depth_m: float = Form(3.0),
):
    if not location_name.strip():
        raise HTTPException(
            status_code=400,
            detail="location_name cannot be empty.",
        )
    if not (500.0 <= analysis_radius_m <= 10000.0):
        raise HTTPException(
            status_code=400,
            detail="analysis_radius_m must be between 500 and 10000 metres.",
        )
    if not (1.0 <= contour_interval_m <= 20.0):
        raise HTTPException(
            status_code=400,
            detail="contour_interval_m must be between 1 and 20 metres.",
        )
    if not (2.0 <= resolution_m <= 100.0):
        raise HTTPException(
            status_code=400,
            detail="resolution_m must be between 2 and 100 metres.",
        )
    if not (1 <= max_candidates <= 50):
        raise HTTPException(
            status_code=400,
            detail="max_candidates must be between 1 and 50.",
        )
    if not (1 <= rainfall_years <= 30):
        raise HTTPException(
            status_code=400,
            detail="rainfall_years must be between 1 and 30.",
        )
    if not (0.0 <= runoff_coefficient <= 1.0):
        raise HTTPException(
            status_code=400,
            detail="runoff_coefficient must be between 0 and 1.",
        )
    if not (10.0 <= pond_radius_m <= 150.0):
        raise HTTPException(
            status_code=400,
            detail="pond_radius_m must be between 10 and 150 metres.",
        )
    if not (1.0 <= max_pond_depth_m <= 6.0):
        raise HTTPException(
            status_code=400,
            detail="max_pond_depth_m must be between 1 and 6 metres.",
        )

    try:
        place = resolve_location(location_name, radius_m=analysis_radius_m)
        job_id = uuid.uuid4().hex[:12]
        job_dir = GENERATED_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        dem_path = job_dir / "source_dem.tif"
        kml_path = job_dir / "generated_contours.kml"
        geojson_path = job_dir / "generated_contours.geojson"

        dem_info = fetch_dem_for_bounding_box(
            south=place.south,
            north=place.north,
            west=place.west,
            east=place.east,
            output_path=dem_path,
        )

        generated = generate_contours_from_dem(
            dem_path=dem_path,
            kml_path=kml_path,
            geojson_path=geojson_path,
            contour_interval_m=contour_interval_m,
        )

        effective_resolution_m = max(float(resolution_m), 30.0)

        result = analyze_contour_file(
            file_bytes=kml_path.read_bytes(),
            filename=kml_path.name,
            resolution_m=effective_resolution_m,
            max_candidates=max_candidates,
            rainfall_years=rainfall_years,
            runoff_coefficient=runoff_coefficient,
            pond_radius_m=pond_radius_m,
            max_pond_depth_m=max_pond_depth_m,
        )

        result["location_search"] = {
            "query": place.query,
            "resolved_name": place.display_name,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "analysis_radius_m": analysis_radius_m,
            "bounding_box": {
                "south": place.south,
                "north": place.north,
                "west": place.west,
                "east": place.east,
            },
        }

        source_dem_dict = dict(dem_info)
        source_dem_dict.update({
            "vertical_source": "Copernicus COP30 DSM via OpenTopography",
            "nominal_horizontal_resolution_m": 30,
            "analysis_grid_resolution_m": effective_resolution_m,
            "resolution_note": (
                "The analysis grid is not allowed to be finer than 30 m for COP30, "
                "to avoid implying spatial accuracy that the source DEM does not provide."
            ),
        })
        result["source_dem"] = source_dem_dict

        result["generated_contours"] = {
            "contour_interval_m": generated.contour_interval_m,
            "minimum_elevation_m": generated.minimum_elevation_m,
            "maximum_elevation_m": generated.maximum_elevation_m,
            "contour_feature_count": generated.contour_feature_count,
            "geojson": generated.geojson,
        }

        result["generated_files"] = {
            "dem_url": f"/generated/{job_id}/source_dem.tif",
            "contour_kml_url": f"/generated/{job_id}/generated_contours.kml",
            "contour_geojson_url": f"/generated/{job_id}/generated_contours.geojson",
        }

        return result
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Location analysis failed: {exc}",
        ) from exc
