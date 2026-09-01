from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.contour_routes import router as contour_router
from backend.api.location_routes import router as location_router


BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BASE_DIR / "frontend"
GENERATED_DIR = BASE_DIR / "data" / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="AI-Based Village Pond Planning System",
    description="Terrain, catchment, land filtering, rainfall and pond-site planning API.",
    version="0.3.0",
)

app.include_router(contour_router, prefix="/api", tags=["Contour Analysis"])
app.include_router(location_router, prefix="/api", tags=["Automatic Location Analysis"])

# Serve frontend CSS and JS at root so index.html relative paths work
app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")
app.mount("/generated", StaticFiles(directory=str(GENERATED_DIR)), name="generated")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "healthy"}
