import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Default fallback climatological rainfall in mm
FALLBACK_ANNUAL_RAINFALL = 800.0
# Approximate monthly distribution of the fallback annual value (climatological average)
FALLBACK_MONTHLY_RAINFALL = [50.0, 45.0, 60.0, 75.0, 90.0, 110.0, 100.0, 85.0, 70.0, 55.0, 40.0, 20.0]

def fetch_rainfall_data(latitude, longitude, year=2025):
    """
    Fetch historical rainfall data for a given location and year from Open-Meteo Archive API.
    Calculates total annual rainfall and monthly averages.
    
    Args:
        latitude (float): Latitude
        longitude (float): Longitude
        year (int): Year to query
        
    Returns:
        dict: A dictionary containing:
            - 'annual_total_mm': Total rainfall for the year.
            - 'monthly_totals_mm': List of 12 values representing monthly sums.
            - 'source': String indicating data source ('Open-Meteo' or 'Climatological Fallback').
            - 'fallback': Boolean indicating if fallback was used.
    """
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"
    
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "precipitation_sum",
        "timezone": "auto"
    }
    
    try:
        logger.info(f"Querying Open-Meteo Historical API for rainfall at ({latitude}, {longitude}) for year {year}...")
        response = requests.get(url, params=params, timeout=12)
        
        if response.status_code == 200:
            data = response.json()
            if "daily" in data and "precipitation_sum" in data["daily"] and "time" in data["daily"]:
                times = data["daily"]["time"]
                precip = data["daily"]["precipitation_sum"]
                
                # Replace None values with 0.0
                precip = [p if p is not None else 0.0 for p in precip]
                
                # Initialize monthly lists
                monthly_sums = [0.0] * 12
                
                # Group daily precipitation by month
                for t_str, val in zip(times, precip):
                    # date format: "YYYY-MM-DD"
                    try:
                        dt = datetime.strptime(t_str, "%Y-%m-%d")
                        month_idx = dt.month - 1  # 0-indexed
                        monthly_sums[month_idx] += val
                    except ValueError:
                        continue
                        
                annual_total = sum(precip)
                
                # If the total is abnormally 0, check if we fetched valid data
                if annual_total == 0.0:
                    logger.warning("Fetched 0mm rainfall. Falling back to default data.")
                    return {
                        "annual_total_mm": FALLBACK_ANNUAL_RAINFALL,
                        "monthly_totals_mm": FALLBACK_MONTHLY_RAINFALL,
                        "source": "Climatological Fallback (Zero Return)",
                        "fallback": True
                    }
                    
                logger.info(f"Rainfall API Success: {annual_total:.1f} mm total.")
                return {
                    "annual_total_mm": round(annual_total, 2),
                    "monthly_totals_mm": [round(m, 2) for m in monthly_sums],
                    "source": f"Open-Meteo Historical ({year})",
                    "fallback": False
                }
            else:
                logger.error("Open-Meteo daily precipitation keys missing in response.")
        else:
            logger.error(f"Open-Meteo API returned status code {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to Open-Meteo Rainfall API: {e}")
        
    # Fallback response
    logger.info("Using fallback climatological data.")
    return {
        "annual_total_mm": FALLBACK_ANNUAL_RAINFALL,
        "monthly_totals_mm": FALLBACK_MONTHLY_RAINFALL,
        "source": "Climatological Fallback",
        "fallback": True
    }
stream = logging.StreamHandler()
logger.addHandler(stream)
logger.setLevel(logging.INFO)
if __name__ == "__main__":
    # Quick sanity check
    print(fetch_rainfall_data(26.2, 75.3))
