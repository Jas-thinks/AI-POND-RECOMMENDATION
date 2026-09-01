// =========================================================
// MAP COLORS
// =========================================================

const MAP_COLORS = {
    boundary: "#0b5ed7",

    catchmentBorder: "#1976d2",
    catchmentFill: "#42a5f5",

    excludedBorder: "#c62828",
    excludedFill: "#ef5350",

    bestCandidateBorder: "#145a32",
    bestCandidateFill: "#2e7d32",

    candidateBorder: "#0d47a1",
    candidateFill: "#2196f3",

    // Original contour lines from uploaded KML
    contour: "#8b5a2b"
};


// =========================================================
// MAP INITIALIZATION
// =========================================================

const map = L
    .map("map")
    .setView(
        [21.25, 81.29],
        13
    );


// =========================================================
// BASE MAPS
// =========================================================

const street = L.tileLayer(
    "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    {
        maxZoom: 19,
        attribution:
            "&copy; OpenStreetMap contributors"
    }
).addTo(map);


const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
        maxZoom: 19,
        attribution:
            "Tiles &copy; Esri"
    }
);


// =========================================================
// ORIGINAL CONTOUR LAYERS
// =========================================================
//
// These layers contain geometry read DIRECTLY from the
// uploaded KML file.
//
// They are NOT generated from our DEM.
//
// This gives us direct visual validation against
// the contour map supplied by the instructor.
// =========================================================

const originalContourLayer =
    L.layerGroup().addTo(map);


const contourLabelLayer =
    L.layerGroup().addTo(map);


// =========================================================
// LEAFLET LAYER CONTROL
// =========================================================

const layerControl = L.control.layers(

    {
        "Street Map": street,
        "Satellite Map": satellite
    },

    {
        "Original Contours (KML)":
            originalContourLayer,

        "Contour Elevation Labels":
            contourLabelLayer
    }

).addTo(map);


// =========================================================
// MAP LEGEND
// =========================================================

const legend = L.control({
    position: "bottomleft"
});


legend.onAdd = function () {

    const div = L.DomUtil.create(
        "div",
        "map-legend"
    );


    div.innerHTML = `

        <div class="legend-title">
            Map Information
        </div>


        <div class="legend-item">

            <span
                class="legend-line legend-contour"
            ></span>

            <span>
                Original contour from uploaded KML
                (elevation above mean sea level)
            </span>

        </div>


        <div class="legend-item">

            <span
                class="legend-box legend-boundary"
            ></span>

            <span>
                Uploaded contour-map analysis boundary
            </span>

        </div>


        <div class="legend-item">

            <span
                class="legend-box legend-catchment"
            ></span>

            <span>
                Calculated catchment area of
                top pond candidate
            </span>

        </div>


        <div class="legend-item">

            <span
                class="legend-box legend-excluded"
            ></span>

            <span>
                Excluded area:
                river, water body, road,
                building or safety clearance
            </span>

        </div>


        <div class="legend-item">

            <span
                class="legend-dot legend-best-site"
            ></span>

            <span>
                Best recommended pond site
            </span>

        </div>


        <div class="legend-item">

            <span
                class="legend-dot legend-other-site"
            ></span>

            <span>
                Other possible pond sites
            </span>

        </div>


        <div class="legend-divider"></div>


        <div class="legend-note">

            <strong>AMSL</strong> =
            Above Mean Sea Level.

            <br><br>

            Brown contour lines are read directly
            from the uploaded KML.

            <br>

            Blue/red polygons are results generated
            by our analysis.

        </div>
    `;


    L.DomEvent.disableClickPropagation(div);

    L.DomEvent.disableScrollPropagation(div);


    return div;
};


legend.addTo(map);


// =========================================================
// ANALYSIS LAYERS
// =========================================================

let analysisLayers = [];

let candidateMarkers =
    new Map();


// =========================================================
// CLEAR PREVIOUS ANALYSIS
// =========================================================

function clearAnalysisLayers() {

    analysisLayers.forEach(
        layer => {

            map.removeLayer(layer);
        }
    );


    analysisLayers = [];

    candidateMarkers.clear();
}


// =========================================================
// ADD ANALYSIS LAYER
// =========================================================

function addLayer(layer) {

    layer.addTo(map);

    analysisLayers.push(layer);

    return layer;
}


// =========================================================
// CLEAR ORIGINAL KML CONTOURS
// =========================================================

function clearContourLayers() {

    originalContourLayer.clearLayers();

    contourLabelLayer.clearLayers();
}


// =========================================================
// READ ORIGINAL KML FILE AND DRAW CONTOURS
// =========================================================

async function drawOriginalContours(file) {

    clearContourLayers();


    if (!file) {
        return;
    }


    const filename =
        file.name.toLowerCase();


    // -----------------------------------------------------
    // This frontend parser currently displays KML directly.
    //
    // KMZ still works in backend analysis, but direct
    // browser contour visualization is intended for KML.
    // -----------------------------------------------------

    if (!filename.endsWith(".kml")) {

        console.warn(
            "Direct contour overlay currently requires KML."
        );

        return;
    }


    const text =
        await file.text();


    const parser =
        new DOMParser();


    const xml =
        parser.parseFromString(
            text,
            "application/xml"
        );


    // -----------------------------------------------------
    // Check XML parsing
    // -----------------------------------------------------

    if (
        xml.getElementsByTagName(
            "parsererror"
        ).length > 0
    ) {

        console.error(
            "Unable to parse uploaded KML."
        );

        return;
    }


    // Namespace-independent Placemark selection
    const placemarks =
        xml.getElementsByTagNameNS(
            "*",
            "Placemark"
        );


    // -----------------------------------------------------
    // Keep one representative label per elevation.
    //
    // Example:
    //
    // 267 m → one label
    // 268 m → one label
    // ...
    // 298 m → one label
    //
    // Without this, your KML contains >1000 lines
    // and the map would contain too many labels.
    // -----------------------------------------------------

    const bestLabelCandidate =
        new Map();


    let contourCount = 0;


    for (
        const placemark
        of placemarks
    ) {

        const lineStrings =
            placemark.getElementsByTagNameNS(
                "*",
                "LineString"
            );


        if (
            lineStrings.length === 0
        ) {
            continue;
        }


        // -------------------------------------------------
        // Read <name>
        //
        // In your contour KML:
        //
        // <name>274.0</name>
        //
        // means:
        //
        // contour elevation = 274 metres AMSL
        // -------------------------------------------------

        const nameElements =
            placemark.getElementsByTagNameNS(
                "*",
                "name"
            );


        if (
            nameElements.length === 0
        ) {
            continue;
        }


        const elevation =
            Number(
                nameElements[0]
                    .textContent
                    .trim()
            );


        // Skip non-numeric placemarks such as "land"
        if (
            !Number.isFinite(
                elevation
            )
        ) {
            continue;
        }


        for (
            const lineString
            of lineStrings
        ) {

            const coordinateElements =
                lineString
                    .getElementsByTagNameNS(
                        "*",
                        "coordinates"
                    );


            if (
                coordinateElements.length === 0
            ) {
                continue;
            }


            const coordinateText =
                coordinateElements[0]
                    .textContent
                    .trim();


            if (!coordinateText) {
                continue;
            }


            // ---------------------------------------------
            // KML coordinate format:
            //
            // longitude,latitude,altitude
            //
            // Leaflet needs:
            //
            // latitude,longitude
            // ---------------------------------------------

            const latLngs =
                coordinateText
                    .split(/\s+/)
                    .map(
                        item => {

                            const parts =
                                item.split(",");


                            if (
                                parts.length < 2
                            ) {
                                return null;
                            }


                            const longitude =
                                Number(parts[0]);


                            const latitude =
                                Number(parts[1]);


                            if (
                                !Number.isFinite(longitude)
                                ||
                                !Number.isFinite(latitude)
                            ) {

                                return null;
                            }


                            return [
                                latitude,
                                longitude
                            ];
                        }
                    )
                    .filter(
                        coordinate =>
                            coordinate !== null
                    );


            if (
                latLngs.length < 2
            ) {
                continue;
            }


            // =============================================
            // DRAW ORIGINAL CONTOUR
            // =============================================

            const contourLine =
                L.polyline(

                    latLngs,

                    {

                        color:
                            MAP_COLORS.contour,

                        weight:
                            1.3,

                        opacity:
                            0.80
                    }
                );


            // ---------------------------------------------
            // Hover / click shows elevation
            // ---------------------------------------------

            contourLine.bindTooltip(

                `${elevation.toFixed(0)} m AMSL`,

                {

                    sticky:
                        true,

                    direction:
                        "top",

                    className:
                        "contour-tooltip"
                }
            );


            contourLine.bindPopup(

                `

                <b>
                    Original Contour
                </b>

                <br>

                Elevation:
                <strong>
                    ${elevation.toFixed(1)} m AMSL
                </strong>

                <br><br>

                This line is read directly
                from the uploaded contour KML.

                `
            );


            contourLine.addTo(
                originalContourLayer
            );


            contourCount++;


            // =============================================
            // FIND A GOOD LABEL POSITION
            // =============================================
            //
            // We choose the longest available contour
            // segment for each elevation level.
            // =============================================

            const key =
                elevation.toFixed(2);


            const existing =
                bestLabelCandidate.get(
                    key
                );


            if (
                !existing
                ||
                latLngs.length
                >
                existing.latLngs.length
            ) {

                bestLabelCandidate.set(

                    key,

                    {
                        elevation:
                            elevation,

                        latLngs:
                            latLngs
                    }
                );
            }
        }
    }


    // =====================================================
    // ADD ONE VISIBLE LABEL PER UNIQUE ELEVATION
    // =====================================================

    for (
        const item
        of bestLabelCandidate.values()
    ) {

        const points =
            item.latLngs;


        const midpointIndex =
            Math.floor(
                points.length / 2
            );


        const position =
            points[
                midpointIndex
            ];


        const icon =
            L.divIcon({

                className:
                    "contour-elevation-label",

                html:
                    `<span>${item.elevation.toFixed(0)} m</span>`,

                iconSize:
                    null
            });


        L.marker(

            position,

            {
                icon:
                    icon,

                interactive:
                    false
            }

        ).addTo(
            contourLabelLayer
        );
    }


    console.log(
        `Original KML contours drawn: ${contourCount}`
    );
}


// =========================================================
// NUMBER FORMATTERS
// =========================================================

function number(
    value,
    digits = 2
) {

    if (
        value === null
        ||
        value === undefined
    ) {

        return "N/A";
    }


    return Number(
        value
    ).toFixed(
        digits
    );
}


function integer(value) {

    if (
        value === null
        ||
        value === undefined
    ) {

        return "N/A";
    }


    return Math
        .round(
            Number(value)
        )
        .toLocaleString();
}


// =========================================================
// RAINFALL TEXT
// =========================================================

function rainfallText(data) {

    const rain =
        data.rainfall;


    if (!rain.available) {

        return (
            `Unavailable (` +
            `${
                rain.error
                ||
                "rainfall service error"
            })`
        );
    }


    return (

        `${number(
            rain.average_annual_rainfall_mm,
            1
        )} mm/year `

        +

        `(${rain.period})`
    );
}


// =========================================================
// RESULT PANEL
// =========================================================

function showResults(data) {

    const terrain =
        data.terrain;


    const land =
        data.land_filter;


    const best =

        data.candidates.find(

            candidate =>

                candidate.candidate_id
                ===
                data.recommended_candidate_id
        )

        ||

        data.candidates[0];


    const waterFeatureCount = (

        (
            land.excluded_feature_counts.water
            || 0
        )

        +

        (
            land.excluded_feature_counts.waterway
            || 0
        )
    );


    const roadBuildingCount = (

        (
            land.excluded_feature_counts.road
            || 0
        )

        +

        (
            land.excluded_feature_counts.building
            || 0
        )
    );


    document
        .getElementById(
            "results"
        )
        .innerHTML = `


        <h2>
            Analysis Result
        </h2>


        <div class="result-grid">


            <div>
                <strong>Contours</strong>

                <span>
                    ${terrain.contour_line_count}
                </span>
            </div>


            <div>
                <strong>Elevation</strong>

                <span>

                    ${number(
                        terrain.minimum_elevation_m,
                        1
                    )}

                    –

                    ${number(
                        terrain.maximum_elevation_m,
                        1
                    )}

                    m

                </span>
            </div>


            <div>
                <strong>Grid Resolution</strong>

                <span>
                    ${number(
                        terrain.grid_resolution_m,
                        1
                    )} m
                </span>
            </div>


            <div>
                <strong>Candidate Sites</strong>

                <span>
                    ${data.candidates.length}
                </span>
            </div>


            <div class="wide">

                <strong>
                    Historical Rainfall
                </strong>

                <span>
                    ${rainfallText(data)}
                </span>

            </div>


            <div class="wide">

                <strong>
                    Original Contour Overlay
                </strong>

                <span>
                    Brown lines are read directly
                    from the uploaded KML.
                    Hover over a contour to see
                    its AMSL elevation.
                </span>

            </div>


            <div class="wide">

                <strong>
                    Land Filter
                </strong>

                <span>
                    OSM water, waterways,
                    roads and buildings excluded
                </span>

            </div>


            <div>
                <strong>
                    Mapped Water Features
                </strong>

                <span>
                    ${waterFeatureCount}
                </span>
            </div>


            <div>
                <strong>
                    Mapped Roads/Buildings
                </strong>

                <span>
                    ${roadBuildingCount}
                </span>
            </div>


            <div>
                <strong>
                    Excluded Grid Cells
                </strong>

                <span>
                    ${integer(
                        land.excluded_cell_count
                    )}
                </span>
            </div>


            <div>
                <strong>
                    Free Grid Cells
                </strong>

                <span>
                    ${integer(
                        land.free_cell_count
                    )}
                </span>
            </div>


        </div>


        ${
            best

            ?

            `

            <hr>


            <h3>
                Top Candidate —
                Site ${best.rank}
            </h3>


            <div class="result-grid">


                <div>

                    <strong>
                        Score
                    </strong>

                    <span>
                        ${number(
                            best.suitability_score,
                            2
                        )}
                        /100
                    </span>

                </div>


                <div>

                    <strong>
                        Slope
                    </strong>

                    <span>
                        ${number(
                            best.slope_percent,
                            2
                        )}
                        %
                    </span>

                </div>


                <div>

                    <strong>
                        Catchment
                    </strong>

                    <span>
                        ${number(
                            best.catchment.area_hectares,
                            2
                        )}
                        ha
                    </span>

                </div>


                <div>

                    <strong>
                        Flow Accumulation
                    </strong>

                    <span>
                        ${integer(
                            best.flow_accumulation_cells
                        )}
                        cells
                    </span>

                </div>


                <div>

                    <strong>
                        Pond Area Estimate
                    </strong>

                    <span>
                        ${integer(
                            best.water.pond_area_m2
                        )}
                        m²
                    </span>

                </div>


                <div>

                    <strong>
                        Recommended Depth
                    </strong>

                    <span>
                        ${number(
                            best.water.recommended_depth_m,
                            2
                        )}
                        m
                    </span>

                </div>


                <div>

                    <strong>
                        Storage Capacity
                    </strong>

                    <span>
                        ${integer(
                            best.water.estimated_storage_capacity_m3
                        )}
                        m³
                    </span>

                </div>


                <div>

                    <strong>
                        Annual Runoff
                    </strong>

                    <span>
                        ${integer(
                            best.water.estimated_annual_runoff_m3
                        )}
                        m³/year
                    </span>

                </div>


            </div>


            <hr>


            <p>

                <strong>
                    Coordinates
                </strong>

                <br>

                ${best.latitude.toFixed(6)},
                ${best.longitude.toFixed(6)}

            </p>


            <p class="notice">
                ${best.land_status}
            </p>

            `

            :

            ""
        }
    `;
}


// =========================================================
// CANDIDATE POPUP
// =========================================================

function candidatePopup(
    candidate,
    rainfall
) {

    const annualRain =

        rainfall.available

        ?

        `${number(
            rainfall.average_annual_rainfall_mm,
            1
        )} mm/year`

        :

        "Unavailable";


    return `

        <div class="popup-content">

            <b>
                Candidate ${candidate.rank}
            </b>

            <br>

            Score:
            ${number(
                candidate.suitability_score,
                2
            )}
            /100

            <br>

            Elevation:
            ${number(
                candidate.elevation_m,
                2
            )}
            m AMSL

            <br>

            Slope:
            ${number(
                candidate.slope_percent,
                2
            )}
            %

            <br>

            Catchment:
            ${number(
                candidate.catchment.area_hectares,
                2
            )}
            ha

            <br>

            Flow accumulation:
            ${integer(
                candidate.flow_accumulation_cells
            )}
            cells

            <br>

            Rainfall:
            ${annualRain}

            <br>

            Annual runoff:
            ${integer(
                candidate.water.estimated_annual_runoff_m3
            )}
            m³/year

            <br>

            Pond area:
            ${integer(
                candidate.water.pond_area_m2
            )}
            m²

            <br>

            Depth:
            ${number(
                candidate.water.recommended_depth_m,
                2
            )}
            m

            <br>

            Storage:
            ${integer(
                candidate.water.estimated_storage_capacity_m3
            )}
            m³

        </div>
    `;
}


// =========================================================
// DRAW ANALYSIS
// =========================================================

function drawAnalysis(data) {

    clearAnalysisLayers();


    // -----------------------------------------------------
    // CONTOUR MAP ANALYSIS BOUNDARY
    // -----------------------------------------------------

    const boundary = addLayer(

        L.geoJSON(

            data.boundary_geojson,

            {

                style: {

                    color:
                        MAP_COLORS.boundary,

                    weight:
                        3,

                    dashArray:
                        "8 5",

                    fillColor:
                        MAP_COLORS.boundary,

                    fillOpacity:
                        0.02
                }
            }

        ).bindPopup(

            `
            <b>Contour Map Analysis Boundary</b>

            <br>

            Area represented by the uploaded
            contour map.
            `
        )
    );


    // -----------------------------------------------------
    // EXCLUDED AREAS
    // -----------------------------------------------------

    if (
        data.excluded_areas_geojson
    ) {

        const excluded =
            L.geoJSON(

                data.excluded_areas_geojson,

                {

                    style: {

                        color:
                            MAP_COLORS.excludedBorder,

                        weight:
                            1.5,

                        fillColor:
                            MAP_COLORS.excludedFill,

                        fillOpacity:
                            0.27
                    }
                }

            ).bindPopup(

                `
                <b>Excluded Area</b>

                <br>

                Pond candidate is not allowed here.

                <br><br>

                Includes mapped river,
                water body, road,
                building or safety clearance.
                `
            );


        addLayer(excluded);
    }


    // -----------------------------------------------------
    // TOP CANDIDATE CATCHMENT
    // -----------------------------------------------------

    if (
        data.recommended_catchment_geojson
    ) {

        const catchment =
            L.geoJSON(

                data.recommended_catchment_geojson,

                {

                    style: {

                        color:
                            MAP_COLORS.catchmentBorder,

                        weight:
                            2.5,

                        fillColor:
                            MAP_COLORS.catchmentFill,

                        fillOpacity:
                            0.28
                    }
                }

            ).bindPopup(

                `
                <b>Calculated Catchment Area</b>

                <br>

                All terrain in this region is
                estimated to drain toward the
                top-ranked pond candidate
                according to the D8 model.

                <br><br>

                Turn on the original contour
                overlay to visually verify the
                surrounding terrain elevations.
                `
            );


        addLayer(catchment);
    }


    // -----------------------------------------------------
    // CANDIDATE MARKERS
    // -----------------------------------------------------

    data.candidates.forEach(

        candidate => {


            const isBest = (

                candidate.candidate_id

                ===

                data.recommended_candidate_id
            );


            const marker =
                L.circleMarker(

                    [
                        candidate.latitude,
                        candidate.longitude
                    ],

                    {

                        radius:
                            isBest
                            ? 10
                            : 6,

                        color:
                            isBest
                            ?
                            MAP_COLORS.bestCandidateBorder
                            :
                            MAP_COLORS.candidateBorder,

                        fillColor:
                            isBest
                            ?
                            MAP_COLORS.bestCandidateFill
                            :
                            MAP_COLORS.candidateFill,

                        weight:
                            isBest
                            ? 3
                            : 2,

                        fillOpacity:
                            0.9
                    }
                );


            marker.bindPopup(

                candidatePopup(
                    candidate,
                    data.rainfall
                )
            );


            marker.bindTooltip(

                isBest

                ?

                `Best Pond Site — Rank ${candidate.rank}`

                :

                `Possible Pond Site — Rank ${candidate.rank}`,

                {
                    direction:
                        "top"
                }
            );


            addLayer(marker);


            candidateMarkers.set(

                candidate.candidate_id,

                marker
            );
        }
    );


    map.fitBounds(

        boundary.getBounds(),

        {
            padding:
                [20, 20]
        }
    );
}


// =========================================================
// CANDIDATE TABLE
// =========================================================

function renderCandidateTable(data) {

    const rows =

        data.candidates.map(

            candidate => `

            <tr>

                <td>
                    ${candidate.rank}
                </td>

                <td>
                    ${number(
                        candidate.suitability_score,
                        1
                    )}
                </td>

                <td>
                    ${candidate.latitude.toFixed(6)}
                    <br>
                    ${candidate.longitude.toFixed(6)}
                </td>

                <td>
                    ${number(
                        candidate.elevation_m,
                        1
                    )}
                </td>

                <td>
                    ${number(
                        candidate.slope_percent,
                        2
                    )}
                </td>

                <td>
                    ${number(
                        candidate.catchment.area_hectares,
                        2
                    )}
                </td>

                <td>
                    ${integer(
                        candidate.water.estimated_annual_runoff_m3
                    )}
                </td>

                <td>
                    ${integer(
                        candidate.water.pond_area_m2
                    )}
                </td>

                <td>
                    ${number(
                        candidate.water.recommended_depth_m,
                        1
                    )}
                </td>

                <td>
                    ${integer(
                        candidate.water.estimated_storage_capacity_m3
                    )}
                </td>

                <td>

                    <button
                        class="site-button"
                        data-candidate-id="${candidate.candidate_id}"
                    >
                        Show
                    </button>

                </td>

            </tr>

        `).join("");


    document
        .getElementById(
            "candidateTableWrap"
        )
        .innerHTML = `

        <div class="table-scroll">

            <table class="candidate-table">

                <thead>

                    <tr>

                        <th>Rank</th>

                        <th>Score</th>

                        <th>Coordinates</th>

                        <th>
                            Elevation
                            (m AMSL)
                        </th>

                        <th>Slope (%)</th>

                        <th>Catchment (ha)</th>

                        <th>
                            Annual Runoff (m³)
                        </th>

                        <th>Pond Area (m²)</th>

                        <th>Depth (m)</th>

                        <th>Storage (m³)</th>

                        <th>Map</th>

                    </tr>

                </thead>


                <tbody>
                    ${rows}
                </tbody>

            </table>

        </div>


        <p class="notice">

            <strong>
                Terrain verification:
            </strong>

            Brown contour lines are taken
            directly from the uploaded KML.

            Hover over any contour to view
            its elevation in metres AMSL.

            This allows the calculated
            catchment to be compared directly
            with the original contour map.

        </p>
    `;


    document
        .querySelectorAll(
            ".site-button"
        )
        .forEach(

            button => {

                button.addEventListener(

                    "click",

                    () => {

                        const id =
                            Number(
                                button.dataset.candidateId
                            );


                        const candidate =
                            data.candidates.find(

                                item =>
                                    item.candidate_id
                                    === id
                            );


                        const marker =
                            candidateMarkers.get(id);


                        if (
                            candidate
                            &&
                            marker
                        ) {

                            map.setView(

                                [
                                    candidate.latitude,
                                    candidate.longitude
                                ],

                                Math.max(
                                    map.getZoom(),
                                    16
                                )
                            );


                            marker.openPopup();


                            document
                                .getElementById(
                                    "map"
                                )
                                .scrollIntoView({

                                    behavior:
                                        "smooth",

                                    block:
                                        "center"
                                });
                        }
                    }
                );
            }
        );
}


// =========================================================
// ANALYZE BUTTON
// =========================================================

document
    .getElementById(
        "analyzeBtn"
    )
    .addEventListener(

        "click",

        async () => {


            const fileInput =
                document.getElementById(
                    "contourFile"
                );


            const status =
                document.getElementById(
                    "status"
                );


            if (
                !fileInput.files.length
            ) {

                status.textContent =
                    "Please choose a KML or KMZ file first.";

                return;
            }


            const uploadedFile =
                fileInput.files[0];


            // =================================================
            // DRAW THE ORIGINAL CONTOURS FIRST
            //
            // This is completely separate from
            // backend hydrological analysis.
            // =================================================

            try {

                await drawOriginalContours(
                    uploadedFile
                );

            } catch (error) {

                console.error(
                    "Contour overlay error:",
                    error
                );
            }


            // =================================================
            // BUILD BACKEND REQUEST
            // =================================================

            const form =
                new FormData();


            form.append(
                "file",
                uploadedFile
            );


            form.append(

                "resolution_m",

                document
                    .getElementById(
                        "resolution"
                    )
                    .value

                ||

                "10"
            );


            form.append(

                "max_candidates",

                document
                    .getElementById(
                        "maxCandidates"
                    )
                    .value

                ||

                "20"
            );


            form.append(

                "rainfall_years",

                document
                    .getElementById(
                        "rainfallYears"
                    )
                    .value

                ||

                "5"
            );


            form.append(

                "runoff_coefficient",

                document
                    .getElementById(
                        "runoffCoefficient"
                    )
                    .value

                ||

                "0.30"
            );


            form.append(

                "pond_radius_m",

                document
                    .getElementById(
                        "pondRadius"
                    )
                    .value

                ||

                "40"
            );


            form.append(

                "max_pond_depth_m",

                document
                    .getElementById(
                        "maxDepth"
                    )
                    .value

                ||

                "3"
            );


            status.textContent =

                "Overlaying original contours and "
                +
                "analyzing terrain, land exclusions, "
                +
                "catchment and rainfall...";


            try {


                const response =
                    await fetch(

                        "/api/analyzeContour",

                        {

                            method:
                                "POST",

                            body:
                                form
                        }
                    );


                const data =
                    await response.json();


                if (!response.ok) {

                    throw new Error(

                        data.detail
                        ||
                        "Analysis failed"
                    );
                }


                showResults(data);

                drawAnalysis(data);

                renderCandidateTable(data);

                if (typeof window.onAnalysisComplete === 'function') {
                    window.onAnalysisComplete(data, 'upload');
                }

                status.textContent =

                    "Analysis completed successfully. "

                    +

                    `${data.candidates.length} `

                    +

                    "candidate site(s) returned. "

                    +

                    "Original contour lines are overlaid "
                    +

                    "on the real-world map.";


            } catch (error) {


                status.textContent =
                    `Error: ${error.message}`;

                // Dismiss progress overlay so it doesn't stay stuck at 95%
                if (typeof window.onAnalysisError === 'function') {
                    window.onAnalysisError(`Error: ${error.message}`, 'upload');
                } else if (typeof onAnalysisError === 'function') {
                    onAnalysisError(`Error: ${error.message}`, 'upload');
                }
            }
        }
    );
