from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pyproj
from pyproj import CRS, Transformer
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from scipy.interpolate import griddata
from shapely.geometry import Polygon, mapping
from shapely.ops import transform as shapely_transform
from backend.services.kml_service import ParsedContourMap


@dataclass
class TerrainGrid:
    dem: np.ndarray
    valid_mask: np.ndarray
    resolution_m: float
    transform: any
    to_wgs84: Transformer
    from_wgs84: Transformer
    boundary_projected: Polygon
    xs: np.ndarray
    ys: np.ndarray


def _utm_crs(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) / 6) + 1
    hemisphere = 32600 if lat >= 0 else 32700
    return CRS.from_epsg(hemisphere + zone)


def _sample_contour_points(parsed: ParsedContourMap, max_points: int = 60000):
    total_pts = sum(len(c.coordinates) for c in parsed.contours)
    step = max(1, total_pts // max_points) if max_points > 0 else 1
    pts = []
    elevs = []
    for c in parsed.contours:
        for i, coord in enumerate(c.coordinates):
            if i % step == 0:
                pts.append(coord)
                elevs.append(c.elevation_m)
    return np.asarray(pts), np.asarray(elevs)


def build_dem(
    parsed: ParsedContourMap,
    resolution_m: float = 10.0,
    max_cells: int = 250000,
) -> TerrainGrid:
    poly = Polygon(parsed.boundary_wgs84)
    if not poly.is_valid:
        poly = poly.buffer(0)
    centroid = poly.centroid
    crs_utm = _utm_crs(centroid.x, centroid.y)
    from_wgs84 = Transformer.from_crs("EPSG:4326", crs_utm, always_xy=True)
    to_wgs84 = Transformer.from_crs(crs_utm, "EPSG:4326", always_xy=True)

    poly_proj = shapely_transform(from_wgs84.transform, poly)
    minx, miny, maxx, maxy = poly_proj.bounds

    width_m = maxx - minx
    height_m = maxy - miny

    rows = int(np.ceil(height_m / resolution_m))
    cols = int(np.ceil(width_m / resolution_m))

    if rows * cols > max_cells:
        scale = np.sqrt((rows * cols) / max_cells)
        resolution_m = float(resolution_m * scale)
        rows = int(np.ceil(height_m / resolution_m))
        cols = int(np.ceil(width_m / resolution_m))

    transform = from_origin(minx, maxy, resolution_m, resolution_m)

    xs = np.arange(cols) * resolution_m + minx + resolution_m / 2.0
    ys = maxy - np.arange(rows) * resolution_m - resolution_m / 2.0
    grid_x, grid_y = np.meshgrid(xs, ys)

    geom = [mapping(poly_proj)]
    inv_mask = geometry_mask(geom, out_shape=(rows, cols), transform=transform, invert=True)

    pts, elevs = _sample_contour_points(parsed)
    pts_x, pts_y = from_wgs84.transform(pts[:, 0], pts[:, 1])
    points = np.column_stack((pts_x, pts_y))

    dem = griddata(points, elevs, (grid_x, grid_y), method="cubic")
    if np.isnan(dem[inv_mask]).any():
        dem_lin = griddata(points, elevs, (grid_x, grid_y), method="linear")
        dem = np.where(np.isnan(dem), dem_lin, dem)
    if np.isnan(dem[inv_mask]).any():
        dem_near = griddata(points, elevs, (grid_x, grid_y), method="nearest")
        dem = np.where(np.isnan(dem), dem_near, dem)

    dem[~inv_mask] = np.nan

    return TerrainGrid(
        dem=dem,
        valid_mask=inv_mask,
        resolution_m=resolution_m,
        transform=transform,
        to_wgs84=to_wgs84,
        from_wgs84=from_wgs84,
        boundary_projected=poly_proj,
        xs=xs,
        ys=ys,
    )
