// =========================================================
// AUTOMATIC LOCATION ANALYSIS
//
// LOCATION NAME
//      ↓
// GEOCODING
//      ↓
// OPENTOPOGRAPHY DEM
//      ↓
// AUTOMATIC CONTOUR GENERATION
//      ↓
// EXISTING HYDROLOGY ENGINE
//      ↓
// POND CANDIDATES
// =========================================================


// =========================================================
// DRAW AUTOMATICALLY GENERATED CONTOURS
// =========================================================
//
// These contours are different from the instructor's
// uploaded KML.
//
// They are generated from the OpenTopography COP30 DEM.
//
// They use the same contour overlay layers already created
// inside app.js:
//
// originalContourLayer
// contourLabelLayer
//
// =========================================================

function drawGeneratedContours(contourData) {


    // -----------------------------------------------------
    // Remove old contour lines
    // -----------------------------------------------------

    originalContourLayer.clearLayers();

    contourLabelLayer.clearLayers();



    // -----------------------------------------------------
    // Validate GeoJSON
    // -----------------------------------------------------

    if (
        !contourData
        ||
        !contourData.features
    ) {

        console.warn(
            "No generated contour GeoJSON received."
        );

        return;
    }



    // -----------------------------------------------------
    // We only want one visible label per elevation.
    //
    // Example:
    //
    // 270 m
    // 275 m
    // 280 m
    //
    // Many contour segments may exist at the same elevation.
    // -----------------------------------------------------

    const bestLabelFeature =
        new Map();



    let contourCount = 0;



    // =====================================================
    // LOOP THROUGH GENERATED CONTOUR FEATURES
    // =====================================================

    contourData.features.forEach(

        feature => {


            // -------------------------------------------------
            // Read elevation
            // -------------------------------------------------

            const elevation =
                Number(
                    feature.properties?.elevation_m
                );


            // -------------------------------------------------
            // Read geometry
            // -------------------------------------------------

            const coordinates =
                feature.geometry?.coordinates
                || [];



            if (
                !Number.isFinite(
                    elevation
                )
                ||
                coordinates.length < 2
            ) {

                return;
            }



            // -------------------------------------------------
            // GeoJSON:
            //
            // [longitude, latitude]
            //
            // Leaflet:
            //
            // [latitude, longitude]
            // -------------------------------------------------

            const latLngs =
                coordinates.map(

                    ([longitude, latitude]) => [

                        latitude,

                        longitude

                    ]
                );



            // =================================================
            // CREATE CONTOUR LINE
            // =================================================

            const line =
                L.polyline(

                    latLngs,

                    {

                        color:
                            MAP_COLORS.contour,

                        weight:
                            1.3,

                        opacity:
                            0.82
                    }
                );



            // -------------------------------------------------
            // Hover shows elevation
            // -------------------------------------------------

            line.bindTooltip(

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



            // -------------------------------------------------
            // Click gives detailed information
            // -------------------------------------------------

            line.bindPopup(

                `

                <b>
                    Generated Terrain Contour
                </b>

                <br>

                Elevation:

                <strong>
                    ${elevation.toFixed(1)} m AMSL
                </strong>

                <br><br>

                This contour was generated automatically
                from the OpenTopography Copernicus COP30
                elevation dataset.

                `
            );



            line.addTo(
                originalContourLayer
            );



            contourCount++;



            // =================================================
            // FIND BEST LOCATION FOR LABEL
            // =================================================

            const key =
                elevation.toFixed(2);



            const existing =
                bestLabelFeature.get(
                    key
                );



            if (
                !existing
                ||
                coordinates.length
                >
                existing.coordinates.length
            ) {

                bestLabelFeature.set(

                    key,

                    {

                        elevation:
                            elevation,

                        coordinates:
                            coordinates

                    }
                );
            }
        }
    );



    // =====================================================
    // ADD ELEVATION LABELS
    // =====================================================

    for (
        const item
        of bestLabelFeature.values()
    ) {


        const coordinates =
            item.coordinates;



        // Choose the middle point of a contour segment
        const midpoint =

            coordinates[
                Math.floor(
                    coordinates.length / 2
                )
            ];



        const longitude =
            midpoint[0];


        const latitude =
            midpoint[1];



        // -------------------------------------------------
        // Create label
        // -------------------------------------------------

        const icon =
            L.divIcon({

                className:
                    "contour-elevation-label",

                html:

                    `<span>
                        ${item.elevation.toFixed(0)} m
                    </span>`,

                iconSize:
                    null
            });



        L.marker(

            [
                latitude,
                longitude
            ],

            {

                interactive:
                    false,

                icon:
                    icon
            }

        ).addTo(
            contourLabelLayer
        );
    }



    console.log(

        `Generated contour lines drawn: ${contourCount}`

    );
}



// =========================================================
// DISPLAY AUTOMATICALLY GENERATED FILES
// =========================================================

function showGeneratedFiles(data) {


    const panel =
        document.getElementById(
            "generatedFiles"
        );



    const place =
        data.location_search;



    const source =
        data.source_dem;



    const files =
        data.generated_files;



    // -----------------------------------------------------
    // Defensive check
    // -----------------------------------------------------

    if (
        !place
        ||
        !source
        ||
        !files
    ) {

        panel.innerHTML =
            `
            <p class="warning">
                Generated-file information
                was not returned by the backend.
            </p>
            `;

        return;
    }



    panel.innerHTML = `


        <div class="generated-summary">


            <strong>
                Resolved Location:
            </strong>

            ${place.resolved_name}


            <br>



            <strong>
                Centre Coordinates:
            </strong>

            ${Number(
                place.latitude
            ).toFixed(6)},

            ${Number(
                place.longitude
            ).toFixed(6)}


            <br>



            <strong>
                Analysis Radius:
            </strong>

            ${Number(
                place.analysis_radius_m
            ).toLocaleString()}

            m


            <br>



            <strong>
                DEM Dataset:
            </strong>

            ${source.dataset}


            <br>



            <strong>
                DEM Source:
            </strong>

            ${source.vertical_source}


            <br>



            <strong>
                Nominal DEM Resolution:
            </strong>

            approximately

            ${source.nominal_horizontal_resolution_m}

            m


            <br>



            <strong>
                Hydrology Grid Resolution:
            </strong>

            ${source.analysis_grid_resolution_m}

            m


        </div>



        <div class="generated-links">


            <a
                href="${files.dem_url}"
                download
            >

                Download DEM GeoTIFF

            </a>



            <a
                href="${files.contour_kml_url}"
                download
            >

                Download Generated Contour KML

            </a>



            <a
                href="${files.contour_geojson_url}"
                download
            >

                Download Contour GeoJSON

            </a>


        </div>


    `;
}



// =========================================================
// BUILD REQUEST FOR LOCATION ANALYSIS
// =========================================================

function buildLocationAnalysisForm() {


    const form =
        new FormData();



    // =====================================================
    // LOCATION PARAMETERS
    // =====================================================

    form.append(

        "location_name",

        document
            .getElementById(
                "locationName"
            )
            .value
            .trim()

    );



    form.append(

        "analysis_radius_m",

        document
            .getElementById(
                "analysisRadius"
            )
            .value

        ||

        "3000"

    );



    form.append(

        "contour_interval_m",

        document
            .getElementById(
                "autoContourInterval"
            )
            .value

        ||

        "5"

    );



    // =====================================================
    // EXISTING HYDROLOGY SETTINGS
    //
    // These are shared with manual KML analysis.
    // =====================================================

    form.append(

        "resolution_m",

        document
            .getElementById(
                "resolution"
            )
            .value

        ||

        "30"

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



    return form;
}



// =========================================================
// AUTOMATIC LOCATION ANALYSIS BUTTON
// =========================================================

document
    .getElementById(
        "analyzeLocationBtn"
    )
    .addEventListener(

        "click",

        async () => {


            // -------------------------------------------------
            // Read location
            // -------------------------------------------------

            const locationName =

                document
                    .getElementById(
                        "locationName"
                    )
                    .value
                    .trim();



            const status =

                document
                    .getElementById(
                        "locationStatus"
                    );



            const generatedFiles =

                document
                    .getElementById(
                        "generatedFiles"
                    );



            // =================================================
            // VALIDATE LOCATION
            // =================================================

            if (!locationName) {


                status.textContent =

                    "Please enter a location name first.";


                return;
            }



            // Clear previous generated-file information

            generatedFiles.innerHTML =
                "";



            // =================================================
            // SHOW PROGRESS
            // =================================================

            status.textContent =

                "Step 1/6: Finding the location...";



            try {


                // =================================================
                // PREPARE FORM DATA
                // =================================================

                const form =
                    buildLocationAnalysisForm();



                status.textContent =

                    (
                        "Finding location → "
                        +
                        "downloading OpenTopography DEM → "
                        +
                        "generating contours → "
                        +
                        "calculating hydrology → "
                        +
                        "checking free land → "
                        +
                        "calculating rainfall and pond sites..."
                    );



                // =================================================
                // CALL BACKEND
                // =================================================

                const response =

                    await fetch(

                        "/api/analyzeLocation",

                        {

                            method:
                                "POST",

                            body:
                                form
                        }
                    );



                // =================================================
                // READ RESPONSE
                // =================================================

                let data;


                try {

                    data =
                        await response.json();

                } catch (error) {

                    throw new Error(

                        "Backend returned an invalid response."

                    );
                }



                // =================================================
                // ERROR RESPONSE
                // =================================================

                if (!response.ok) {


                    throw new Error(

                        data.detail

                        ||

                        "Automatic location analysis failed."

                    );
                }



                // =================================================
                // DRAW GENERATED CONTOURS
                // =================================================

                if (
                    data.generated_contours
                    &&
                    data.generated_contours.geojson
                ) {


                    drawGeneratedContours(

                        data
                            .generated_contours
                            .geojson

                    );
                }



                // =================================================
                // DISPLAY NORMAL ANALYSIS RESULTS
                //
                // These functions already exist in app.js
                // =================================================

                showResults(
                    data
                );


                drawAnalysis(
                    data
                );


                renderCandidateTable(
                    data
                );



                // =================================================
                // DISPLAY DEM / CONTOUR DOWNLOAD LINKS
                // =================================================

                showGeneratedFiles(
                    data
                );


                if (typeof window.onAnalysisComplete === 'function') {
                    window.onAnalysisComplete(data, 'location');
                }


                // =================================================
                // SUCCESS MESSAGE
                // =================================================

                const resolvedName =

                    data
                        .location_search
                        ?.resolved_name

                    ||

                    locationName;



                status.textContent =

                    (
                        "Analysis completed successfully for "
                        +
                        resolvedName
                        +
                        ". "
                        +
                        data.candidates.length
                        +
                        " possible pond candidate site(s) found."
                    );



            } catch (error) {


                console.error(
                    error
                );



                status.textContent =

                    `Error: ${error.message}`;

                // Ensure progress overlay is dismissed and UI returns to idle
                // (otherwise it stays stuck at 95% "Ranking optimal pond locations...")
                if (typeof window.onAnalysisError === 'function') {
                    window.onAnalysisError(`Error: ${error.message}`, 'location');
                } else if (typeof onAnalysisError === 'function') {
                    onAnalysisError(`Error: ${error.message}`, 'location');
                }
            }
        }
    );
