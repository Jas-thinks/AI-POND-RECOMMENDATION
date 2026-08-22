/* ==========================================================================
   AI-Based Village Pond Planner - Frontend Orchestration
   Handles Map rendering, layer visualization, chart updates, and API calls.
   ========================================================================== */

let map;
let baseLayers = {};
let overlayLayers = {};
let gridLayerGroup;
let flowPathLayerGroup;
let catchmentLayerGroup;
let markersLayerGroup;

let activeGridData = null; // Store current analysis results locally
let selectedRasterLayer = "suitability"; // Default raster view

// Initialize App
document.addEventListener("DOMContentLoaded", () => {
    initSliders();
    initMap();
    initEventListeners();
    
    // Auto-run first analysis in Demo Mode on startup to show immediate results
    setTimeout(() => {
        runAnalysis();
    }, 500);
});

// 1. SLIDER & PARAMETER CONTROLS
function initSliders() {
    const depthSlider = document.getElementById("param-depth");
    const depthVal = document.getElementById("val-depth");
    depthSlider.addEventListener("input", (e) => {
        depthVal.textContent = e.target.value + " m";
    });

    const fracSlider = document.getElementById("param-fraction");
    const fracVal = document.getElementById("val-fraction");
    fracSlider.addEventListener("input", (e) => {
        fracVal.textContent = e.target.value + "%";
    });

    // Setup interactive weights balancing (Proportional adjustment)
    const weights = {
        elevation: document.getElementById("weight-elev"),
        slope: document.getElementById("weight-slope"),
        accumulation: document.getElementById("weight-accum")
    };
    
    const weightSpans = {
        elevation: document.getElementById("val-w-elev"),
        slope: document.getElementById("val-w-slope"),
        accumulation: document.getElementById("val-w-accum")
    };

    function adjustWeights(changedKey) {
        let valA = parseInt(weights[changedKey].value);
        let keyB, keyC;
        
        if (changedKey === "elevation") {
            keyB = "slope";
            keyC = "accumulation";
        } else if (changedKey === "slope") {
            keyB = "elevation";
            keyC = "accumulation";
        } else {
            keyB = "elevation";
            keyC = "slope";
        }

        let valB = parseInt(weights[keyB].value);
        let valC = parseInt(weights[keyC].value);
        let sumBC = valB + valC;
        let remaining = 100 - valA;

        if (sumBC > 0) {
            valB = Math.round(remaining * (valB / sumBC));
            valC = remaining - valB;
        } else {
            valB = Math.round(remaining / 2);
            valC = remaining - valB;
        }

        // Apply new values
        weights[keyB].value = valB;
        weights[keyC].value = valC;

        // Update Text
        for (let key in weights) {
            weightSpans[key].textContent = weights[key].value + "%";
        }
        document.getElementById("weight-sum").textContent = "100%";
    }

    weights.elevation.addEventListener("input", () => adjustWeights("elevation"));
    weights.slope.addEventListener("input", () => adjustWeights("slope"));
    weights.accumulation.addEventListener("input", () => adjustWeights("accumulation"));
}

// 2. LEAFLET MAP INITIALIZATION
function initMap() {
    // Default center at Malpura, Rajasthan, India
    const defaultCenter = [26.2974, 75.3804];
    
    // Create Map
    map = L.map("map", {
        center: defaultCenter,
        zoom: 15,
        zoomControl: true
    });

    // Map Base Layers
    const osmStandard = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    });

    const esriSatellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 18,
        attribution: "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community"
    });

    // Add default base
    osmStandard.addTo(map);

    baseLayers = {
        "Standard Map": osmStandard,
        "Satellite Imagery": esriSatellite
    };

    // Layer groups for overlays
    gridLayerGroup = L.layerGroup().addTo(map);
    flowPathLayerGroup = L.layerGroup().addTo(map);
    catchmentLayerGroup = L.layerGroup().addTo(map);
    markersLayerGroup = L.layerGroup().addTo(map);

    overlayLayers = {
        "Analysis Grids": gridLayerGroup,
        "Flow Accumulation Paths": flowPathLayerGroup,
        "Catchment Basin Boundary": catchmentLayerGroup,
        "Pond Candidates": markersLayerGroup
    };

    // Add Layer Control
    L.control.layers(baseLayers, overlayLayers, { collapsed: false }).addTo(map);
}

// 3. INTERACTIVE LAYERS RENDER
function renderAnalysisGrids(grid, metadata, layerType) {
    gridLayerGroup.clearLayers();
    flowPathLayerGroup.clearLayers();
    
    if (!grid || grid.length === 0) return;
    
    const rows = grid.length;
    const cols = grid[0].length;
    const spacing = metadata.cell_spacing;
    
    // Find min and max elevations for color scaling
    let elevations = [];
    let maxAccum = 1;
    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            elevations.push(grid[r][c].elevation);
            if (grid[r][c].accumulation > maxAccum) {
                maxAccum = grid[r][c].accumulation;
            }
        }
    }
    const minElev = Math.min(...elevations);
    const maxElev = Math.max(...elevations);
    
    // Lat Offset approximations
    const latOffset = spacing / 111320.0 / 2.0;

    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            const cell = grid[r][c];
            
            // Calculate Cell bounds
            const lonOffset = spacing / (111320.0 * Math.cos(cell.lat * Math.PI / 180.0)) / 2.0;
            const bounds = [
                [cell.lat - latOffset, cell.lon - lonOffset],
                [cell.lat + latOffset, cell.lon + lonOffset]
            ];
            
            // Decide fill color
            let fillColor = "#ffffff";
            let opacity = 0.55;
            
            if (layerType === "suitability") {
                fillColor = getSuitabilityColor(cell.suitability);
                // Reduce opacity for unsuitable (0 score) cells to see base map
                if (cell.suitability === 0) opacity = 0.05;
            } else if (layerType === "elevation") {
                fillColor = getElevationColor(cell.elevation, minElev, maxElev);
            } else if (layerType === "slope") {
                fillColor = getSlopeColor(cell.slope);
                if (cell.slope > 15.0) opacity = 0.7; // highlight steep slopes
            } else if (layerType === "accumulation") {
                fillColor = getAccumulationColor(cell.accumulation, maxAccum);
                opacity = cell.accumulation > 1 ? 0.65 : 0.0;
            }
            
            // Draw grid square
            const rect = L.rectangle(bounds, {
                color: "#94a3b8", // light border
                weight: 0.5,
                fillColor: fillColor,
                fillOpacity: opacity
            });
            
            // Create nice popup
            const popupContent = `
                <div class="gis-cell-popup">
                    <h4>Grid Cell [R:${r}, C:${c}]</h4>
                    <div>Elevation: <b>${cell.elevation.toFixed(1)} m</b></div>
                    <div>Slope: <b>${cell.slope.toFixed(1)}&deg;</b></div>
                    <div>Accumulation: <b>${cell.accumulation} cells</b></div>
                    <div>Suitability Score: <b>${cell.suitability.toFixed(1)}</b></div>
                </div>
            `;
            rect.bindPopup(popupContent);
            gridLayerGroup.addLayer(rect);
            
            // Draw D8 flow arrows as vector lines connecting centers
            if (cell.flow_dir !== -1) {
                // Find downstream cell indices
                const offsets = [
                    [0, 1],    // 0: East
                    [1, 1],    // 1: South-East
                    [1, 0],    // 2: South
                    [1, -1],   // 3: South-West
                    [0, -1],   // 4: West
                    [-1, -1],  // 5: North-West
                    [-1, 0],   // 6: North
                    [-1, 1]    // 7: North-East
                ];
                
                const [dr, dc] = offsets[cell.flow_dir];
                const nr = r + dr;
                const nc = c + dc;
                
                // Ensure neighbor is in bounds before drawing arrow path
                if (0 <= nr < rows && 0 <= nc < cols && grid[nr] && grid[nr][nc]) {
                    const nextCell = grid[nr][nc];
                    const latlngs = [
                        [cell.lat, cell.lon],
                        [nextCell.lat, nextCell.lon]
                    ];
                    
                    // Water vector path (D8)
                    const path = L.polyline(latlngs, {
                        color: "#2563eb",
                        weight: cell.accumulation > 5 ? 2.5 : 1.0,
                        opacity: cell.accumulation > 2 ? 0.55 : 0.15,
                        dashArray: "4, 4"
                    });
                    flowPathLayerGroup.addLayer(path);
                }
            }
        }
    }
}

// 4. COLOR GENERATORS (MATHEMATICAL RAMPS)
function getSuitabilityColor(score) {
    // Red (0) to Yellow (50) to Green (100)
    // HSL mapping: 0 is Red, 120 is Green.
    // Score is 0 - 100
    const hue = score * 1.2; // 0 -> 0, 100 -> 120
    return `hsl(${hue}, 85%, 45%)`;
}

function getSlopeColor(slope) {
    // Flat (0, Green) to Steep (15+, Red)
    const limit = 15.0;
    const factor = Math.min(1.0, slope / limit);
    const hue = (1.0 - factor) * 120; // 0 slope -> 120 (Green), 15+ slope -> 0 (Red)
    return `hsl(${hue}, 85%, 45%)`;
}

function getElevationColor(elev, minElev, maxElev) {
    // Terrain brown/yellow ramp.
    const range = maxElev - minElev;
    if (range === 0) return "#10b981";
    const factor = (elev - minElev) / range;
    // Lower elevation is darker green/teal (depressions), higher is brown
    const hue = (1.0 - factor) * 100 + 40; // 140 (teal) down to 40 (brown)
    return `hsl(${hue}, 60%, ${40 + factor * 20}%)`;
}

function getAccumulationColor(accum, maxAccum) {
    if (accum <= 1) return "transparent";
    // Log scale blue
    const val = Math.log(accum) / Math.log(maxAccum);
    return `rgba(37, 99, 235, ${0.3 + val * 0.7})`;
}

// 5. CATCHMENT BASIN DISPLAY
function showCatchmentBoundary(candidate) {
    catchmentLayerGroup.clearLayers();
    
    const latlngs = candidate.catchment_coordinates;
    if (!latlngs || latlngs.length === 0) return;
    
    // Draw cells representing the catchment
    // Since catchment_coordinates lists center points, we draw transparent blue rectangles
    const spacing = activeGridData.metadata.cell_spacing;
    const latOffset = spacing / 111320.0 / 2.0;
    
    latlngs.forEach(pt => {
        const lat = pt[0];
        const lon = pt[1];
        const lonOffset = spacing / (111320.0 * Math.cos(lat * Math.PI / 180.0)) / 2.0;
        const bounds = [
            [lat - latOffset, lon - lonOffset],
            [lat + latOffset, lon + lonOffset]
        ];
        
        const rect = L.rectangle(bounds, {
            color: "#3b82f6",
            weight: 1.0,
            fillColor: "#60a5fa",
            fillOpacity: 0.35
        });
        catchmentLayerGroup.addLayer(rect);
    });
    
    // Highlight bounding polygon of the points as a dashed boundary line
    // (A convex hull could be drawn, but outline cells are sufficient and look extremely raster-native)
}

// 6. CANDIDATES RENDER
function renderCandidates(candidates, bestId) {
    markersLayerGroup.clearLayers();
    
    candidates.forEach(cand => {
        const isBest = cand.id === bestId;
        
        // Custom icon styling for candidates
        const markerColor = isBest ? "#eab308" : "#0d9488"; // Gold star for best, teal for rest
        const pulseClass = isBest ? "pulse-marker" : "";
        
        const iconHtml = `
            <div style="
                background-color: ${markerColor};
                width: 28px;
                height: 28px;
                border-radius: 50%;
                border: 2px solid white;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
                font-size: 13px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            " class="${pulseClass}">
                ${isBest ? '<i class="fa-solid fa-star" style="font-size: 10px;"></i>' : cand.id}
            </div>
        `;
        
        const customIcon = L.divIcon({
            html: iconHtml,
            className: "custom-candidate-icon",
            iconSize: [28, 28],
            iconAnchor: [14, 14]
        });
        
        const marker = L.marker([cand.latitude, cand.longitude], { icon: customIcon });
        
        const popupContent = `
            <div style="font-size: 12.5px; line-height: 1.4; min-width: 180px;">
                <h4 style="margin: 0 0 6px 0; border-bottom: 2px solid ${markerColor}; padding-bottom: 3px; color: ${isBest ? '#b45309' : '#0f766e'};">
                    ${isBest ? '&#9733; Recommended Site' : `Candidate Site #${cand.id}`}
                </h4>
                <div>Suitability: <b>${cand.suitability_score}%</b></div>
                <div>Elevation: <b>${cand.elevation_m} m</b></div>
                <div>Slope: <b>${cand.slope_deg}&deg;</b></div>
                <div>Catchment Area: <b>${cand.catchment_area_ha} ha</b></div>
                <div>Runoff: <b>${cand.runoff_volume_m3.toLocaleString()} m&sup3;</b></div>
                <div style="margin-top: 6px; border-top: 1px dashed #ccc; padding-top: 6px;">
                    Pond: <b>${cand.pond_dimensions.length_m}m &times; ${cand.pond_dimensions.width_m}m &times; ${cand.pond_dimensions.depth_m}m</b>
                </div>
            </div>
        `;
        
        marker.bindPopup(popupContent);
        
        // Show catchment basin when clicked or hovered
        marker.on("click", () => {
            showCatchmentBoundary(cand);
            updateRecommendationCard(cand, isBest);
        });
        
        markersLayerGroup.addLayer(marker);
        
        // Auto-open recommended popup on start
        if (isBest) {
            setTimeout(() => {
                marker.openPopup();
                showCatchmentBoundary(cand);
                updateRecommendationCard(cand, true);
            }, 300);
        }
    });
}

// 7. SIDEBAR RESULTS RENDER
function updateRecommendationCard(cand, isBest) {
    document.getElementById("rec-score").textContent = cand.suitability_score + "%";
    document.getElementById("rec-elev").textContent = cand.elevation_m + " m";
    document.getElementById("rec-slope").textContent = cand.slope_deg + "°";
    document.getElementById("rec-catchment").textContent = cand.catchment_area_ha + " ha (" + cand.catchment_area_sqm.toLocaleString() + " m²)";
    document.getElementById("rec-rainfall").textContent = activeGridData.metadata.rainfall_annual_mm.toFixed(1) + " mm";
    document.getElementById("rec-runoff").textContent = cand.runoff_volume_m3.toLocaleString() + " m³";
    
    // Dimensions
    const dims = cand.pond_dimensions;
    document.getElementById("dim-len").textContent = dims.length_m;
    document.getElementById("dim-wid").textContent = dims.width_m;
    document.getElementById("dim-dep").textContent = dims.depth_m;
    document.getElementById("dim-capacity").textContent = dims.capacity_m3.toLocaleString();
}

// 8. RENDER RAINFALL BARS
function renderRainfallChart(monthlyData) {
    const container = document.getElementById("rainfall-chart-container");
    container.innerHTML = ""; // Clear loader
    
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const maxVal = Math.max(...monthlyData, 10.0);
    
    monthlyData.forEach((val, idx) => {
        const pct = (val / maxVal) * 100;
        
        const barContainer = document.createElement("div");
        barContainer.className = "chart-bar-container";
        
        const barFill = document.createElement("div");
        barFill.className = "chart-bar-fill";
        barFill.style.height = pct + "%";
        barFill.setAttribute("data-val", val.toFixed(1));
        
        const label = document.createElement("span");
        label.className = "chart-bar-label";
        label.textContent = months[idx];
        
        barContainer.appendChild(barFill);
        barContainer.appendChild(label);
        container.appendChild(barContainer);
    });
}

// 9. UPDATE CANDIDATES RANKING COMPARISON TABLE
function renderComparisonTable(candidates, bestId) {
    const tbody = document.getElementById("candidates-table").getElementsByTagName("tbody")[0];
    tbody.innerHTML = "";
    
    if (candidates.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">No candidates identified above score limit.</td></tr>`;
        return;
    }
    
    candidates.forEach((cand, idx) => {
        const isBest = cand.id === bestId;
        const dims = cand.pond_dimensions;
        
        const tr = document.createElement("tr");
        if (isBest) tr.style.backgroundColor = "#fffbeb"; // light gold row highlight
        
        tr.innerHTML = `
            <td><b>${idx + 1}</b> ${isBest ? '<i class="fa-solid fa-star text-gold"></i>' : ''}</td>
            <td>${cand.latitude.toFixed(5)}, ${cand.longitude.toFixed(5)}</td>
            <td>${cand.elevation_m} m</td>
            <td>${cand.slope_deg}&deg;</td>
            <td>${cand.catchment_area_ha} ha</td>
            <td>${cand.runoff_volume_m3.toLocaleString()} m&sup3;</td>
            <td>${dims.length_m}m &times; ${dims.width_m}m &times; ${dims.depth_m}m</td>
            <td><span class="badge ${isBest ? 'badge-real' : 'badge-demo'}" style="font-size: 11px;">${cand.suitability_score}%</span></td>
        `;
        
        // Clicking row highlights marker and catchment
        tr.addEventListener("click", () => {
            showCatchmentBoundary(cand);
            updateRecommendationCard(cand, isBest);
            map.panTo([cand.latitude, cand.longitude]);
        });
        tr.style.cursor = "pointer";
        
        tbody.appendChild(tr);
    });
}

// 10. RUN COMPUTE ANALYSIS PIPELINE (ORCHESTRATOR)
async function runAnalysis() {
    const runBtn = document.getElementById("run-btn");
    const statusMsg = document.getElementById("api-status");
    const modeBadge = document.getElementById("mode-badge");
    
    runBtn.disabled = true;
    runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Running Analysis...';
    statusMsg.textContent = "Connecting to elevation grids & meteorological databases...";
    
    // Collect Inputs
    const lat = parseFloat(document.getElementById("input-lat").value);
    const lon = parseFloat(document.getElementById("input-lon").value);
    const spacing = parseFloat(document.getElementById("param-spacing").value);
    const gridSize = parseInt(document.getElementById("param-grid-size").value);
    const soilType = document.getElementById("param-soil").value;
    const depth = parseFloat(document.getElementById("param-depth").value);
    const fraction = parseFloat(document.getElementById("param-fraction").value) / 100.0;
    const demoMode = document.getElementById("param-demo").checked;
    
    const wElev = parseInt(document.getElementById("weight-elev").value);
    const wSlope = parseInt(document.getElementById("weight-slope").value);
    const wAccum = parseInt(document.getElementById("weight-accum").value);
    
    const payload = {
        latitude: lat,
        longitude: lon,
        cell_spacing: spacing,
        grid_size: gridSize,
        soil_type: soilType,
        pond_depth: depth,
        target_fraction: fraction,
        demo_mode: demoMode,
        weights: {
            elevation: wElev,
            slope: wSlope,
            accumulation: wAccum
        }
    };
    
    try {
        const response = await fetch("/api/analyze", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        });
        
        if (response.ok) {
            const result = await response.json();
            activeGridData = result;
            
            // Update mode indicators
            if (result.metadata.demo_mode) {
                modeBadge.textContent = "Demo Mode Active";
                modeBadge.className = "badge badge-demo";
                document.getElementById("param-demo").checked = true;
            } else {
                modeBadge.textContent = "Production Mode";
                modeBadge.className = "badge badge-real";
                document.getElementById("param-demo").checked = false;
            }
            
            statusMsg.textContent = `Completed. Terrain Source: ${result.metadata.dem_source}`;
            document.getElementById("rainfall-source").textContent = "Source: " + result.metadata.rainfall_source;
            
            // Re-render UI
            renderAnalysisGrids(result.grid, result.metadata, selectedRasterLayer);
            renderCandidates(result.candidates, result.best_candidate_id);
            renderRainfallChart(result.metadata.rainfall_monthly_mm);
            renderComparisonTable(result.candidates, result.best_candidate_id);
            
            // Pan Map to grid bounds
            const grid = result.grid;
            if (grid && grid.length > 0) {
                const rows = grid.length;
                const cols = grid[0].length;
                const southWest = [grid[rows - 1][0].lat, grid[rows - 1][0].lon];
                const northEast = [grid[0][cols - 1].lat, grid[0][cols - 1].lon];
                map.fitBounds([southWest, northEast]);
            }
            
        } else {
            const err = await response.json();
            statusMsg.textContent = `Error: ${err.error || "Analysis failed"}`;
            alert("Error running analysis: " + (err.error || "Unknown server error"));
        }
    } catch (e) {
        console.error(e);
        statusMsg.textContent = "Failed to connect to Flask server.";
        alert("API request error. Make sure app.py is running locally.");
    } finally {
        runBtn.disabled = false;
        runBtn.innerHTML = '<i class="fa-solid fa-gears"></i> Run AI Pond Analysis';
    }
}

// 11. MAP LAYER CONTROL & EVENT LISTENERS
function initEventListeners() {
    // Run button click
    document.getElementById("run-btn").addEventListener("click", runAnalysis);
    
    // Geocode Location Search
    const searchInput = document.getElementById("village-search");
    const searchBtn = document.getElementById("search-btn");
    
    async function triggerSearch() {
        const query = searchInput.value;
        if (!query.strip) {
            String.prototype.strip = function() { return this.replace(/^\s+|\s+$/g, ""); };
        }
        if (!query.strip()) return;
        
        searchBtn.disabled = true;
        searchBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
        
        try {
            const response = await fetch(`/api/geocode?query=${encodeURIComponent(query)}`);
            if (response.ok) {
                const res = await response.json();
                document.getElementById("input-lat").value = res.lat.toFixed(6);
                document.getElementById("input-lon").value = res.lon.toFixed(6);
                searchInput.value = res.display_name;
                
                // Disable Demo Mode on successful query to test real-world terrain fetch!
                document.getElementById("param-demo").checked = false;
                
                // Pan map
                map.setView([res.lat, res.lon], 15);
                
                // Run calculations automatically
                runAnalysis();
            } else {
                alert("Location not found. Try adding country/state name (e.g. 'Malpura, Rajasthan').");
            }
        } catch (e) {
            console.error(e);
            alert("Geocoding service unavailable.");
        } finally {
            searchBtn.disabled = false;
            searchBtn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i>';
        }
    }
    
    searchBtn.addEventListener("click", triggerSearch);
    searchInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") triggerSearch();
    });

    // Custom UI overlay switches in Leaflet legend
    // Create a client-side filter to switch between raster layers instantly
    const legendDiv = document.getElementById("layer-legend");
    legendDiv.innerHTML = `
        <h4>Map Display Layer</h4>
        <select id="raster-layer-select" style="width:100%; padding:3px; font-size:11px; margin-bottom:8px; border-radius:4px; border:1px solid #ccc; outline:none; background:white;">
            <option value="suitability" selected>AI Suitability Heatmap</option>
            <option value="elevation">Elevation Contours (DEM)</option>
            <option value="slope">Terrain Slope Gradient</option>
            <option value="accumulation">Water Flow Accumulation</option>
        </select>
        <div class="legend-labels-container">
            <div class="legend-scale" id="dyn-scale"></div>
            <div class="legend-labels" id="dyn-labels"></div>
        </div>
    `;
    
    const layerSelect = document.getElementById("raster-layer-select");
    
    function updateLegendUI(layer) {
        const scale = document.getElementById("dyn-scale");
        const labels = document.getElementById("dyn-labels");
        
        if (layer === "suitability") {
            scale.innerHTML = `
                <span style="background:#d73027"></span>
                <span style="background:#fc8d59"></span>
                <span style="background:#fee08b"></span>
                <span style="background:#d9ef8b"></span>
                <span style="background:#91cf60"></span>
                <span style="background:#1a9850"></span>
            `;
            labels.innerHTML = `<span>Unsuitable (0)</span><span>Optimal (100)</span>`;
        } else if (layer === "elevation") {
            scale.innerHTML = `
                <span style="background:hsl(140, 60%, 40%)"></span>
                <span style="background:hsl(100, 60%, 45%)"></span>
                <span style="background:hsl(70, 60%, 50%)"></span>
                <span style="background:hsl(40, 60%, 60%)"></span>
            `;
            labels.innerHTML = `<span>Low Elev</span><span>High Elev</span>`;
        } else if (layer === "slope") {
            scale.innerHTML = `
                <span style="background:#1a9850"></span>
                <span style="background:#d9ef8b"></span>
                <span style="background:#fee08b"></span>
                <span style="background:#fc8d59"></span>
                <span style="background:#d73027"></span>
            `;
            labels.innerHTML = `<span>Flat (0&deg;)</span><span>Steep (15&deg;+)</span>`;
        } else if (layer === "accumulation") {
            scale.innerHTML = `
                <span style="background:rgba(37,99,235,0.1)"></span>
                <span style="background:rgba(37,99,235,0.4)"></span>
                <span style="background:rgba(37,99,235,0.7)"></span>
                <span style="background:rgba(37,99,235,1.0)"></span>
            `;
            labels.innerHTML = `<span>Runoff flow</span><span>Stream center</span>`;
        }
    }
    
    // Initial legend setup
    updateLegendUI("suitability");
    
    layerSelect.addEventListener("change", (e) => {
        selectedRasterLayer = e.target.value;
        updateLegendUI(selectedRasterLayer);
        if (activeGridData) {
            renderAnalysisGrids(activeGridData.grid, activeGridData.metadata, selectedRasterLayer);
        }
    });
}
