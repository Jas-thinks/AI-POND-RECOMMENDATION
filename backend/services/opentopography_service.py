from __future__ import annotations
import os
import shutil
import hashlib
from pathlib import Path
import httpx
import rasterio

OPEN_TOPOGRAPHY_URL = "https://portal.opentopography.org/API/globaldem"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "opentopography"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _get_api_key() -> str:
    key = os.getenv("OPENTOPOGRAPHY_API_KEY", "").strip()
    if not key:
        return "demo"
    return key


def _validate_geotiff(path: Path) -> None:
    try:
        with rasterio.open(path) as ds:
            if ds.count < 1 or ds.width < 1 or ds.height < 1:
                raise ValueError("Downloaded DEM has invalid dimensions.")
    except Exception as exc:
        raise RuntimeError(
            "OpenTopography response could not be opened as a GeoTIFF. "
            "Check your API key, bounding box and API quota."
        ) from exc


def download_global_dem(
    south: float,
    north: float,
    west: float,
    east: float,
    destination: Path,
    dem_type: str = "COP30",
) -> dict:
    if south >= north or west >= east:
        raise ValueError("Invalid DEM bounding box.")

    api_key = _get_api_key()
    cache_str = f"{dem_type}|{south:.7f}|{north:.7f}|{west:.7f}|{east:.7f}"
    cache_key = hashlib.sha1(cache_str.encode("utf-8")).hexdigest()[:20]
    cache_path = CACHE_DIR / f"{dem_type.lower()}_{cache_key}.tif"

    if cache_path.exists():
        try:
            _validate_geotiff(cache_path)
            shutil.copy2(cache_path, destination)
            return {
                "dataset": f"OpenTopography {dem_type}",
                "source": "OpenTopography Global DEM API (cached)",
                "cached": True,
                "south": south,
                "north": north,
                "west": west,
                "east": east,
            }
        except Exception:
            pass

    params = {
        "demtype": dem_type,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }

    try:
        with httpx.Client(timeout=90.0, follow_redirects=True) as client:
            resp = client.get(OPEN_TOPOGRAPHY_URL, params=params)
            resp.raise_for_status()
            cache_path.write_bytes(resp.content)
            _validate_geotiff(cache_path)
            shutil.copy2(cache_path, destination)
            return {
                "dataset": f"OpenTopography {dem_type}",
                "source": "OpenTopography Global DEM API",
                "cached": False,
                "south": south,
                "north": north,
                "west": west,
                "east": east,
            }
    except Exception as exc:
        raise RuntimeError(f"OpenTopography DEM download failed: {exc}") from exc


def fetch_dem_for_bounding_box(
    south: float,
    north: float,
    west: float,
    east: float,
    output_path: Path,
) -> dict:
    return download_global_dem(
        south=south,
        north=north,
        west=west,
        east=east,
        destination=output_path,
    )
