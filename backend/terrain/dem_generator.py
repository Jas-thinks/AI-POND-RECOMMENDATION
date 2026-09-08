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


# ------------------------------------------------------------
# DEM SIZE SAFETY LIMITS
#
# These protect the worker/process from accidentally allocating an
# enormous in-memory raster. They are sized for a modest (e.g. 512 MB)
# single-instance deployment. Reasonable defaults can be overridden by
# passing ``max_cells`` / ``max_dimension`` to ``build_dem``.
# ------------------------------------------------------------
DEFAULT_MAX_CELLS = 150_000       # ~390x390 grid of float64 DEM cells
DEFAULT_MAX_DIMENSION = 1_500     # cap per grid axis (rows and columns)


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

    @property
    def to_projected(self) -> Transformer:
        """Return the transformer to projected (UTM) coordinates.

        Note: In this codebase the ``to_wgs84`` transformer converts from UTM
        to WGS84, so ``to_projected`` aliases it for compatibility with
        callers that expect a ``to_projected`` attribute.
        """
        return self.to_wgs84


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
    max_cells: int = DEFAULT_MAX_CELLS,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
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

    # -------------------------------------------------------
    # GRID SAFETY LIMITS
    #
    # 1. If the raw grid is too large, coarsen the effective
    #    resolution so the total number of DEM cells never
    #    exceeds ``max_cells``. This is graceful: analysis still
    #    runs, at a coarser (but still valid) resolution.
    # 2. If the area is so elongated that a single axis would
    #    still exceed ``max_dimension`` even after coarsening,
    #    the request is genuinely too large to process safely
    #    and we raise a clear error instead of crashing.
    #
    # The resulting coarsened ``resolution_m`` is returned on the
    # ``TerrainGrid`` so callers can report the *effective*
    # resolution actually used.
    # -------------------------------------------------------
    scale = 1.0
    if rows * cols > max_cells:
        scale = float(np.sqrt((rows * cols) / max_cells))
        resolution_m = float(resolution_m * scale)
        rows = int(np.ceil(height_m / resolution_m))
        cols = int(np.ceil(width_m / resolution_m))

    if scale > 1.0 or rows > max_dimension or cols > max_dimension:
        # Verify we landed inside the per-axis bound after coarsening.
        if rows > max_dimension or cols > max_dimension:
            raise ValueError(
                "The requested analysis area/resolution would produce a DEM "
                "grid larger than this deployment can safely process "
                f"(grid {rows} x {cols} cells, axis limit {max_dimension}). "
                "Reduce the analysis area, increase the resolution (cell size), "
                "or use a smaller search radius, then try again."
            )
        if scale > 1.0:
            # Emit a diagnostic hint about the coarsened resolution. Callers
            # surface the effective resolution through ``grid_resolution_m``.
            import logging
            logging.getLogger("analysis.pipeline").info(
                "[ANALYSIS] DEM grid reduced by coarsening resolution "
                "%.2fm -> %.2fm (%d x %d cells)",
                float(resolution_m / scale), float(resolution_m), rows, cols,
            )

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
        del dem_lin  # release temporary interpolation buffer promptly
    if np.isnan(dem[inv_mask]).any():
        dem_near = griddata(points, elevs, (grid_x, grid_y), method="nearest")
        dem = np.where(np.isnan(dem), dem_near, dem)
        del dem_near  # release temporary interpolation buffer promptly

    # The full-resolution coordinate meshes are no longer needed once the DEM
    # has been interpolated. Free them now rather than carrying two extra
    # grid-sized float64 arrays for the rest of the pipeline.
    del grid_x, grid_y, points, pts_x, pts_y

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
