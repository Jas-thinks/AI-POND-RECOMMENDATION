from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from backend.schemas.response import AnalysisResponse
from backend.services.analysis_service import analyze_contour_file

router = APIRouter()


@router.post("/analyzeContour", response_model=AnalysisResponse)
async def analyze_contour(
    file: UploadFile = File(...),
    resolution_m: float = Form(10.0),
    max_candidates: int = Form(20),
    rainfall_years: int = Form(5),
    runoff_coefficient: float = Form(0.30),
    pond_radius_m: float = Form(40.0),
    max_pond_depth_m: float = Form(3.0),
):
    filename = (file.filename or "").lower()
    if not (filename.endswith(".kml") or filename.endswith(".kmz")):
        raise HTTPException(
            status_code=400,
            detail="Only .kml or .kmz files are allowed.",
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

    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    try:
        return analyze_contour_file(
            raw,
            file.filename,
            resolution_m=resolution_m,
            max_candidates=max_candidates,
            rainfall_years=rainfall_years,
            runoff_coefficient=runoff_coefficient,
            pond_radius_m=pond_radius_m,
            max_pond_depth_m=max_pond_depth_m,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Terrain analysis failed: {exc}",
        ) from exc
