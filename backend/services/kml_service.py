from __future__ import annotations
from dataclasses import dataclass
import io
import zipfile
import xml.etree.ElementTree as ET


@dataclass
class ContourLine:
    elevation_m: float
    coordinates: list[tuple[float, float]]


@dataclass
class ParsedContourMap:
    contours: list[ContourLine]
    boundary_wgs84: list[tuple[float, float]]


def _extract_kml_bytes(file_bytes: bytes, filename: str) -> bytes:
    if filename.lower().endswith(".kmz"):
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            kml_files = [f for f in zf.namelist() if f.lower().endswith(".kml")]
            if not kml_files:
                raise ValueError("KMZ archive does not contain any .kml file.")
            return zf.read(kml_files[0])
    return file_bytes


def _parse_coordinates(text: str) -> list[tuple[float, float]]:
    coords = []
    for part in text.strip().split():
        tokens = part.split(",")
        if len(tokens) >= 2:
            try:
                lon = float(tokens[0])
                lat = float(tokens[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


def parse_contour_file(file_bytes: bytes, filename: str) -> ParsedContourMap:
    kml_bytes = _extract_kml_bytes(file_bytes, filename)
    root = ET.fromstring(kml_bytes)

    contours = []
    boundary_coords = []

    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "Placemark":
            name_elem = None
            coords_elem = None

            for child in elem.iter():
                ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if ctag == "name" and name_elem is None:
                    name_elem = child
                elif ctag == "coordinates":
                    coords_elem = child

            if name_elem is not None and name_elem.text:
                try:
                    elev = float(name_elem.text.strip())
                except ValueError:
                    elev = None

                if elev is not None and coords_elem is not None and coords_elem.text:
                    pts = _parse_coordinates(coords_elem.text)
                    if len(pts) >= 2:
                        contours.append(ContourLine(elevation_m=elev, coordinates=pts))

            if name_elem is not None and name_elem.text and name_elem.text.strip().lower() == "land":
                if coords_elem is not None and coords_elem.text:
                    pts = _parse_coordinates(coords_elem.text)
                    if len(pts) >= 3:
                        boundary_coords = pts

    if not contours:
        raise ValueError("No valid elevation contour LineStrings were found in the uploaded KML/KMZ.")

    if not boundary_coords:
        all_pts = [pt for c in contours for pt in c.coordinates]
        lons = [p[0] for p in all_pts]
        lats = [p[1] for p in all_pts]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        boundary_coords = [
            (min_lon, min_lat),
            (max_lon, min_lat),
            (max_lon, max_lat),
            (min_lon, max_lat),
            (min_lon, min_lat),
        ]

    return ParsedContourMap(contours=contours, boundary_wgs84=boundary_coords)
