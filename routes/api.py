from flask import Blueprint, request, jsonify
import numpy as np
import logging

from analysis.location import geocode_location
from analysis.terrain import generate_coordinate_grid, fetch_elevation_grid, generate_synthetic_dem
from analysis.slope import calculate_slope
from analysis.catchment import calculate_d8_flow_direction, calculate_flow_accumulation, delineate_catchment
from analysis.rainfall import fetch_rainfall_data
from analysis.runoff import calculate_runoff_volume
from analysis.suitability import calculate_suitability_grid, identify_candidates, recommend_pond_dimensions

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__)

@api_bp.route("/geocode", methods=["GET"])
def api_geocode():
    """
    Endpoint for geocoding a search query.
    GET /api/geocode?query=Malpura
    """
    query = request.args.get("query", "")
    if not query:
        return jsonify({"error": "Missing 'query' parameter"}), 400
        
    result = geocode_location(query)
    if result:
        return jsonify(result), 200
    else:
        return jsonify({"error": "Location not found"}), 404

@api_bp.route("/analyze", methods=["POST"])
def api_analyze():
    """
    Endpoint for running the full terrain analysis pipeline.
    POST /api/analyze
    JSON Input:
        - latitude (float, required)
        - longitude (float, required)
        - cell_spacing (float, optional, default 50)
        - grid_size (int, optional, default 11)
        - soil_type (str, optional, default 'loam_soil')
        - demo_mode (bool, optional, default False)
        - target_fraction (float, optional, default 0.15)
        - pond_depth (float, optional, default 2.5)
        - weights (dict, optional, e.g. {"elevation": 0.25, "slope": 0.35, "accumulation": 0.40})
    """
    data = request.get_json() or {}
    
    # 1. Validation
    try:
        lat = float(data.get("latitude"))
        lon = float(data.get("longitude"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid or missing 'latitude' and 'longitude' values"}), 400
        
    cell_spacing = float(data.get("cell_spacing", 50.0))
    grid_size = int(data.get("grid_size", 11))
    soil_type = data.get("soil_type", "loam_soil")
    demo_mode = bool(data.get("demo_mode", False))
    target_fraction = float(data.get("target_fraction", 0.15))
    pond_depth = float(data.get("pond_depth", 2.5))
    weights = data.get("weights", None)
    
    # Ensure grid size is odd (needed for clean centering)
    if grid_size % 2 == 0:
        grid_size += 1
        
    # Cap grid size to 15 to keep API and compute times fast
    grid_size = min(15, max(5, grid_size))
    
    try:
        # 2. Coordinate Grid Generation
        lats_grid, lons_grid = generate_coordinate_grid(lat, lon, grid_size=grid_size, cell_spacing=cell_spacing)
        
        # 3. Elevation Retrieval (DEM)
        if demo_mode:
            logger.info("Demo Mode Active: Generating synthetic valley DEM.")
            elevations = generate_synthetic_dem(grid_size=grid_size)
            source_desc = "Synthetic Demo Terrain Model"
        else:
            elevations = fetch_elevation_grid(lats_grid, lons_grid)
            source_desc = "Open-Meteo Elevation API"
            
            # Fallback to demo mode if API fails
            if elevations is None:
                logger.warning("Elevation API failed. Falling back to Synthetic DEM (Demo Mode).")
                elevations = generate_synthetic_dem(grid_size=grid_size)
                demo_mode = True
                source_desc = "Synthetic Fallback (API Offline)"
                
        # 4. Slope Calculation
        slope_results = calculate_slope(elevations, cell_spacing=cell_spacing)
        slopes = slope_results["slope_deg"]
        
        # 5. Flow Routing (D8)
        flow_dir = calculate_d8_flow_direction(elevations, cell_spacing=cell_spacing)
        flow_accum = calculate_flow_accumulation(elevations, flow_dir)
        
        # 6. Suitability Score
        suitability = calculate_suitability_grid(
            elevations, slopes, flow_accum, weights=weights
        )
        
        # 7. Identify Candidates (up to 4 candidates to keep output clear)
        raw_candidates = identify_candidates(suitability, min_score=40.0, max_candidates=4)
        
        # 8. Fetch Rainfall Data (Query once for the center coordinate)
        # In Demo Mode, use standard rainfall fallback
        if demo_mode:
            rainfall_results = {
                "annual_total_mm": 800.0,
                "monthly_totals_mm": [50.0, 45.0, 60.0, 75.0, 90.0, 110.0, 100.0, 85.0, 70.0, 55.0, 40.0, 20.0],
                "source": "Demo climatology data",
                "fallback": True
            }
        else:
            rainfall_results = fetch_rainfall_data(lat, lon)
            
        annual_rainfall = rainfall_results["annual_total_mm"]
        
        # 9. Perform detailed catchment & runoff calculations for candidates
        processed_candidates = []
        for idx, cand in enumerate(raw_candidates):
            cr, cc = cand["r"], cand["c"]
            cand_lat = float(lats_grid[cr, cc])
            cand_lon = float(lons_grid[cr, cc])
            cand_elev = float(elevations[cr, cc])
            cand_slope = float(slopes[cr, cc])
            
            # Delineate catchment
            catchment_res = delineate_catchment(flow_dir, cr, cc, cell_spacing=cell_spacing)
            catch_area = catchment_res["area_sqm"]
            
            # Catchment coordinates for Leaflet polygons
            catchment_latlons = []
            for r, c in catchment_res["boundary_coords"]:
                catchment_latlons.append([float(lats_grid[r, c]), float(lons_grid[r, c])])
                
            # Runoff volume estimation
            runoff_res = calculate_runoff_volume(annual_rainfall, catch_area, soil_type)
            runoff_vol = runoff_res["runoff_volume_m3"]
            
            # Recommend pond size
            pond_size = recommend_pond_dimensions(
                runoff_vol, target_fraction=target_fraction, default_depth=pond_depth
            )
            
            processed_candidates.append({
                "id": idx + 1,
                "row": cr,
                "col": cc,
                "latitude": cand_lat,
                "longitude": cand_lon,
                "elevation_m": round(cand_elev, 1),
                "slope_deg": round(cand_slope, 1),
                "suitability_score": round(cand["score"], 1),
                "catchment_area_sqm": round(catch_area, 1),
                "catchment_area_ha": round(catch_area / 10000.0, 2),
                "runoff_volume_m3": round(runoff_vol, 1),
                "catchment_coordinates": catchment_latlons,
                "pond_dimensions": pond_size
            })
            
        # Select best candidate
        best_candidate_id = None
        if processed_candidates:
            # First is best since raw_candidates are sorted by suitability score descending
            best_candidate_id = processed_candidates[0]["id"]
            
        # Package full grids for frontend visualization
        # We need coordinates alongside values
        grid_data = []
        for r in range(grid_size):
            row_data = []
            for c in range(grid_size):
                row_data.append({
                    "row": r,
                    "col": c,
                    "lat": float(lats_grid[r, c]),
                    "lon": float(lons_grid[r, c]),
                    "elevation": float(elevations[r, c]),
                    "slope": float(slopes[r, c]),
                    "accumulation": float(flow_accum[r, c]),
                    "suitability": float(suitability[r, c]),
                    "flow_dir": int(flow_dir[r, c])
                })
            grid_data.append(row_data)
            
        response = {
            "metadata": {
                "center_lat": lat,
                "center_lon": lon,
                "grid_size": grid_size,
                "cell_spacing": cell_spacing,
                "dem_source": source_desc,
                "rainfall_source": rainfall_results["source"],
                "rainfall_annual_mm": annual_rainfall,
                "rainfall_monthly_mm": rainfall_results["monthly_totals_mm"],
                "rainfall_fallback_used": rainfall_results["fallback"],
                "soil_type": soil_type,
                "demo_mode": demo_mode
            },
            "grid": grid_data,
            "candidates": processed_candidates,
            "best_candidate_id": best_candidate_id
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        logger.error(f"Analysis pipeline crash: {e}", exc_info=True)
        return jsonify({"error": f"Internal computation error: {str(e)}"}), 500
