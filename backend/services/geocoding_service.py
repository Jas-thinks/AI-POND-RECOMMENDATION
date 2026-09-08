from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from pyproj import Geod


NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_SEARCH_URL = "https://geocoding-api.open-meteo.com/v1/search"
MAX_RETRIES = 3
RETRY_DELAY_S = 2.0


@dataclass
class GeocodedPlace:
    query: str
    display_name: str
    latitude: float
    longitude: float
    south: float
    north: float
    west: float
    east: float


def _bbox_from_center(latitude: float, longitude: float, radius_m: float):
    """Create an approximately square WGS84 bounding box around a point."""
    geod = Geod(ellps="WGS84")

    east_lon, _, _ = geod.fwd(longitude, latitude, 90.0, radius_m)
    west_lon, _, _ = geod.fwd(longitude, latitude, 270.0, radius_m)
    _, north_lat, _ = geod.fwd(longitude, latitude, 0.0, radius_m)
    _, south_lat, _ = geod.fwd(longitude, latitude, 180.0, radius_m)

    return south_lat, north_lat, west_lon, east_lon


def _generate_query_variants(query: str) -> list[str]:
    """Generate progressively shorter query variants for fallback geocoding.

    Tries to be forgiving when the user types a verbose address that Nominatim
    does not have indexed. The variants are deduplicated while preserving order.
    """
    original = query.strip()
    if not original:
        return []

    parts = [p.strip() for p in original.split(",") if p.strip()]
    variants: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        key = v.lower()
        if key and key not in seen:
            seen.add(key)
            variants.append(v)

    # 1) Full original query
    add(original)

    # 2) Drop trailing/leading generic country/state tokens progressively
    #    ("India", "Chhattisgarh", etc.) from the right.
    for i in range(len(parts) - 1, 0, -1):
        add(", ".join(parts[:i]))

    # 3) Drop the first token (often an institution name like "IIT").
    if len(parts) >= 2:
        add(", ".join(parts[1:]))

    # 4) Try with just the last 2 parts (city + state/country).
    if len(parts) >= 2:
        add(", ".join(parts[-2:]))

    # 5) Try with just the city name alone.
    if parts:
        add(parts[-1])

    # 6) Special case: if first token looks like an institution acronym
    #    ("IIT", "IIM", "NIT", "AIIMS") followed by a city, try replacing
    #    the acronym with the full form, which Nominatim often indexes.
    acronyms = {
        "iit": "Indian Institute of Technology",
        "iim": "Indian Institute of Management",
        "nit": "National Institute of Technology",
        "iiit": "Indian Institute of Information Technology",
        "aiims": "All India Institute of Medical Sciences",
    }
    if parts and parts[0].lower() in acronyms and len(parts) >= 2:
        expanded = acronyms[parts[0].lower()] + ", " + ", ".join(parts[1:])
        add(expanded)
        # Also try just the expanded acronym + last city/state
        if len(parts) >= 2:
            add(acronyms[parts[0].lower()] + ", " + parts[-1])

    return variants


def _nominatim_search(client: httpx.Client, q: str) -> list[dict]:
    params = {
        "q": q,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
        "accept-language": "en",
    }
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.get(NOMINATIM_SEARCH_URL, params=params)
            response.raise_for_status()
            results = response.json()
            return results if isinstance(results, list) else []
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError,
                httpx.WriteError, httpx.PoolTimeout, httpx.ReadTimeout,
                httpx.ConnectTimeout) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_S * (attempt + 1))
                continue
            raise
        except httpx.HTTPStatusError as exc:
            # 429 (rate limit) or 5xx -> retry
            if exc.response.status_code in (429, 500, 502, 503, 504):
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY_S * (attempt + 1))
                    continue
            raise
    if last_exc:
        raise last_exc
    return []


def _open_meteo_search(client: httpx.Client, q: str) -> list[dict]:
    """Fallback geocoder. Does not require User-Agent but is less detailed."""
    params = {"name": q, "count": 1, "language": "en", "format": "json"}
    response = client.get(OPEN_METEO_SEARCH_URL, params=params)
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", []) if isinstance(payload, dict) else []
    # Normalize to Nominatim-like dicts.
    return [
        {
            "lat": str(r["latitude"]),
            "lon": str(r["longitude"]),
            "display_name": r.get("name", q) + ", " + r.get("country", ""),
        }
        for r in results
    ]


def _search_with_fallback(q: str) -> list[dict]:
    """Try Nominatim with retries; fall back to Open-Meteo if Nominatim is unreachable."""
    headers = {
        "User-Agent": "VillagePondPlanningStudentProject/1.0 (academic project)"
    }
    try:
        with httpx.Client(timeout=20.0, headers=headers) as client:
            return _nominatim_search(client, q)
    except Exception as nominatim_exc:
        # If Nominatim is rate-limited or unreachable, try the Open-Meteo fallback.
        try:
            with httpx.Client(timeout=20.0) as client:
                results = _open_meteo_search(client, q)
                if results:
                    return results
        except Exception:
            pass
        # Re-raise the original Nominatim error so the user sees the real cause.
        raise nominatim_exc


def geocode_place(query: str, radius_m: float = 3000.0) -> GeocodedPlace:
    query = query.strip()
    if not query:
        raise ValueError("Location name cannot be empty.")

    if radius_m < 500 or radius_m > 20000:
        raise ValueError("Analysis radius must be between 500 m and 20,000 m.")

    variants = _generate_query_variants(query)

    last_error: Exception | None = None
    try:
        for variant in variants:
            try:
                results = _search_with_fallback(variant)
            except Exception as exc:
                last_error = exc
                # Network/HTTP error: try the next (shorter) variant.
                continue
            if results:
                result = results[0]
                try:
                    latitude = float(result["lat"])
                    longitude = float(result["lon"])
                except (KeyError, TypeError, ValueError):
                    continue

                south, north, west, east = _bbox_from_center(
                    latitude=latitude,
                    longitude=longitude,
                    radius_m=radius_m,
                )

                return GeocodedPlace(
                    query=query,
                    display_name=result.get("display_name", variant),
                    latitude=latitude,
                    longitude=longitude,
                    south=south,
                    north=north,
                    west=west,
                    east=east,
                )
    except Exception as exc:
        raise RuntimeError(f"Location geocoding failed: {exc}") from exc

    if last_error is not None:
        raise RuntimeError(
            f"Location geocoding failed for '{query}': {last_error}"
        ) from last_error

    raise ValueError(
        f"Location '{query}' was not found. Try a different or more general name, "
        "for example 'Bhilai, Chhattisgarh, India' or 'Raipur, Chhattisgarh, India'."
    )


resolve_location = geocode_place

