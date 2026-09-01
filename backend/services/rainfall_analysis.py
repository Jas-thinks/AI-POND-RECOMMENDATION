"""
Rainfall / climate analysis wrapper.

Wraps the existing get_historical_rainfall() to produce a derived
climate suitability score and seasonal metrics.

Data source: Open-Meteo Historical Weather Archive
(https://archive-api.open-meteo.com/) — no API key required.
"""

from __future__ import annotations

from typing import Any


def _classify_seasonality(monthly: list[float]) -> str:
    if not monthly or len(monthly) != 12:
        return "unknown"
    total = sum(monthly)
    if total <= 0:
        return "arid"
    monsoon_share = sum(monthly[5:9]) / total
    winter_share = sum(monthly[10:12] + monthly[:2]) / total
    if monsoon_share >= 0.7:
        return "strongly monsoonal"
    if monsoon_share >= 0.5:
        return "monsoonal"
    if winter_share >= 0.4:
        return "mediterranean"
    if max(monthly) - min(monthly) < 30:
        return "equable"
    return "mixed"


def _rainfall_reliability(yearly: list[dict[str, Any]]) -> str:
    if not yearly or len(yearly) < 2:
        return "insufficient data"
    values = [y["rainfall_mm"] for y in yearly if y.get("rainfall_mm") is not None]
    if len(values) < 2:
        return "insufficient data"
    mean = sum(values) / len(values)
    if mean <= 0:
        return "unknown"
    cv = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5 / mean
    if cv < 0.15:
        return "very high"
    if cv < 0.25:
        return "high"
    if cv < 0.40:
        return "moderate"
    return "low"


def _climate_score(annual_mm: float | None, reliability: str, seasonality: str) -> float | None:
    if annual_mm is None or annual_mm <= 0:
        return None
    if annual_mm >= 1500:
        base = 90.0
    elif annual_mm >= 1100:
        base = 82.0
    elif annual_mm >= 800:
        base = 72.0
    elif annual_mm >= 600:
        base = 60.0
    elif annual_mm >= 400:
        base = 45.0
    elif annual_mm >= 250:
        base = 30.0
    else:
        base = 18.0

    reliability_bonus = {"very high": 8.0, "high": 5.0, "moderate": 0.0, "low": -8.0}.get(reliability, 0.0)
    seasonality_bonus = {
        "strongly monsoonal": -3.0,
        "monsoonal": 0.0,
        "mediterranean": 0.0,
        "equable": 3.0,
        "mixed": 0.0,
        "arid": -10.0,
    }.get(seasonality, 0.0)
    return round(max(0.0, min(100.0, base + reliability_bonus + seasonality_bonus)), 1)


def get_climate_analysis(latitude: float, longitude: float, years: int = 5) -> dict:
    from backend.services.rainfall_service import get_historical_rainfall

    base = get_historical_rainfall(latitude, longitude, years=years)

    if not base.get("available"):
        return {
            "available": False,
            "source": "Open-Meteo Historical Weather API",
            "latitude": round(float(latitude), 6),
            "longitude": round(float(longitude), 6),
            "period": base.get("period"),
            "annual_rainfall_mm": None,
            "monsoon_rainfall_mm": None,
            "rainfall_reliability": "unknown",
            "seasonality": "unknown",
            "climate_score": None,
            "recommendation": (
                "Historical climate data is unavailable. "
                "Use local meteorological department records to estimate "
                "annual rainfall and seasonal distribution."
            ),
            "error": base.get("error"),
        }

    annual_mm = base.get("average_annual_rainfall_mm")
    yearly = base.get("yearly", [])

    monthly_rainfall_mm = None
    monsoon_mm = None
    monthly_section = base.get("monthly")
    if isinstance(monthly_section, list) and len(monthly_section) == 12:
        monthly = [float(m) for m in monthly_section]
        monthly_rainfall_mm = [round(m, 2) for m in monthly]
        monsoon_mm = round(sum(monthly[5:9]), 2)

    reliability = _rainfall_reliability(yearly)
    seasonality = _classify_seasonality(monthly_rainfall_mm) if monthly_rainfall_mm else "unknown"
    score = _climate_score(annual_mm, reliability, seasonality)

    if score is None:
        rec = "Insufficient rainfall data to derive a climate score."
    elif score >= 80:
        rec = "Climate is highly favourable with reliable seasonal rainfall."
    elif score >= 60:
        rec = "Climate is favourable. Adequate seasonal recharge expected."
    elif score >= 40:
        rec = "Climate is marginal. Larger storage may be needed for dry season."
    else:
        rec = "Climate is poor for rain-fed pond storage. Supplemental sources recommended."

    return {
        "available": True,
        "source": "Open-Meteo Historical Weather API",
        "latitude": round(float(latitude), 6),
        "longitude": round(float(longitude), 6),
        "period": base.get("period"),
        "annual_rainfall_mm": annual_mm,
        "monsoon_rainfall_mm": monsoon_mm,
        "rainfall_reliability": reliability,
        "seasonality": seasonality,
        "climate_score": score,
        "recommendation": rec,
        "yearly": yearly,
        "error": None,
    }
