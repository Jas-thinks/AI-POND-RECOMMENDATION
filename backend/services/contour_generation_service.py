from __future__ import annotations
from dataclasses import dataclass
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from pyproj import Transformer
import rasterio
from skimage import measure

KML_NS = "http://www.opengis.net/kml/2.2"
ET.register_namespace("", KML_NS)


@dataclass
class GeneratedContours:
    kml_path: Path
    geojson_path: Path
    geojson: dict
    contour_interval_m: float
    minimum_elevation_m: float
    maximum_elevation_m: float
    contour_feature_count: int


def _wgs84_xy(transformer: Transformer, x: float, y: float) -> tuple[float, float]:
    lon, lat = transformer.transform(x, y)
    return float(lon), float(lat)


def _raster_boundary_wgs84(dataset, transformer: Transformer) -> list[tuple[float, float]]:
    b = dataset.bounds
    corners_src = [
        (b.left, b.bottom),
        (b.right, b.bottom),
        (b.right, b.top),
        (b.left, b.top),
        (b.left, b.bottom),
    ]
    return [_wgs84_xy(transformer, x, y) for x, y in corners_src]


def generate_contours_from_dem(
    dem_path: Path,
    kml_path: Path,
    geojson_path: Path,
    contour_interval_m: float = 5.0,
) -> GeneratedContours:
    if contour_interval_m <= 0:
        raise ValueError("Contour interval must be greater than zero.")

    with rasterio.open(dem_path) as dataset:
        dem_data = dataset.read(1, masked=True)
        source_crs = dataset.crs
        transform = dataset.transform

        values = dem_data.filled(np.nan).astype(np.float64)
        valid = values[np.isfinite(values)]

        if not np.any(valid):
            raise ValueError("DEM contains no valid elevation values.")

        minimum = float(np.nanmin(valid))
        maximum = float(np.nanmax(valid))

        if math.isclose(minimum, maximum):
            raise ValueError("DEM is essentially flat; contours cannot be generated.")

        to_wgs84 = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)

        start_level = math.ceil(minimum / contour_interval_m) * contour_interval_m
        levels = np.arange(start_level, maximum, contour_interval_m)

        kml_root = ET.Element(f"{{{KML_NS}}}kml")
        document = ET.SubElement(kml_root, f"{{{KML_NS}}}Document")
        folder = ET.SubElement(document, f"{{{KML_NS}}}Folder")
        fname = ET.SubElement(folder, f"{{{KML_NS}}}name")
        fname.text = "Generated contours"

        features = []
        contour_count = 0

        for level in levels:
            paths = measure.find_contours(values, float(level))
            for path in paths:
                coords_wgs84 = []
                for row, col in path:
                    x, y = transform * (col + 0.5, row + 0.5)
                    lon, lat = _wgs84_xy(to_wgs84, x, y)
                    coords_wgs84.append((lon, lat))

                if len(coords_wgs84) < 2:
                    continue

                contour_count += 1

                pm = ET.SubElement(folder, f"{{{KML_NS}}}Placemark")
                pm_name = ET.SubElement(pm, f"{{{KML_NS}}}name")
                pm_name.text = f"{level:.2f}"

                ext = ET.SubElement(pm, f"{{{KML_NS}}}ExtendedData")
                data_el = ET.SubElement(ext, f"{{{KML_NS}}}Data", {"name": "elevation_m"})
                val_el = ET.SubElement(data_el, f"{{{KML_NS}}}value")
                val_el.text = f"{level:.2f}"

                ls = ET.SubElement(pm, f"{{{KML_NS}}}LineString")
                tess = ET.SubElement(ls, f"{{{KML_NS}}}tessellate")
                tess.text = "1"
                coords_el = ET.SubElement(ls, f"{{{KML_NS}}}coordinates")
                coords_el.text = " ".join(f"{lon:.7f},{lat:.7f},{level:.2f}" for lon, lat in coords_wgs84)

                features.append({
                    "type": "Feature",
                    "properties": {
                        "elevation_m": float(level),
                        "name": f"{level:.2f}",
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[lon, lat] for lon, lat in coords_wgs84],
                    },
                })

        boundary_wgs84 = _raster_boundary_wgs84(dataset, to_wgs84)
        bpm = ET.SubElement(folder, f"{{{KML_NS}}}Placemark")
        bpm_name = ET.SubElement(bpm, f"{{{KML_NS}}}name")
        bpm_name.text = "land"
        poly = ET.SubElement(bpm, f"{{{KML_NS}}}Polygon")
        outer = ET.SubElement(poly, f"{{{KML_NS}}}outerBoundaryIs")
        ring = ET.SubElement(outer, f"{{{KML_NS}}}LinearRing")
        ring_coords = ET.SubElement(ring, f"{{{KML_NS}}}coordinates")
        ring_coords.text = " ".join(f"{lon:.7f},{lat:.7f},0" for lon, lat in boundary_wgs84)

        geojson = {
            "type": "FeatureCollection",
            "features": features,
        }

        if contour_count == 0:
            raise ValueError("No contours were produced. Try a smaller contour interval or a larger analysis area.")

        kml_path.parent.mkdir(parents=True, exist_ok=True)
        geojson_path.parent.mkdir(parents=True, exist_ok=True)

        tree = ET.ElementTree(kml_root)
        tree.write(kml_path, encoding="utf-8", xml_declaration=True)
        geojson_path.write_text(json.dumps(geojson), encoding="utf-8")

        return GeneratedContours(
            kml_path=kml_path,
            geojson_path=geojson_path,
            geojson=geojson,
            contour_interval_m=float(contour_interval_m),
            minimum_elevation_m=minimum,
            maximum_elevation_m=maximum,
            contour_feature_count=contour_count,
        )
