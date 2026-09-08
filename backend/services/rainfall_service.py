from __future__ import annotations
from collections import defaultdict
from datetime import date
import httpx

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def get_historical_rainfall(
    latitude: float,
    longitude: float,
    years: int = 5,
) -> dict:
    """Return historical annual rainfall totals and their average."""
    years = max(1, min(30, int(years)))
    today = date.today()
    last_year = today.year - 1
    first_year = last_year - years + 1

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": f"{first_year}-01-01",
        "end_date": f"{last_year}-12-31",
        "daily": "precipitation_sum",
        "timezone": "UTC",
    }

    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.get(OPEN_METEO_ARCHIVE_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()

        times = payload.get("daily", {}).get("time", [])
        precip = payload.get("daily", {}).get("precipitation_sum", [])

        if not times or not precip:
            raise ValueError("Rainfall API returned no usable daily precipitation data.")

        totals = defaultdict(float)
        valid_days = defaultdict(int)

        for t_str, val in zip(times, precip):
            if val is not None:
                yr = int(t_str.split("-")[0])
                totals[yr] += float(val)
                valid_days[yr] += 1

        yearly = []
        for yr in sorted(totals.keys()):
            yearly.append({
                "year": yr,
                "rainfall_mm": round(totals[yr], 1),
                "valid_days": valid_days[yr],
            })

        if not yearly:
            raise ValueError("Rainfall API returned no usable precipitation values.")

        avg_rainfall = round(sum(y["rainfall_mm"] for y in yearly) / len(yearly), 1)

        return {
            "available": True,
            "source": "Open-Meteo Historical Weather API",
            "latitude": latitude,
            "longitude": longitude,
            "period": f"{first_year}-{last_year}",
            "average_annual_rainfall_mm": avg_rainfall,
            "yearly": yearly,
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "source": "Open-Meteo Historical Weather API",
            "latitude": latitude,
            "longitude": longitude,
            "period": "unavailable",
            "average_annual_rainfall_mm": 800.0,
            "yearly": [],
            "error": str(exc),
        }
