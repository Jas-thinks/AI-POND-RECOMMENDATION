import logging

logger = logging.getLogger(__name__)

# Standard runoff coefficients by soil / surface type
SOIL_COEFFICIENTS = {
    "sandy_soil": 0.15,      # High infiltration, low runoff
    "loam_soil": 0.35,       # Moderate infiltration, moderate runoff
    "clay_soil": 0.55,       # Low infiltration, high runoff
    "forested": 0.12,        # Heavy vegetation intercepts rainfall
    "urban_impervious": 0.80 # Paved surfaces, almost zero infiltration
}

def calculate_runoff_volume(annual_rainfall_mm, catchment_area_sqm, soil_type_or_coeff):
    """
    Calculate the estimated runoff volume in cubic meters.
    Formula: Runoff Volume = Rainfall (m) * Catchment Area (m^2) * Runoff Coefficient
    
    Args:
        annual_rainfall_mm (float): Annual precipitation in mm.
        catchment_area_sqm (float): Upstream catchment area in square meters.
        soil_type_or_coeff (str or float): Soil coefficient string key or custom float value.
        
    Returns:
        dict: A dictionary containing:
            - 'runoff_volume_m3': Estimated runoff in m^3.
            - 'coefficient': Runoff coefficient used.
            - 'rainfall_m': Rainfall converted to meters.
    """
    # Parse coefficient
    if isinstance(soil_type_or_coeff, str):
        coeff = SOIL_COEFFICIENTS.get(soil_type_or_coeff, 0.30)  # default loam
    else:
        try:
            coeff = float(soil_type_or_coeff)
        except ValueError:
            coeff = 0.30
            
    # Convert rainfall to meters
    rainfall_m = annual_rainfall_mm / 1000.0
    
    # Calculate volume
    runoff_volume_m3 = rainfall_m * catchment_area_sqm * coeff
    
    logger.info(f"Runoff calculation: Rainfall={rainfall_m:.3f}m, Area={catchment_area_sqm:.1f}m2, Coeff={coeff:.2f} -> Volume={runoff_volume_m3:.2f} m3")
    
    return {
        "runoff_volume_m3": round(runoff_volume_m3, 2),
        "coefficient": coeff,
        "rainfall_m": rainfall_m
    }
