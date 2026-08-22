import os
import requests
import logging

logger = logging.getLogger(__name__)

def geocode_location(query):
    """
    Geocode a village or location query to latitude and longitude.
    Uses free OpenStreetMap Nominatim API.
    
    Args:
        query (str): The search query, e.g., "Malpura, Rajasthan"
        
    Returns:
        dict: A dictionary containing 'lat', 'lon', and 'display_name' if found, else None.
    """
    if not query or not query.strip():
        logger.warning("Empty search query provided to geocoder.")
        return None
        
    # Read custom user-agent from env to adhere to Nominatim's usage policy
    user_agent = os.getenv("GEOCONTROLLER_USER_AGENT", "AI-VillagePondPlanner/1.0")
    
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query.strip(),
        "format": "json",
        "limit": 1
    }
    headers = {
        "User-Agent": user_agent
    }
    
    try:
        logger.info(f"Geocoding query: '{query}'")
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                result = {
                    "lat": float(data[0]["lat"]),
                    "lon": float(data[0]["lon"]),
                    "display_name": data[0]["display_name"]
                }
                logger.info(f"Geocoding success: {result['display_name']} -> ({result['lat']}, {result['lon']})")
                return result
            else:
                logger.warning(f"No results found for query: '{query}'")
                return None
        else:
            logger.error(f"Nominatim API error: HTTP status {response.status_code}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Geocoding request failed: {e}")
        return None
