document.addEventListener('DOMContentLoaded', function () {
    const chartDom = document.getElementById('main-chart');
    const spotsTableBody = document.getElementById('data-table-body');
    const spotsTable = document.getElementById('data-table');
    const clearButton = document.getElementById('clear_spots_button');
    const addLassoButton = document.getElementById('add_lasso_selection_button');
    const exportCsvButton = document.getElementById('export_csv_button');
    const annotateNeuroglancerButton = document.getElementById('annotate_neuroglancer_button');
    const labelInput = document.getElementById('label_input');
    const activeLabelDisplay = document.getElementById('active_label_display');
    const prevChannelButton = document.getElementById('prev_channel_pair');
    const nextChannelButton = document.getElementById('next_channel_pair');
    const currentChannelDisplay = document.getElementById('current_channel_display');
    const sampleSizeInput = document.getElementById('sample-size-input');
    const samplingTypeSelect = document.getElementById('sampling-type-select');
    const resampleButton = document.getElementById('resample_button');
    const sampleSizeNote = document.getElementById('sample_size_note');
    const sampleSizeIcon = document.getElementById('sample_size_icon');
    const sampleSizeText = document.getElementById('sample_size_text');
    const refreshButton = document.getElementById('refresh_button');
    const highlightReassignedToggle = document.getElementById('highlight_reassigned_toggle');
    const highlightStatus = document.getElementById('highlight_status');
    const highlightRemovedToggle = document.getElementById('highlight_removed_toggle');
    const highlightRemovedStatus = document.getElementById('highlight_removed_status');
    const displayChanSelect = document.getElementById('display_chan_select');
    // const validSpotToggle = document.getElementById('valid_spot_toggle');
    // const validSpotStatus = document.getElementById('valid_spot_status');
    const xlimMin = document.getElementById('xlim_min');
    const xlimMax = document.getElementById('xlim_max');
    const ylimMin = document.getElementById('ylim_min');
    const ylimMax = document.getElementById('ylim_max');
    const limitsAutoButton = document.getElementById('limits_auto');
    const limitsFixedButton = document.getElementById('limits_fixed');
    const limitsMinMaxButton = document.getElementById('limits_minmax');
    const limitsPercentileButton = document.getElementById('limits_percentile');
    const summaryBarChartDom = document.getElementById('summary-bar-chart');
    const summaryHeatmapDom = document.getElementById('summary-heatmap');
    const futureChartDom = document.getElementById('future-chart');
    const spotsContainerHeader = document.getElementById('spots_container_header');
    const spotsContainerContent = document.getElementById('spots_container_content');
    const spotsContainerToggle = document.getElementById('spots_container_toggle');
    const selectedSpotsCount = document.getElementById('selected_spots_count');
    const neuroglancerClickedCount = document.getElementById('neuroglancer_clicked_count');
    const clearNeuroglancerClicksBtn = document.getElementById('clear_neuroglancer_clicks_btn');
    
    const myChart = echarts.init(chartDom);
    const summaryBarChart = echarts.init(summaryBarChartDom);
    const summaryHeatmap = echarts.init(summaryHeatmapDom);
    const futureChart = echarts.init(futureChartDom);
    
    let option;
    let currentLabel = '';
    let lassoSelectedData = [];
    let allChartData = [];
    let channelPairs = [];
    let currentPairIndex = 0;
    let currentSampleSize = parseInt(sampleSizeInput.value) || 10000;
    let samplingType = 'class_balanced'; // 'class_balanced' or 'random'
    let highlightReassigned = false;
    let highlightRemoved = false;
    let displayChanMode = 'mixed'; // 'unmixed' or 'mixed'
    let isNeuroglancerMode = false;
    let showDyeLines = false; // Toggle state for dye lines
    let spotDetails = {}; // Will store the spot details for neuroglancer lookup
    let fusedS3Paths = {}; // Will store the fused S3 paths from the API
    let summaryStats = null; // Will store the summary stats from the API
    let ratiosMatrix = null; // Will store the ratios matrix from the API
    let sankeyData = null; // Will store the sankey flow data from the API
    let selectedSpots = new Set();
    let currentDatasetName = 'Unknown Dataset'; // Track current dataset name
    
    // Chart limits variables
    let chartLimitsMode = 'auto'; // 'auto', 'fixed', 'minmax', 'percentile'
    let currentXLimits = [0, 2000];
    let currentYLimits = [0, 2000];
    
    // Filter ranges for R Value and Distance
    let rValueRange = [0, 100];
    let distanceRange = [0, 100];
    let rValueFilter = [0, 100];
    let distanceFilter = [0, 100];
    
    // Neuroglancer click debounce variables
    let lastNeuroglancerClickTime = 0;
    let lastNeuroglancerSpotId = null;
    const NEUROGLANCER_CLICK_DEBOUNCE_MS = 1000; // Prevent duplicate clicks within 1 second
    let neuroglancerClickedSpots = new Set(); // Track clicked spot IDs for visual indication
    
    // Large data threshold - samples above this will use optimized rendering
    const LARGE_DATA_THRESHOLD = 25001;
    
    // Define color mapping for unmixed channels
    const COLORS = {
        '488': '#4CAF50',    // Green
        '514': '#F44336',    // Red
        '561': '#2196F3',    // Blue
        '594': '#00BCD4',    // Cyan
        '638': '#9C27B0',    // Purple
        'ambiguous': '#9E9E9E', // Grey
        'none': '#607D8B',    // Blue Grey
        'Removed': 'rgba(0, 0, 0, 0.5)', // Black with 50% alpha for removed spots
        'default': '#2196F3'  // Blue (default)
    };

    // Dataset management variables and elements
    const datasetNameInput = document.getElementById('dataset_name_input');
    const downloadDatasetBtn = document.getElementById('download_dataset_btn');
    const datasetTableBody = document.getElementById('dataset_table_body');
    const loadDatasetBtn = document.getElementById('load_dataset_btn');
    const redownloadDatasetBtn = document.getElementById('redownload_dataset_btn');
    let selectedDataset = null;
    let datasetList = [];

    // Dataset management functions
    function loadDatasetList() {
        fetch('/api/datasets')
            .then(response => response.json())
            .then(data => {
                if (data.datasets) {
                    datasetList = data.datasets;
                    updateDatasetTable();
                } else {
                    console.error('Failed to load dataset list:', data);
                }
            })
            .catch(error => {
                console.error('Error loading dataset list:', error);
                showDatasetMessage('Error loading dataset list: ' + error.message, 'error');
            });
    }

    let dataTable = null; // Store DataTables instance
    
    function updateDatasetTable() {
        // Destroy existing DataTable if it exists
        if (dataTable) {
            dataTable.destroy();
        }
        
        // Clear table body
        datasetTableBody.innerHTML = '';
        
        datasetList.forEach(dataset => {
            const row = document.createElement('tr');
            row.dataset.datasetName = dataset.name;
            
            if (dataset.is_current) {
                row.classList.add('current-dataset');
            }
            
            // Status indicator
            let statusClass, statusText;
            if (dataset.is_current) {
                statusClass = 'status-current';
                statusText = 'Current';
            } else if (dataset.has_data) {
                statusClass = 'status-cached';
                statusText = 'Cached';
            } else {
                statusClass = 'status-missing';
                statusText = 'Missing';
            }
            
            // No truncation - show full dataset name
            // Format date to show only date (YYYY-MM-DD) without time
            const dateOnly = dataset.creation_date.split(' ')[0];
            row.innerHTML = `
                <td title="${dataset.name}">${dataset.name}</td>
                <td title="${dataset.creation_date}">${dateOnly}</td>
                <td><span class="status-indicator ${statusClass}"></span>${statusText}</td>
            `;
            
            row.addEventListener('click', () => selectDataset(dataset.name, row));
            datasetTableBody.appendChild(row);
        });
        
        // Initialize DataTables with custom configuration
        dataTable = $('#dataset_table').DataTable({
            paging: false, // Disable pagination since we have limited datasets
            searching: true, // Enable search box
            ordering: true, // Enable column sorting
            info: false, // Hide "Showing X to Y of Z entries" text
            scrollX: false, // Disable horizontal scrolling
            autoWidth: false, // Disable auto width calculation
            columnDefs: [
                { width: "80%", targets: 0, className: "text-wrap" }, // Dataset Name column
                { width: "10%", targets: 1 }, // Date Added column
                { width: "10%", targets: 2, orderable: false } // Status column (no sorting)
            ],
            language: {
                search: "Filter datasets:",
                searchPlaceholder: "e.g., HCR_76710"
            },
            order: [[1, 'desc']] // Sort by Date Added (newest first) by default
        });
    }

    function selectDataset(datasetName, rowElement) {
        // Remove previous selection
        document.querySelectorAll('#dataset_table tbody tr').forEach(row => {
            row.classList.remove('selected');
        });
        
        // Add selection to clicked row
        rowElement.classList.add('selected');
        selectedDataset = datasetName;
        
        // Enable/disable buttons based on selection
        const dataset = datasetList.find(d => d.name === datasetName);
        if (dataset) {
            loadDatasetBtn.disabled = dataset.is_current || !dataset.has_data;
            redownloadDatasetBtn.disabled = false;
        }
    }

    function showDatasetMessage(message, type = 'info') {
        // Create a simple message display (you can style this further)
        const messageDiv = document.createElement('div');
        messageDiv.style.position = 'fixed';
        messageDiv.style.top = '20px';
        messageDiv.style.right = '20px';
        messageDiv.style.padding = '10px 15px';
        messageDiv.style.borderRadius = '4px';
        messageDiv.style.zIndex = '10000';
        messageDiv.style.fontSize = '14px';
        messageDiv.style.maxWidth = '400px';
        messageDiv.style.wordWrap = 'break-word';
        
        if (type === 'error') {
            messageDiv.style.backgroundColor = '#ffebee';
            messageDiv.style.color = '#c62828';
            messageDiv.style.border = '1px solid #ef5350';
        } else if (type === 'success') {
            messageDiv.style.backgroundColor = '#e8f5e9';
            messageDiv.style.color = '#2e7d32';
            messageDiv.style.border = '1px solid #4caf50';
        } else {
            messageDiv.style.backgroundColor = '#e3f2fd';
            messageDiv.style.color = '#1565c0';
            messageDiv.style.border = '1px solid #2196f3';
        }
        
        messageDiv.textContent = message;
        document.body.appendChild(messageDiv);
        
        // Remove after 5 seconds
        setTimeout(() => {
            if (messageDiv.parentNode) {
                messageDiv.parentNode.removeChild(messageDiv);
            }
        }, 5000);
    }

    // Function to update the dataset title display
    function updateDatasetTitle(datasetName) {
        console.log('updateDatasetTitle called with:', datasetName);
        const titleElement = document.getElementById('dataset-title');
        const nameSpan = titleElement.querySelector('.dataset-name');
        
        console.log('titleElement:', titleElement);
        console.log('nameSpan:', nameSpan);
        
        if (datasetName === null) {
            // No dataset selected - show selection prompt
            console.log('No dataset selected, showing prompt');
            titleElement.classList.remove('loading');
            nameSpan.textContent = 'Please select a dataset ➡️';
            return;
        }
        
        if (!datasetName || datasetName === 'Unknown Dataset') {
            console.log('No valid dataset name, showing loading state');
            titleElement.classList.add('loading');
            nameSpan.textContent = 'Loading dataset...';
            return;
        }
        
        // Remove loading state
        titleElement.classList.remove('loading');
        
        // Format the dataset name for better readability
        // Extract key parts: HCR_ID, capture date, processing date
        const parts = datasetName.split('_');
        let formattedName = datasetName;
        
        if (parts.length >= 3 && parts[0] === 'HCR') {
            const hcrId = parts[1];
            const captureDate = parts[2]; // YYYY-MM-DD format
            formattedName = `HCR ${hcrId} (${captureDate})`;
        }
        
        // Update the display
        //nameSpan.textContent = formattedName;
        nameSpan.textContent = datasetName; // Show full name, not formatted MJD
        nameSpan.title = datasetName; // Full name in tooltip
        
        // Store current dataset name
        currentDatasetName = datasetName;
        
        console.log(`Dataset title updated: ${formattedName}`);
    }

    // Dataset management event listeners
    downloadDatasetBtn.addEventListener('click', function() {
        const datasetName = datasetNameInput.value.trim();
        if (!datasetName) {
            showDatasetMessage('Please enter a dataset name', 'error');
            return;
        }
        
        downloadDatasetBtn.disabled = true;
        downloadDatasetBtn.textContent = 'Downloading...';
        
        fetch('/api/datasets/download', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ dataset_name: datasetName })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showDatasetMessage(`Successfully downloaded dataset: ${data.dataset_name}`, 'success');
                datasetNameInput.value = '';
                loadDatasetList(); // Refresh the list
            } else {
                showDatasetMessage(data.error || 'Failed to download dataset', 'error');
                if (data.checked_path) {
                    console.log('Checked path:', data.checked_path);
                }
            }
        })
        .catch(error => {
            console.error('Error downloading dataset:', error);
            showDatasetMessage('Error downloading dataset: ' + error.message, 'error');
        })
        .finally(() => {
            downloadDatasetBtn.disabled = false;
            downloadDatasetBtn.textContent = 'Download';
        });
    });

    loadDatasetBtn.addEventListener('click', function() {
        if (!selectedDataset) {
            showDatasetMessage('Please select a dataset first', 'error');
            return;
        }
        
        loadDatasetBtn.disabled = true;
        loadDatasetBtn.textContent = 'Loading...';
        
        fetch('/api/datasets/set-active', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ dataset_name: selectedDataset })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // showDatasetMessage(`Successfully loaded dataset: ${data.dataset_name}`, 'success');
                loadDatasetList(); // Refresh the list to show new current dataset
                
                // Refresh the main data display
                setTimeout(() => {
                    refreshData(true);
                }, 1000);
            } else {
                showDatasetMessage(data.error || 'Failed to load dataset', 'error');
            }
        })
        .catch(error => {
            console.error('Error loading dataset:', error);
            showDatasetMessage('Error loading dataset: ' + error.message, 'error');
        })
        .finally(() => {
            loadDatasetBtn.disabled = false;
            loadDatasetBtn.textContent = 'Load Dataset';
        });
    });

    redownloadDatasetBtn.addEventListener('click', function() {
        if (!selectedDataset) {
            showDatasetMessage('Please select a dataset first', 'error');
            return;
        }
        
        if (!confirm(`Are you sure you want to redownload "${selectedDataset}"? This will overwrite any cached data.`)) {
            return;
        }
        
        redownloadDatasetBtn.disabled = true;
        redownloadDatasetBtn.textContent = 'Redownloading...';
        
        fetch('/api/datasets/download', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ dataset_name: selectedDataset })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showDatasetMessage(`Successfully redownloaded dataset: ${data.dataset_name}`, 'success');
                loadDatasetList(); // Refresh the list
            } else {
                showDatasetMessage(data.error || 'Failed to redownload dataset', 'error');
            }
        })
        .catch(error => {
            console.error('Error redownloading dataset:', error);
            showDatasetMessage('Error redownloading dataset: ' + error.message, 'error');
        })
        .finally(() => {
            redownloadDatasetBtn.disabled = false;
            redownloadDatasetBtn.textContent = 'Redownload';
        });
    });

    // Initialize dataset management
    loadDatasetList();
    
    // Setup noUiSlider elements
    const rValueSliderEl = document.getElementById('r_value_slider');
    const distanceSliderEl = document.getElementById('distance_slider');
    const markerSizeSliderEl = document.getElementById('marker_size_slider');
    const rValueMinLabel = document.getElementById('r_value_min_label');
    const rValueMaxLabel = document.getElementById('r_value_max_label');
    const distanceMinLabel = document.getElementById('distance_min_label');
    const distanceMaxLabel = document.getElementById('distance_max_label');
    const markerSizeMinLabel = document.getElementById('marker_size_min_label');
    const markerSizeMaxLabel = document.getElementById('marker_size_max_label');
    const resetFiltersBtn = document.getElementById('reset_filters_btn');
    const dyeLinesToggle = document.getElementById('dye_lines_toggle');
    const dyeLinesStatus = document.getElementById('dye_lines_status');
    
    let rValueSlider = null;
    let distanceSlider = null;
    let markerSizeSlider = null;
    let markerSizeMultiplier = 1.0; // Default marker size multiplier
    
    // Function to update filter slider ranges based on data
    function updateFilterSliderRanges() {
        if (!allChartData || allChartData.length === 0) return;
        if (typeof noUiSlider === 'undefined') {
            console.error('noUiSlider library not loaded');
            return;
        }
        
        const rValues = allChartData.typedArrays.r;
        const distValues = allChartData.typedArrays.dist;
        
        // Calculate 99th percentile for better range
        const rSorted = new Float32Array(rValues).sort();
        const distSorted = new Float32Array(distValues).sort();
        
        const r99 = rSorted[Math.floor(rSorted.length * 0.99)] || 1.0;
        const dist99 = distSorted[Math.floor(distSorted.length * 0.99)] || 5.0;
        
        // Cap at reasonable maximums: R at 1.0, Distance at 5.0
        const rMax = Math.min(r99, 1.0);
        const distMax = Math.min(dist99, 5.0);
        
        rValueRange = [0, rMax];
        distanceRange = [0, distMax];
        rValueFilter = [0, rMax];
        distanceFilter = [0, distMax];
        
        // Destroy existing sliders if they exist
        if (rValueSlider) {
            rValueSlider.destroy();
        }
        if (distanceSlider) {
            distanceSlider.destroy();
        }
        if (markerSizeSlider) {
            markerSizeSlider.destroy();
        }
        
        // Create R Value slider
        rValueSlider = noUiSlider.create(rValueSliderEl, {
            start: [0, rMax],
            connect: true,
            range: {
                'min': 0,
                'max': rMax
            },
            step: 0.01,
            tooltips: [true, true],
            format: {
                to: function(value) {
                    return value.toFixed(2);
                },
                from: function(value) {
                    return Number(value);
                }
            }
        });
        
        // Create Distance slider
        distanceSlider = noUiSlider.create(distanceSliderEl, {
            start: [0, distMax],
            connect: true,
            range: {
                'min': 0,
                'max': distMax
            },
            step: 0.05,
            tooltips: [true, true],
            format: {
                to: function(value) {
                    return value.toFixed(2);
                },
                from: function(value) {
                    return Number(value);
                }
            }
        });
        
        // Update labels
        rValueMinLabel.textContent = '0.00';
        rValueMaxLabel.textContent = rMax.toFixed(2);
        distanceMinLabel.textContent = '0.00';
        distanceMaxLabel.textContent = distMax.toFixed(2);
        
        // Add event listeners for R Value slider - use 'set' event to avoid excessive updates
        rValueSlider.on('set', function(values, handle) {
            rValueFilter[0] = parseFloat(values[0]);
            rValueFilter[1] = parseFloat(values[1]);
            updateChart();
        });
        
        // Add event listeners for Distance slider - use 'set' event to avoid excessive updates
        distanceSlider.on('set', function(values, handle) {
            distanceFilter[0] = parseFloat(values[0]);
            distanceFilter[1] = parseFloat(values[1]);
            updateChart();
        });
        
        // Create Marker Size slider (0.5x to 3.0x)
        markerSizeSlider = noUiSlider.create(markerSizeSliderEl, {
            start: [1.0],
            connect: [true, false],
            range: {
                'min': 0.5,
                'max': 3.0
            },
            step: 0.1,
            tooltips: [true],
            format: {
                to: function(value) {
                    return value.toFixed(1) + '×';
                },
                from: function(value) {
                    return Number(value.replace('×', ''));
                }
            }
        });
        
        // Add event listener for Marker Size slider
        markerSizeSlider.on('set', function(values, handle) {
            markerSizeMultiplier = parseFloat(values[0]);
            updateChart();
        });
        
        console.log(`Filter ranges - R: [0, ${rMax.toFixed(2)}], Distance: [0, ${distMax.toFixed(2)}]`);
    }
    
    // Clear neuroglancer clicked spots button
    clearNeuroglancerClicksBtn.addEventListener('click', function() {
        neuroglancerClickedSpots.clear();
        neuroglancerClickedCount.textContent = '0';
        updateChart();
        console.log('Cleared all neuroglancer clicked spots');
    });
    
    // Reset filters button
    resetFiltersBtn.addEventListener('click', function() {
        if (rValueSlider && distanceSlider) {
            rValueSlider.set([0, rValueRange[1]]);
            distanceSlider.set([0, distanceRange[1]]);
            
            rValueFilter = [0, rValueRange[1]];
            distanceFilter = [0, distanceRange[1]];
            
            if (markerSizeSlider) {
                markerSizeSlider.set([1.0]);
                markerSizeMultiplier = 1.0;
            }
            
            updateChart();
        }
    });

    // Dye lines toggle event listener
    dyeLinesToggle.addEventListener('change', function() {
        showDyeLines = this.checked;
        dyeLinesStatus.textContent = showDyeLines ? 'On' : 'Off';
        
        // Update toggle style
        const toggleLabel = this.nextElementSibling;
        const toggleSpan = toggleLabel.querySelector('span');
        
        if (showDyeLines) {
            toggleLabel.style.backgroundColor = '#2196F3'; // Blue when active
            toggleSpan.style.left = '22px';
        } else {
            toggleLabel.style.backgroundColor = '#ccc'; // Gray when inactive
            toggleSpan.style.left = '2px';
        }
        
        updateChart();
        console.log(`Dye lines toggle: ${showDyeLines ? 'ON' : 'OFF'}`);
    });

    // Toggle collapsible Selected Spots section
    spotsContainerHeader.addEventListener('click', function() {
        const isCollapsed = spotsContainerContent.classList.contains('collapsed');
        
        if (isCollapsed) {
            spotsContainerContent.classList.remove('collapsed');
            spotsContainerToggle.classList.remove('collapsed');
        } else {
            spotsContainerContent.classList.add('collapsed');
            spotsContainerToggle.classList.add('collapsed');
        }
    });

    // Function to update the selected spots count
    function updateSelectedSpotsCount() {
        const count = spotsTableBody.rows.length;
        selectedSpotsCount.textContent = count;
    }

    // Update current label when input changes
    labelInput.addEventListener('input', function() {
        currentLabel = labelInput.value.trim();
        activeLabelDisplay.textContent = currentLabel ? `(${currentLabel})` : '(None)';
        console.log("Current label set to:", currentLabel || 'None');
    });

    // Navigate through channel pairs
    prevChannelButton.addEventListener('click', function() {
        if (channelPairs.length === 0) return;
        currentPairIndex = (currentPairIndex - 1 + channelPairs.length) % channelPairs.length;
        updateChannelSelector();
        updateChart();
    });

    nextChannelButton.addEventListener('click', function() {
        if (channelPairs.length === 0) return;
        currentPairIndex = (currentPairIndex + 1) % channelPairs.length;
        updateChannelSelector();
        updateChart();
    });
    
    // Handle resample button click
    resampleButton.addEventListener('click', function() {
        // Get the new sample size
        const newSampleSize = parseInt(sampleSizeInput.value);
        if (isNaN(newSampleSize) || newSampleSize < 100) {
            alert("Please enter a valid sample size (minimum 100)");
            return;
        }
        
        if (newSampleSize > LARGE_DATA_THRESHOLD) {
            if (!confirm(`Warning: Large sample size (${newSampleSize}) may affect performance. Continue?`)) {
                return;
            }
        }
        
        currentSampleSize = newSampleSize;
        const selectedValue = samplingTypeSelect.value;
        samplingType = selectedValue; // Get current sampling type
        console.log(`Resample clicked: dropdown value = ${selectedValue}, samplingType = ${samplingType}, displayChanMode = ${displayChanMode}`);
        console.log(`Dropdown element:`, samplingTypeSelect);
        updateSampleSizeNote(currentSampleSize);
        
        // Show loading state
        myChart.showLoading({
            text: `Loading new sample (${samplingType})...`,
            maskColor: 'rgba(255, 255, 255, 0.8)',
            fontSize: 14
        });
        
        // Fetch data with new sample size and sampling type
        fetchData(currentSampleSize, false);
    });

    // Handle refresh button click (force reload data from server)
    if (refreshButton) {
        refreshButton.addEventListener('click', function() {
            if (confirm("This will reload data from the server. Continue?")) {
                refreshData(true);
            }
        });
    }

    // Add function for refresh button
    function refreshData(forceRefresh = true) {
        console.log(`Forcing data refresh from server: ${forceRefresh}`);
        
        // Show loading state
        myChart.showLoading({
            text: 'Refreshing data from server...',
            maskColor: 'rgba(255, 255, 255, 0.8)',
            fontSize: 14
        });
        
        // Fetch fresh data from server
        fetchData(currentSampleSize, forceRefresh);
    }
    
    // Function to update the sample size note
    function updateSampleSizeNote(sampleSize) {
        const isLargeSample = sampleSize >= LARGE_DATA_THRESHOLD;
        
        if (isLargeSample) {
            sampleSizeNote.style.backgroundColor = '#ffebee'; // Light red
            sampleSizeIcon.textContent = '✕';
            sampleSizeIcon.style.color = '#f44336'; // Red
            sampleSizeText.textContent = `Large sample (${sampleSize.toLocaleString()}): Some features disabled`;
        } else {
            sampleSizeNote.style.backgroundColor = '#e8f5e9'; // Light green
            sampleSizeIcon.textContent = '✓';
            sampleSizeIcon.style.color = '#4caf50'; // Green
            sampleSizeText.textContent = `Small sample (${sampleSize.toLocaleString()}): All features enabled`;
        }
    }
    
    // Initial sample size note update
    updateSampleSizeNote(currentSampleSize);
    
    // Don't fetch data on initial load - wait for user to select a dataset
    // fetchData(currentSampleSize, false);
    
    // Initialize button states
    updateButtonStates();
    
    // Fetch data function
    function fetchData(sampleSize, forceRefresh = false) {
        const validSpotsOnly = false; // validSpotToggle.checked; // Toggle disabled
        const url = `/api/real_spots_data?sample_size=${sampleSize}&sampling_type=${samplingType}&display_chan=${displayChanMode}${forceRefresh ? '&force_refresh=true' : ''}${validSpotsOnly ? '&valid_spots_only=true' : '&valid_spots_only=false'}`;
        console.log(`Fetching data with URL: ${url} (sampling: ${samplingType}, display: ${displayChanMode})`);
        
        fetch(url)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! Status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                console.log(`Fetched spots data with sample size ${sampleSize}:`, data);
                console.log('Current dataset from API:', data.current_dataset);
                
                // Check if no dataset is selected
                if (data.no_dataset_selected) {
                    console.log('No dataset selected:', data.message);
                    updateDatasetTitle(null);  // Show "Please select a dataset" message
                    myChart.hideLoading();
                    return;
                }
                
                if (!data.spots_data || !data.channel_pairs || data.spots_data.length === 0) {
                    throw new Error("Invalid or empty data received from API");
                }
                
                // Update dataset title if available
                if (data.current_dataset) {
                    console.log('Calling updateDatasetTitle with:', data.current_dataset);
                    updateDatasetTitle(data.current_dataset);
                } else {
                    console.warn('No current_dataset field in API response');
                }

                channelPairs = data.channel_pairs;
                const spotsData = data.spots_data;
                
                // Store spot details if available
                if (data.spot_details) {
                    spotDetails = data.spot_details;
                    console.log(`Received spot details for ${Object.keys(spotDetails).length} spots`);
                }
                
                // Store fused S3 paths if available
                if (data.fused_s3_paths) {
                    fusedS3Paths = data.fused_s3_paths;
                    console.log(`Received fused S3 paths:`, fusedS3Paths);
                }
                
                // Store summary stats if available
                if (data.summary_stats) {
                    summaryStats = data.summary_stats;
                    console.log(`Received summary stats:`, summaryStats);
                }
                
                // Store ratios matrix if available
                if (data.ratios) {
                    ratiosMatrix = data.ratios;
                    console.log(`Received ratios matrix:`, ratiosMatrix);
                }
                
                // Store sankey data if available
                if (data.sankey_data) {
                    sankeyData = data.sankey_data;
                    console.log(`Received sankey data:`, sankeyData);
                }
                
                // Process the data for chart
                processDataAndRenderChart(spotsData);
                
                // Update summary charts if data is available
                updateSummaryCharts();
                
                // Hide loading indicator
                myChart.hideLoading();
            })
            .catch(error => {
                console.error('Error fetching or processing unmixed spots data:', error);
                chartDom.innerHTML = `<div style="padding: 20px; color: red;">
                    Error loading chart data: ${error.message}
                </div>`;
                myChart.hideLoading();
            });
    }

    function processDataAndRenderChart(spotsData) {
        if (channelPairs.length === 0) {
            console.error("No channel pairs available");
            return;
        }

        // Always convert to typed arrays for better performance
        convertToTypedArrays(spotsData);

        // Set allChartData before calling updateFilterSliderRanges
        // so the sliders have data to work with
        allChartData = spotsData;

        // Create channel selector buttons
        createChannelSelector();
        
        // Update filter slider ranges based on data
        updateFilterSliderRanges();

        // Set initial channel pair
        currentPairIndex = 0;
        updateChart();  // Don't pass spotsData since allChartData is already set
    }

    function createChannelSelector() {
        const channelSelector = document.getElementById('channel-selector');
        channelSelector.innerHTML = ''; // Clear existing buttons
        
        channelPairs.forEach((pair, index) => {
            const button = document.createElement('button');
            button.textContent = `${pair[0]} vs ${pair[1]}`;
            button.dataset.index = index;
            
            if (index === currentPairIndex) {
                button.classList.add('active');
            }
            
            button.addEventListener('click', function() {
                // Remove active class from all buttons
                channelSelector.querySelectorAll('button').forEach(btn => {
                    btn.classList.remove('active');
                });
                
                // Add active class to clicked button
                this.classList.add('active');
                
                // Update current pair index and chart
                currentPairIndex = parseInt(this.dataset.index);
                updateChart();
            });
            
            channelSelector.appendChild(button);
        });
    }

    function updateChannelSelector() {
        const channelSelector = document.getElementById('channel-selector');
        const buttons = channelSelector.querySelectorAll('button');
        
        buttons.forEach((button, index) => {
            if (index === currentPairIndex) {
                button.classList.add('active');
            } else {
                button.classList.remove('active');
            }
        });
    }

    // Convert data to typed arrays for better performance
    function convertToTypedArrays(spotsData) {
        console.log("Converting data to typed arrays for better performance");
        
        // Extract all intensity columns
        const intensityColumns = [];
        for (const pair of channelPairs) {
            intensityColumns.push(`chan_${pair[0]}_intensity`);
            intensityColumns.push(`chan_${pair[1]}_intensity`);
        }
        
        // Create a unique set of columns
        const uniqueIntensityColumns = [...new Set(intensityColumns)];
        
        // Create typed arrays for each intensity column
        const typedArrays = {};
        for (const column of uniqueIntensityColumns) {
            typedArrays[column] = new Float32Array(spotsData.length);
        }
        
        // Also create typed arrays for r and dist values
        typedArrays.r = new Float32Array(spotsData.length);
        typedArrays.dist = new Float32Array(spotsData.length);
        
        // Array for spot IDs (can't use typed arrays for strings)
        const spotIds = new Array(spotsData.length);
        const channels = new Array(spotsData.length);
        const unmixedChans = new Array(spotsData.length);
        const reassigned = new Array(spotsData.length);
        const removed = new Array(spotsData.length);
        
        // Fill typed arrays
        for (let i = 0; i < spotsData.length; i++) {
            const dataPoint = spotsData[i];
            
            // Fill intensity values
            for (const column of uniqueIntensityColumns) {
                typedArrays[column][i] = dataPoint[column] || 0;
            }
            
            // Fill r and dist values
            typedArrays.r[i] = dataPoint.r || 0;
            typedArrays.dist[i] = dataPoint.dist || 0;
            
            // Store non-numeric data
            spotIds[i] = dataPoint.spot_id;
            channels[i] = dataPoint.chan;
            unmixedChans[i] = dataPoint.unmixed_chan || 'none';
            reassigned[i] = dataPoint.reassigned || false;
            removed[i] = dataPoint.unmixed_removed || false;
        }
        
        // Attach typed arrays to spotsData object for later use
        spotsData.typedArrays = typedArrays;
        spotsData.spotIds = spotIds;
        spotsData.channels = channels;
        spotsData.unmixedChans = unmixedChans;
        spotsData.reassigned = reassigned;
        spotsData.removed = removed;
    }

    // Function to apply R Value and Distance filters
    function applyFilters(data) {
        const filtered = [];
        const rValues = data.typedArrays.r;
        const distValues = data.typedArrays.dist;
        
        for (let i = 0; i < data.length; i++) {
            const r = rValues[i];
            const dist = distValues[i];
            
            // Check if point passes both filters
            if (r >= rValueFilter[0] && r <= rValueFilter[1] &&
                dist >= distanceFilter[0] && dist <= distanceFilter[1]) {
                filtered.push(i);
            }
        }
        
        return filtered;
    }

    /**
     * Clip a line passing through origin with direction (dx, dy) to axis boundaries.
     * Returns the two intersection points where the line enters/exits the visible rectangle.
     * 
     * @param {number} dx - X component of line direction (normalized)
     * @param {number} dy - Y component of line direction (normalized)
     * @param {Array<number>} xLimits - [xMin, xMax] for x-axis
     * @param {Array<number>} yLimits - [yMin, yMax] for y-axis
     * @returns {Object} Object with clipped {x0, y0, x1, y1} or null if no intersection
     */
    function clipLineToAxes(dx, dy, xLimits, yLimits) {
        const [xMin, xMax] = xLimits;
        const [yMin, yMax] = yLimits;
        
        // Line through origin: (x, y) = t * (dx, dy) for any scalar t
        // Find all t values where line intersects the four boundaries
        const tValues = [];
        
        // Intersection with x = xMin: t = xMin / dx (if dx != 0)
        if (Math.abs(dx) > 1e-9) {
            const t = xMin / dx;
            const y = t * dy;
            if (y >= yMin && y <= yMax) {
                tValues.push({ t, x: xMin, y });
            }
        }
        
        // Intersection with x = xMax: t = xMax / dx
        if (Math.abs(dx) > 1e-9) {
            const t = xMax / dx;
            const y = t * dy;
            if (y >= yMin && y <= yMax) {
                tValues.push({ t, x: xMax, y });
            }
        }
        
        // Intersection with y = yMin: t = yMin / dy (if dy != 0)
        if (Math.abs(dy) > 1e-9) {
            const t = yMin / dy;
            const x = t * dx;
            if (x >= xMin && x <= xMax) {
                tValues.push({ t, x, y: yMin });
            }
        }
        
        // Intersection with y = yMax: t = yMax / dy
        if (Math.abs(dy) > 1e-9) {
            const t = yMax / dy;
            const x = t * dx;
            if (x >= xMin && x <= xMax) {
                tValues.push({ t, x, y: yMax });
            }
        }
        
        // Need at least 2 intersections (line enters and exits rectangle)
        if (tValues.length < 2) {
            return null;
        }
        
        // Sort by t value and take the two extremes (smallest and largest t)
        tValues.sort((a, b) => a.t - b.t);
        const start = tValues[0];
        const end = tValues[tValues.length - 1];
        
        return {
            x0: start.x,
            y0: start.y,
            x1: end.x,
            y1: end.y
        };
    }

    /**
     * Calculate dye line endpoints for the current channel pair.
     * The ratios matrix contains learned dye spectral signatures.
     * Lines are clipped to the visible axis boundaries.
     * 
     * @param {string} xChan - X-axis channel (e.g., "488")
     * @param {string} yChan - Y-axis channel (e.g., "514")
     * @param {Array<Array<number>>} ratiosMatrix - NxN matrix of dye coefficients
     * @param {Array<string>} channels - Ordered list of channel names
     * @param {Array<number>} xLimits - [min, max] for x-axis
     * @param {Array<number>} yLimits - [min, max] for y-axis
     * @returns {Array<Object>} Array of dye line objects with endpoints and styling
     */
    function calculateDyeLines(xChan, yChan, ratiosMatrix, channels, xLimits, yLimits) {
        if (!ratiosMatrix || ratiosMatrix.length === 0) {
            console.warn('No ratios matrix available for dye lines');
            return [];
        }
        
        // Get indices of current channels
        const xIdx = channels.indexOf(xChan);
        const yIdx = channels.indexOf(yChan);
        
        if (xIdx === -1 || yIdx === -1) {
            console.warn(`Channel indices not found: x=${xChan} (idx=${xIdx}), y=${yChan} (idx=${yIdx})`);
            return [];
        }
        
        const dyeLines = [];
        const numDyes = ratiosMatrix.length;
        
        console.log(`Calculating dye lines: xChan=${xChan} (idx=${xIdx}), yChan=${yChan} (idx=${yIdx})`);
        console.log(`Axis limits: x=[${xLimits[0]}, ${xLimits[1]}], y=[${yLimits[0]}, ${yLimits[1]}]`);
        
        // For each dye (each row represents a dye's spectral signature)
        for (let d = 0; d < numDyes; d++) {
            // Extract 2D projection of this dye's direction
            // ratiosMatrix[d] is the d-th dye's coefficients across all channels
            const dx = ratiosMatrix[d][xIdx]; // Coefficient for x-channel
            const dy = ratiosMatrix[d][yIdx]; // Coefficient for y-channel
            
            // Normalize to unit length in this 2D subspace
            const norm = Math.sqrt(dx * dx + dy * dy);
            if (norm < 1e-9) {
                console.log(`Skipping dye ${d} (channel ${channels[d]}): near-zero norm (${norm})`);
                continue; // Skip near-zero vectors
            }
            
            const ux = dx / norm;
            const uy = dy / norm;
            
            // Clip the line to the visible axis boundaries
            const clipped = clipLineToAxes(ux, uy, xLimits, yLimits);
            
            if (!clipped) {
                console.log(`Skipping dye ${d} (channel ${channels[d]}): no intersection with visible area`);
                continue;
            }
            
            dyeLines.push({
                dyeIndex: d,
                channel: channels[d],
                x0: clipped.x0,
                y0: clipped.y0,
                x1: clipped.x1,
                y1: clipped.y1,
                color: COLORS[channels[d]] || COLORS.default,
                dx: dx,
                dy: dy,
                norm: norm
            });
            
            console.log(`Dye line ${d} (${channels[d]}): dx=${dx.toFixed(3)}, dy=${dy.toFixed(3)}, ` +
                       `clipped to [(${clipped.x0.toFixed(1)}, ${clipped.y0.toFixed(1)}) -> ` +
                       `(${clipped.x1.toFixed(1)}, ${clipped.y1.toFixed(1)})]`);
        }
        
        return dyeLines;
    }
    
    function updateChart(newData = null) {
        if (newData) {
            allChartData = newData;
        }
        
        if (allChartData.length === 0 || channelPairs.length === 0) {
            console.error("No data or channel pairs available for chart update");
            return;
        }

        // Get current channel pair
        const xChan = channelPairs[currentPairIndex][0];
        const yChan = channelPairs[currentPairIndex][1];
        
        // Update channel display
        currentChannelDisplay.textContent = `Channel: ${xChan} vs ${yChan}`;
        
        // Calculate 99th percentile values for r and dist for slider max
        // Sort copies of the typed arrays (sorting is in-place)
        const rValuesSorted = new Float32Array(allChartData.typedArrays.r);
        const distValuesSorted = new Float32Array(allChartData.typedArrays.dist);
        rValuesSorted.sort();
        distValuesSorted.sort();
        
        const r99Percentile = rValuesSorted[Math.floor(rValuesSorted.length * 0.99)] || 10;
        const dist99Percentile = distValuesSorted[Math.floor(distValuesSorted.length * 0.99)] || 10;
        
        console.log(`99th percentiles - r: ${r99Percentile.toFixed(2)}, dist: ${dist99Percentile.toFixed(2)}`);
        
        // Prepare data for scatter plot
        const xField = `chan_${xChan}_intensity`;
        const yField = `chan_${yChan}_intensity`;
        
        // Apply filters to get indices of points that pass
        const filteredIndices = applyFilters(allChartData);
        
        // Update filter count display
        const filterCountEl = document.getElementById('filter_count');
        if (filterCountEl) {
            filterCountEl.textContent = `Showing ${filteredIndices.length.toLocaleString()} of ${allChartData.length.toLocaleString()} points`;
        }
        
        // Create series data grouped by display channel (mixed or unmixed)
        const seriesData = {};
        const uniqueChannels = [];
        
        // Using typed arrays for better performance
        const xValues = allChartData.typedArrays[xField];
        const yValues = allChartData.typedArrays[yField];
        const rValues = allChartData.typedArrays.r;
        const distValues = allChartData.typedArrays.dist;
        const spotIds = allChartData.spotIds;
        const channels = allChartData.channels;
        const unmixedChans = allChartData.unmixedChans;
        const reassigned = allChartData.reassigned;
        const removed = allChartData.removed;
        
        // Only process filtered indices
        for (let idx = 0; idx < filteredIndices.length; idx++) {
            const i = filteredIndices[idx];
            // Use either unmixed channel or original channel based on display mode
            let displayChan = displayChanMode === 'mixed' ? channels[i] : unmixedChans[i];
            
            // Special handling: if unmixed channel is 'none', display as 'Removed'
            if (displayChanMode === 'unmixed' && (unmixedChans[i] === 'none' || unmixedChans[i] === null || unmixedChans[i] === undefined)) {
                displayChan = 'Removed';
            }
            
            // Add to unique channels if not already there
            if (!uniqueChannels.includes(displayChan)) {
                uniqueChannels.push(displayChan);
            }
            
            // Initialize series if not exists
            if (!seriesData[displayChan]) {
                seriesData[displayChan] = [];
            }
            
            // Add data point using typed array values for faster access
            seriesData[displayChan].push({
                name: `Spot ${spotIds[i]}`,
                value: [
                    xValues[i],            // x-coordinate (first channel intensity)
                    yValues[i],            // y-coordinate (second channel intensity)
                    rValues[i],            // size (r value)
                    channels[i],           // category (chan)
                    spotIds[i],            // ID
                    unmixedChans[i],       // unmixed channel
                    distValues[i],         // distance
                    xChan,                 // x-axis channel
                    yChan,                 // y-axis channel
                    reassigned[i],         // reassigned flag
                    removed[i]             // removed flag
                ]
            });
        }
        
        // Build series array for each channel with custom sorting
        // Put "Removed" series at the end for better visual hierarchy
        const sortedChannels = uniqueChannels.sort((a, b) => {
            if (a === 'Removed') return 1;
            if (b === 'Removed') return -1;
            return a.localeCompare(b);
        });
        
        const series = sortedChannels.map(channel => {
            // Start with series hidden if it's "Removed" in unmixed mode
            const isRemovedSeries = channel === 'Removed';
            const shouldHideByDefault = isRemovedSeries && displayChanMode === 'unmixed';
            
            return {
                name: channel, // Remove the Mixed/Unmixed prefix from individual labels
                type: 'scatter',
                // Hide "Removed" series by default in unmixed mode
                selected: !shouldHideByDefault,
                data: seriesData[channel].map(point => {
                const spotId = point.value[4];
                const isClicked = neuroglancerClickedSpots.has(spotId);
                const baseSize = (channel === 'Removed' ? 8 : 5) * markerSizeMultiplier;
                
                // Add symbol, symbolSize, and itemStyle to each data point
                const dataPoint = {
                    ...point,
                    symbol: isClicked ? 'pin' : (channel === 'Removed' ? 'triangle' : 'circle'),
                    symbolSize: isClicked ? baseSize * 4 : baseSize
                };
                
                // Add itemStyle overrides for clicked spots
                if (isClicked) {
                    dataPoint.itemStyle = {
                        borderWidth: 5,
                        borderColor: '#000000'
                    };
                }
                
                return dataPoint;
            }),
            // Add large dataset mode optimizations (but disable for Removed series to ensure visibility)
            large: channel !== 'Removed',
            largeThreshold: LARGE_DATA_THRESHOLD,
            itemStyle: {
                color: function(params) {
                    // Special handling for Removed series - always black with 50% alpha
                    if (channel === 'Removed') {
                        return 'rgba(0, 0, 0, 0.5)'; // Black with 50% alpha for removed spots
                    }
                    
                    const isReassigned = params.data.value[9];
                    const isRemoved = params.data.value[10];
                    
                    // If highlighting reassigned is active, show non-reassigned in gray
                    if (highlightReassigned && !isReassigned) {
                        return '#cccccc'; // Gray for non-reassigned when highlighting
                    }
                    
                    // If highlighting removed is active, show non-removed in gray
                    if (highlightRemoved && !isRemoved) {
                        return '#cccccc'; // Gray for non-removed when highlighting
                    }
                    
                    return COLORS[channel] || COLORS.default;
                },
                // Add visual styling for reassigned and removed spots
                borderWidth: function(params) {
                    const spotId = params.data.value[4];
                    const isReassigned = params.data.value[9];
                    const isRemoved = params.data.value[10];
                    
                    // Thick border for clicked spots
                    if (neuroglancerClickedSpots.has(spotId)) {
                        return 5;
                    }
                    
                    // Add border if reassigned or removed
                    if (isReassigned || isRemoved) {
                        return 2;
                    }
                    return 0;
                },
                borderColor: function(params) {
                    const spotId = params.data.value[4];
                    const isReassigned = params.data.value[9];
                    const isRemoved = params.data.value[10];
                    
                    // Black border for clicked spots
                    if (neuroglancerClickedSpots.has(spotId)) {
                        return '#000000';
                    }
                    
                    // Different border colors for different states
                    if (isReassigned && isRemoved) {
                        return '#ff00ff'; // Magenta for both reassigned and removed
                    } else if (isReassigned) {
                        return '#ffffff'; // White for reassigned
                    } else if (isRemoved) {
                        return '#000000'; // Black for removed
                    }
                    return '#ffffff';
                },
                borderType: 'solid',
                shadowBlur: function(params) {
                    const spotId = params.data.value[4];
                    if (neuroglancerClickedSpots.has(spotId)) {
                        return 15; // Glow effect for clicked spots
                    }
                    return 0;
                },
                shadowColor: function(params) {
                    const spotId = params.data.value[4];
                    if (neuroglancerClickedSpots.has(spotId)) {
                        return 'rgba(255, 255, 255, 0.8)'; // White glow
                    }
                    return 'transparent';
                },
                opacity: function(params) {
                    // If highlighting reassigned, make non-reassigned more transparent
                    if (highlightReassigned) {
                        const isReassigned = params.data.value[9];
                        return isReassigned ? 1 : 0.5; // Lower opacity for non-reassigned when highlighting
                    }
                    return params.data.value[9] ? 1 : 0.8; // Default: Higher opacity if reassigned
                }
            },
            emphasis: {
                focus: 'none',
                itemStyle: {
                    shadowBlur: 0, // Remove shadow effect on hover
                    borderWidth: 0 // No border on hover
                }
            },
            // Use fixed color for the legend
            color: COLORS[channel] || COLORS.default
        };
        });
        
        // Configuration for slider positioning and styling
        const sliderConfig = {
            // Space required for all sliders
            width: 60,   // Width of each slider
            gap: 20,     // Gap between sliders
            startRight: 20,  // Distance from right edge of chart

            // Move sliders lower in the plot area
            top: '45%', // Move sliders down (default is 'center')

            // Common slider properties
            itemWidth: 20,
            itemHeight: 200,
            textGap: 20,
            handleSize: 10,
            calculable: true,
            realtime: true,
            orient: 'vertical',
            handleIcon: 'path://M-11.39,9.77h0a3.5,3.5,0,0,0,3.5-3.5V-11.39a3.5,3.5,0,0,0-3.5-3.5h0a3.5,3.5,0,0,0-3.5,3.5V6.27A3.5,3.5,0,0,0-11.39,9.77Z'
        };
        
        // Total width needed for all sliders
        const totalSliderWidth = sliderConfig.width * 2 + sliderConfig.gap;

        // Get series indices for visualMap
        const seriesIndices = series.map((_, index) => index);
        
        // Add dye lines if enabled
        if (showDyeLines && ratiosMatrix && channelPairs.length > 0) {
            // Get all channels for matrix lookup
            let allChannels = [];
            if (typeof summaryStats !== 'undefined' && summaryStats && summaryStats.length > 0) {
                allChannels = summaryStats.map(stat => stat.channel.toString());
            } else {
                // Derive from channel pairs
                const uniqueChans = new Set();
                channelPairs.forEach(pair => {
                    uniqueChans.add(pair[0].toString());
                    uniqueChans.add(pair[1].toString());
                });
                allChannels = Array.from(uniqueChans).sort();
            }
            
            // Only plot dye lines for the current pair
            const currentPairChannels = [xChan.toString(), yChan.toString()];
            console.log(`Drawing dye lines for current pair only: ${currentPairChannels}`);
            
            // Determine axis limits for dye line scaling
            let xLims, yLims;
            if (chartLimitsMode === 'auto') {
                // Use data range for auto mode
                const xField = `chan_${xChan}_intensity`;
                const yField = `chan_${yChan}_intensity`;
                const xValues = allChartData.typedArrays[xField];
                const yValues = allChartData.typedArrays[yField];
                
                const xMax = Math.max(...xValues);
                const yMax = Math.max(...yValues);
                xLims = [0, xMax * 1.1]; // Add 10% padding
                yLims = [0, yMax * 1.1];
            } else {
                // Use the actual axis limits that will be applied
                xLims = currentXLimits;
                yLims = currentYLimits;
            }
            
            console.log(`Dye line scaling: x=[${xLims[0]}, ${xLims[1].toFixed(1)}], y=[${yLims[0]}, ${yLims[1].toFixed(1)}]`);
            
            const dyeLines = calculateDyeLines(
                xChan.toString(),
                yChan.toString(),
                ratiosMatrix,
                allChannels,
                xLims,
                yLims
            );
            
            // Filter to only include dye lines for the current pair
            const filteredDyeLines = dyeLines.filter(line => 
                currentPairChannels.includes(line.channel)
            );
            
            if (filteredDyeLines.length > 0) {
                console.log(`Adding ${filteredDyeLines.length} dye lines to chart (filtered from ${dyeLines.length} total)`);
                
                // Add each dye line as a separate series
                filteredDyeLines.forEach(line => {
                    series.push({
                        name: `Dye: ${line.channel}`,
                        type: 'line',
                        data: [[line.x0, line.y0], [line.x1, line.y1]],
                        lineStyle: {
                            color: line.color,
                            width: 4,
                            type: 'solid',
                            opacity: 0.9
                        },
                        itemStyle: {
                            color: line.color  // Ensure marker color matches line color
                        },
                        symbol: 'none',
                        symbolSize: 0,
                        emphasis: {
                            disabled: true
                        },
                        zlevel: 10, // Render on top of scatter points
                        silent: true, // Don't respond to mouse events
                        animation: false,
                        clip: false, // Keep rendering even when points are outside axis range
                        // Show only line in legend (no marker)
                        legendHoverLink: false,
                        showSymbol: false,
                        // Add text label at endpoint
                        markPoint: {
                            symbol: 'none',
                            data: [{
                                coord: [line.x1, line.y1],
                                symbol: 'none',
                                symbolSize: 0,
                                itemStyle: {
                                    opacity: 0
                                },
                                label: {
                                    show: true,
                                    formatter: line.channel,
                                    position: 'top',
                                    fontSize: 14,
                                    fontWeight: 'bold',
                                    color: line.color,
                                    backgroundColor: 'rgba(255, 255, 255, 0.9)',
                                    padding: 3,
                                    borderRadius: 3
                                }
                            }]
                        }
                    });
                });
            } else {
                console.log('No valid dye lines calculated for current pair');
            }
        }

        option = {
            // title: {
            //     text: `Intensity Scatter Plot: Channel ${xChan} vs ${yChan}`,
            //     textStyle: {
            //         fontSize: 16,
            //         fontWeight: 'bold'
            //     }
            // },
            // Add a separate title element for the legend
            graphic: [{
                type: 'text',
                right: 80, // Position it above the legend
                top: 60,
                style: {
                    text: displayChanMode === 'mixed' ? 'Mixed' : 'Unmixed',
                    fontSize: 16,
                    fontWeight: 'bold',
                    fill: '#333'
                }
            }],
            color: sortedChannels.map(chan => COLORS[chan] || COLORS.default), // Fixed colors for legend
            legend: {
                type: 'scroll',
                orient: 'vertical',
                right: 45,
                top: 85, // Move down to make room for graphic title
                bottom: 50,
                textStyle: {
                    fontSize: 14
                },
                selected: sortedChannels.reduce((acc, chan) => {
                    // Hide "Removed" by default in unmixed mode
                    const isRemovedSeries = chan === 'Removed';
                    const shouldHideByDefault = isRemovedSeries && displayChanMode === 'unmixed';
                    acc[chan] = !shouldHideByDefault;
                    return acc;
                }, {})
            },
            grid: {
                right: 120, // Space for legend only
                bottom: 70 // Space for axis labels
            },
            tooltip: {
                trigger: 'item',
                formatter: function (params) {
                    const item = params.data.value;
                    const reassignedText = item[9] ? '<span style="color:red;font-weight:bold">⚠ Reassigned</span>' : '';
                    const removedText = item[10] ? '<span style="color:orange;font-weight:bold">🗑 Removed</span>' : '';
                    const statusText = [reassignedText, removedText].filter(t => t).join('<br/>');
                    
                    return `<div style="font-size: 14px;">
                           ID: ${item[4]}<br/>
                           ${xChan} Intensity: ${item[0].toFixed(2)}<br/>
                           ${yChan} Intensity: ${item[1].toFixed(2)}<br/>
                           R: ${item[2].toFixed(2)}<br/>
                           Mixed Chan: ${item[3]}<br/>
                           Unmixed Chan: ${item[5]}<br/>
                           Dist: ${item[6].toFixed(2)}<br/>
                           ${statusText ? statusText + '<br/>' : ''}
                           </div>`;
                },
                textStyle: {
                    fontSize: 14
                }
            },
            toolbox: {
                feature: {
                    dataZoom: {
                        yAxisIndex: 0,
                        title: {
                            zoom: 'Box Zoom',
                            back: 'Reset Zoom'
                        },
                        icon: {
                            zoom: 'path://M15,15 L25,15 L25,25 L15,25 Z M10,10 M0,0 L30,0 L30,30 L0,30 Z M12,12 L18,12 L18,8 L21,8 L21,12 L27,12 L27,15 L21,15 L21,19 L27,19 L27,22 L21,22 L21,26 L18,26 L18,22 L12,22 L12,19 L18,19 L18,15 L12,15 Z'
                        }
                    },
                    brush: {
                        type: ['polygon', 'clear']
                    },
                    restore: {},
                    saveAsImage: {}
                }
            },
            brush: {
                toolbox: ['polygon'],
                throttleType: 'debounce',
                throttleDelay: 300,
                brushStyle: {
                    borderWidth: 1,
                    color: 'rgba(120,140,180,0.3)',
                    borderColor: 'rgba(120,140,180,0.8)'
                }
            },
            xAxis: { 
                type: 'value', 
                name: `${xChan} Intensity`,
                nameLocation: 'middle',
                nameGap: 30,
                nameTextStyle: {
                    fontSize: 20,
                    fontWeight: 'bold'
                },
                axisLabel: {
                    fontSize: 16,
                    formatter: function(value) {
                        return Math.round(value);
                    }
                }
            },
            yAxis: { 
                type: 'value', 
                name: `${yChan} Intensity`,
                nameLocation: 'middle',
                nameGap: 40,
                nameTextStyle: {
                    fontSize: 20,
                    fontWeight: 'bold'
                },
                axisLabel: {
                    fontSize: 16,
                    formatter: function(value) {
                        return Math.round(value);
                    }
                }
            },
            dataZoom: [
                {
                    type: 'inside',
                    xAxisIndex: 0,
                    filterMode: 'empty'
                },
                {
                    type: 'inside',
                    yAxisIndex: 0,
                    filterMode: 'empty'
                }
            ],
            series: series
        };

        // Apply chart limits based on current mode
        if (chartLimitsMode !== 'auto') {
            option.xAxis.min = currentXLimits[0];
            option.xAxis.max = currentXLimits[1];
            option.yAxis.min = currentYLimits[0];
            option.yAxis.max = currentYLimits[1];
        }

        // Initialize chart with all options
        myChart.setOption(option, true);
        
        // Ensure chart fills its container properly
        setTimeout(() => myChart.resize(), 0);
        
        // Store current processed data for lasso selection
        const allProcessedData = [].concat(...Object.values(seriesData));

        // Click event for adding to table (single point)
        myChart.on('click', function (params) {
            console.log("Chart click detected:", params);
            
            if (params.componentType === 'series') {
                const itemData = params.data.value;
                
                // Log the click data for debugging
                console.log("Click data:", {
                    spotId: itemData[4],
                    inNeuroglancerMode: isNeuroglancerMode,
                    hasSpotDetails: spotDetails[itemData[4]] ? true : false
                });
                
                if (isNeuroglancerMode) {
                    handleNeuroglancerClick(itemData);
                } else if (!myChart.getModel().getComponent('brush')) {
                    addSpotToTable(itemData, currentLabel);
                }
            }
        });

        // Brush (lasso) selection event
        myChart.on('brushSelected', function (params) {
            lassoSelectedData = [];
            if (params.batch && params.batch[0] && params.batch[0].selected) {
                // Combine all selected indices from all series
                let allSelectedIndices = [];
                params.batch[0].selected.forEach(item => {
                    if (item.dataIndex && item.dataIndex.length) {
                        // Track which series this selection came from
                        const seriesIndex = item.seriesIndex;
                        const seriesName = option.series[seriesIndex].name;
                        const seriesData = option.series[seriesIndex].data;
                        
                        item.dataIndex.forEach(index => {
                            if (seriesData[index]) {
                                lassoSelectedData.push(seriesData[index].value);
                            }
                        });
                    }
                });
                console.log("Lasso selected data count:", lassoSelectedData.length);
            } else {
                console.log("Lasso selection cleared or empty.");
            }
        });
    }

    // Function to add spot details to the table
    function addSpotToTable(itemData, label) {
        const newRow = spotsTableBody.insertRow();
        newRow.insertCell().textContent = itemData[4]; // Spot ID
        newRow.insertCell().textContent = itemData[3]; // Channel
        newRow.insertCell().textContent = `${itemData[7]}: ${itemData[0].toFixed(2)}`; // X-Channel
        newRow.insertCell().textContent = `${itemData[8]}: ${itemData[1].toFixed(2)}`; // Y-Channel
        newRow.insertCell().textContent = itemData[2].toFixed(2); // R
        newRow.insertCell().textContent = itemData[6].toFixed(2); // Dist
        newRow.insertCell().textContent = itemData[5]; // Unmixed channel
        
        // Add reassigned status with visual indicator
        const reassignedCell = newRow.insertCell();
        if (itemData[9]) {
            reassignedCell.textContent = '✓';
            reassignedCell.style.color = '#f44336'; // Red
            reassignedCell.style.textAlign = 'center';
            reassignedCell.style.fontWeight = 'bold';
            newRow.style.backgroundColor = '#fff3e0'; // Light orange background
        } else {
            reassignedCell.textContent = '-';
            reassignedCell.style.color = '#757575'; // Gray
            reassignedCell.style.textAlign = 'center';
        }
        
        const labelCell = newRow.insertCell(); 
        labelCell.textContent = label || ''; // Label
        
        // Update the count
        updateSelectedSpotsCount();
    }

    // Event listener for the clear button
    clearButton.addEventListener('click', function() {
        // Remove all rows from the table body
        while (spotsTableBody.firstChild) {
            spotsTableBody.removeChild(spotsTableBody.firstChild);
        }
        console.log("Cleared selected spots table.");
        
        // Update the count
        updateSelectedSpotsCount();
    });

    // Event listener for adding lasso selection to table
    addLassoButton.addEventListener('click', function() {
        if (lassoSelectedData.length === 0) {
            console.log("No lasso selection to add.");
            return;
        }
        console.log(`Adding ${lassoSelectedData.length} selected spots to table with label: ${currentLabel || 'None'}`);
        lassoSelectedData.forEach(itemData => {
            addSpotToTable(itemData, currentLabel);
        });
        // Clear the temporary selection data
        lassoSelectedData = [];
        // Clear the visual brush selection on the chart
        myChart.dispatchAction({ type: 'brush', areas: [] });
    });

    // CSV Export Functionality
    exportCsvButton.addEventListener('click', function() {
        if (spotsTableBody.rows.length === 0) {
            alert("Table is empty. Add some spots first.");
            return;
        }
        exportTableToCSV('unmixed_spots_selection.csv');
    });

    // Annotate Neuroglancer Functionality
    annotateNeuroglancerButton.addEventListener('click', function() {
        if (spotsTableBody.rows.length === 0) {
            alert("Table is empty. Add some spots first.");
            return;
        }
        
        // Collect spot IDs from the table
        const spotIds = [];
        for (let i = 0; i < spotsTableBody.rows.length; i++) {
            const row = spotsTableBody.rows[i];
            const spotId = row.cells[0].textContent; // First column is Spot ID
            spotIds.push(spotId);
        }
        
        // Limit to 1000 annotations
        if (spotIds.length > 1000) {
            if (!confirm(`You have ${spotIds.length} spots selected. Only the first 1000 will be annotated in Neuroglancer. Continue?`)) {
                return;
            }
        }
        
        console.log(`Creating Neuroglancer link with ${spotIds.length} annotations`);
        
        // Disable button and show loading state
        annotateNeuroglancerButton.disabled = true;
        annotateNeuroglancerButton.textContent = 'Creating link...';
        
        // Prepare request data
        const requestData = {
            spot_ids: spotIds,
            annotation_color: "#00FF00",  // Green for SeeSpot
            cross_section_scale: 0.2,
            layer_name: "SeeSpot"
        };
        
        // Make API request
        fetch('/api/create-neuroglancer-multi-annotations', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(requestData)
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.url) {
                console.log(`Neuroglancer link created with ${data.annotation_count} annotations`);
                
                // Show success message
                const message = `Created Neuroglancer link with ${data.annotation_count} annotations!`;
                if (data.missing_spots > 0) {
                    alert(`${message}\n\nNote: ${data.missing_spots} spots were missing coordinate data and were skipped.`);
                } else {
                    alert(message);
                }
                
                // Open URL in new tab
                window.open(data.url, '_blank');
            } else {
                console.error("No URL returned from API");
                alert("Failed to create Neuroglancer link. No URL returned.");
            }
        })
        .catch(error => {
            console.error("Error creating multi-annotation neuroglancer link:", error);
            alert(`Error creating Neuroglancer link: ${error.message}`);
        })
        .finally(() => {
            // Re-enable button and restore text
            annotateNeuroglancerButton.disabled = false;
            annotateNeuroglancerButton.textContent = 'Annotate Neuroglancer';
        });
    });

    function escapeCsvCell(cellData) {
        const dataString = String(cellData || '');
        if (dataString.includes(',') || dataString.includes('"') || dataString.includes('\n')) {
            const escapedString = dataString.replace(/"/g, '""');
            return `"${escapedString}"`;
        }
        return dataString;
    }

    function exportTableToCSV(filename) {
        let csv = [];
        const rows = spotsTable.querySelectorAll("tr");

        for (let i = 0; i < rows.length; i++) {
            const row = [], cols = rows[i].querySelectorAll("td, th");
            
            for (let j = 0; j < cols.length; j++) {
                row.push(escapeCsvCell(cols[j].innerText));
            }
            csv.push(row.join(","));        
        }

        // Create CSV file blob and trigger download
        const csvContent = csv.join("\n");
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement("a");

        if (link.download !== undefined) {
            const url = URL.createObjectURL(blob);
            link.setAttribute("href", url);
            link.setAttribute("download", filename);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
            console.log(`Exported table data to ${filename}`);
        } else {
            alert("CSV export is not supported in your browser.");
        }
    }

    // Function to handle Neuroglancer mode click
    function handleNeuroglancerClick(itemData) {
        const spotId = itemData[4];
        const currentTime = Date.now();
        
        // Check for duplicate clicks (same spot within debounce window)
        if (lastNeuroglancerSpotId === spotId && 
            (currentTime - lastNeuroglancerClickTime) < NEUROGLANCER_CLICK_DEBOUNCE_MS) {
            console.log(`Ignoring duplicate neuroglancer click for spot ${spotId} (within ${NEUROGLANCER_CLICK_DEBOUNCE_MS}ms)`);
            return;
        }
        
        // Update debounce tracking
        lastNeuroglancerClickTime = currentTime;
        lastNeuroglancerSpotId = spotId;
        
        // Add spot to clicked set and update counter
        neuroglancerClickedSpots.add(spotId);
        neuroglancerClickedCount.textContent = neuroglancerClickedSpots.size;
        
        console.log("Handling Neuroglancer click for spot ID:", spotId);
        
        if (spotDetails[spotId]) {
            const details = spotDetails[spotId];
            
            // Create a formatted message for the console
            const coords = {
                spot_id: spotId,
                x: details.x,
                y: details.y,
                z: details.z,
                cell_id: details.cell_id,
                round: details.round,
                ...details
            };
            
            console.log("%c Neuroglancer Coordinates:", "background: #4CAF50; color: white; padding: 2px 5px; border-radius: 3px;");
            console.table(coords);
            
            // Also add a visual indicator on the page
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                background-color: rgba(76, 175, 80, 0.9);
                color: white;
                padding: 10px 15px;
                border-radius: 4px;
                z-index: 9999;
                font-size: 14px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.2);
            `;
            notification.innerHTML = `
                <div style="font-weight: bold; margin-bottom: 5px;">Neuroglancer Coordinates</div>
                <div>Spot ID: ${spotId}</div>
                <div>X: ${details.x?.toFixed(2) || 'N/A'}</div>
                <div>Y: ${details.y?.toFixed(2) || 'N/A'}</div>
                <div>Z: ${details.z?.toFixed(2) || 'N/A'}</div>
                <div style="margin-top: 8px;">
                    <button id="open_neuroglancer_button" style="background-color: #2196F3; color: white; border: none; padding: 5px 10px; border-radius: 3px; cursor: pointer;">
                        Open in Neuroglancer
                    </button>
                </div>
            `;
            document.body.appendChild(notification);
            
            // Add click handler for the Open in Neuroglancer button
            const openButton = notification.querySelector('#open_neuroglancer_button');
            openButton.addEventListener('click', () => {
                createAndOpenNeuroglancerLink(spotId, details);
            });
            
            // Automatically create and open the neuroglancer link
            createAndOpenNeuroglancerLink(spotId, details);
            
            // Update chart to show clicked spot styling
            updateChart();
            
            // Remove the notification after 5 seconds (increased from 3 seconds to give more time)
            setTimeout(() => {
                notification.style.opacity = '0';
                notification.style.transition = 'opacity 0.5s';
                setTimeout(() => notification.remove(), 500);
            }, 5000);
        } else {
            console.warn(`No details found for spot ID: ${spotId}`);
        }
    }
    
    // Function to create and open a neuroglancer link
    function createAndOpenNeuroglancerLink(spotId, details) {
        // Make sure we have the required data
        if (!details || !fusedS3Paths) {
            console.error("Missing required data for neuroglancer link creation");
            return;
        }
        
        // Prepare the data for the request
        const requestData = {
            fused_s3_paths: fusedS3Paths,
            position: [details.x, details.y, details.z, 0],
            point_annotation: [details.x, details.y, details.z, 0.5, 0],
            cell_id: details.cell_id || 42,
            spot_id: spotId,
            annotation_color: "#FFFF00",
            cross_section_scale: 0.2
        };
        
        console.log("Creating neuroglancer link with data:", requestData);
        
        // Make the API request
        fetch('/api/create-neuroglancer-link', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(requestData)
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.url) {
                console.log("Neuroglancer link created:", data.url);
                // Open the URL in a new tab
                window.open(data.url, '_blank');
            } else {
                console.error("No URL returned from API");
            }
        })
        .catch(error => {
            console.error("Error creating neuroglancer link:", error);
        });
    }

    // Handle key events for Neuroglancer mode
    document.addEventListener('keydown', function(event) {
        if (event.key.toLowerCase() === 'n') {
            isNeuroglancerMode = true;
            document.body.style.cursor = 'crosshair';
            document.getElementById('neuroglancer_status').textContent = 'ON';
            document.getElementById('neuroglancer_indicator').style.backgroundColor = '#4CAF50';
        }
    });

    document.addEventListener('keyup', function(event) {
        if (event.key.toLowerCase() === 'n') {
            isNeuroglancerMode = false;
            document.body.style.cursor = 'default';
            document.getElementById('neuroglancer_status').textContent = 'OFF';
            document.getElementById('neuroglancer_indicator').style.backgroundColor = '#9E9E9E';
        }
    });

    // Handle window resize
    window.addEventListener('resize', function () {
        myChart.resize();
        summaryBarChart.resize();
        summaryHeatmap.resize();
        futureChart.resize();
    });

    // Handle toggle for highlighting reassigned spots
    highlightReassignedToggle.addEventListener('change', function() {
        highlightReassigned = this.checked;
        highlightStatus.textContent = highlightReassigned ? 'On' : 'Off';
        
        // Update toggle style
        const toggleLabel = this.nextElementSibling;
        const toggleSpan = toggleLabel.querySelector('span');
        
        if (highlightReassigned) {
            toggleLabel.style.backgroundColor = '#f44336'; // Red when active
            toggleSpan.style.left = '22px';
        } else {
            toggleLabel.style.backgroundColor = '#ccc'; // Gray when inactive
            toggleSpan.style.left = '2px';
        }
        
        // Update chart with new highlighting settings
        updateChart();
    });

    // Event listener for display channel mode dropdown
    displayChanSelect.addEventListener('change', function() {
        displayChanMode = this.value;
        console.log(`Display channel mode changed to: ${displayChanMode}`);
        
        // Update chart with new channel display mode
        updateChart();
    });
    
    // Event listener for sampling type select
    samplingTypeSelect.addEventListener('change', function() {
        samplingType = this.value;
        console.log(`Sampling type changed to: ${samplingType}`);
        // Note: Does not automatically resample - user must click Resample button
    });

    // Event listener for highlight removed toggle
    highlightRemovedToggle.addEventListener('change', function() {
        highlightRemoved = this.checked;
        highlightRemovedStatus.textContent = highlightRemoved ? 'On' : 'Off';
        
        // Update toggle style
        const toggleLabel = this.nextElementSibling;
        const toggleSpan = toggleLabel.querySelector('span');
        
        if (highlightRemoved) {
            toggleLabel.style.backgroundColor = '#9c27b0'; // Purple when active
            toggleSpan.style.left = '22px';
        } else {
            toggleLabel.style.backgroundColor = '#ccc'; // Gray when inactive
            toggleSpan.style.left = '2px';
        }
        
        // Update chart with new highlighting settings
        updateChart();
    });

    // Event listener for valid spot toggle - COMMENTED OUT
    // validSpotToggle.addEventListener('change', function() {
    //     validSpotStatus.textContent = this.checked ? 'On' : 'Off';
    //     
    //     // Update toggle style
    //     const toggleLabel = this.nextElementSibling;
    //     const toggleSpan = toggleLabel.querySelector('span');
    //     
    //     if (this.checked) {
    //         toggleLabel.style.backgroundColor = '#4CAF50'; // Green when active
    //         toggleSpan.style.left = '22px';
    //     } else {
    //         toggleLabel.style.backgroundColor = '#ccc'; // Gray when inactive
    //         toggleSpan.style.left = '2px';
    //     }
    //     
    //     // Reload data with new filter setting
    //     fetchData(currentSampleSize, false);
    // });

    // Chart limits event listeners
    function updateButtonStates() {
        // Reset all button styles
        limitsAutoButton.style.backgroundColor = 'white';
        limitsFixedButton.style.backgroundColor = 'white';
        limitsMinMaxButton.style.backgroundColor = 'white';
        limitsPercentileButton.style.backgroundColor = 'white';
        
        // Highlight active button
        if (chartLimitsMode === 'auto') {
            limitsAutoButton.style.backgroundColor = '#e3f2fd';
        } else if (chartLimitsMode === 'fixed') {
            limitsFixedButton.style.backgroundColor = '#e3f2fd';
        } else if (chartLimitsMode === 'minmax') {
            limitsMinMaxButton.style.backgroundColor = '#e3f2fd';
        } else if (chartLimitsMode === 'percentile') {
            limitsPercentileButton.style.backgroundColor = '#e3f2fd';
        }
    }

    // Input change listeners
    [xlimMin, xlimMax, ylimMin, ylimMax].forEach(input => {
        input.addEventListener('change', function() {
            if (chartLimitsMode === 'fixed') {
                currentXLimits = [parseFloat(xlimMin.value), parseFloat(xlimMax.value)];
                currentYLimits = [parseFloat(ylimMin.value), parseFloat(ylimMax.value)];
                updateChart();
            }
        });
    });

    // Auto button
    limitsAutoButton.addEventListener('click', function() {
        chartLimitsMode = 'auto';
        updateButtonStates();
        updateChart();
    });

    // Fixed limits button
    limitsFixedButton.addEventListener('click', function() {
        chartLimitsMode = 'fixed';
        currentXLimits = [parseFloat(xlimMin.value), parseFloat(xlimMax.value)];
        currentYLimits = [parseFloat(ylimMin.value), parseFloat(ylimMax.value)];
        updateButtonStates();
        updateChart();
    });

    // Min/Max button
    limitsMinMaxButton.addEventListener('click', function() {
        if (chartLimitsMode === 'minmax') {
            // Toggle off to auto
            chartLimitsMode = 'auto';
        } else {
            // Toggle on
            chartLimitsMode = 'minmax';
            calculateMinMaxLimits();
        }
        updateButtonStates();
        updateChart();
    });

    // 1-95% button  
    limitsPercentileButton.addEventListener('click', function() {
        if (chartLimitsMode === 'percentile') {
            // Toggle off to auto
            chartLimitsMode = 'auto';
        } else {
            // Toggle on
            chartLimitsMode = 'percentile';
            calculatePercentileLimits();
        }
        updateButtonStates();
        updateChart();
    });

    function calculateMinMaxLimits() {
        if (!allChartData || allChartData.length === 0 || channelPairs.length === 0) return;
        
        const xChan = channelPairs[currentPairIndex][0];
        const yChan = channelPairs[currentPairIndex][1];
        const xField = `chan_${xChan}_intensity`;
        const yField = `chan_${yChan}_intensity`;
        
        const xValues = allChartData.typedArrays[xField];
        const yValues = allChartData.typedArrays[yField];
        
        const xMin = Math.min(...xValues);
        const xMax = Math.max(...xValues);
        const yMin = Math.min(...yValues);  
        const yMax = Math.max(...yValues);
        
        currentXLimits = [xMin, xMax];
        currentYLimits = [yMin, yMax];
        
        // Update input fields
        xlimMin.value = Math.round(xMin);
        xlimMax.value = Math.round(xMax);
        ylimMin.value = Math.round(yMin);
        ylimMax.value = Math.round(yMax);
    }

    function calculatePercentileLimits() {
        if (!allChartData || allChartData.length === 0 || channelPairs.length === 0) return;
        
        const xChan = channelPairs[currentPairIndex][0];
        const yChan = channelPairs[currentPairIndex][1];
        const xField = `chan_${xChan}_intensity`;
        const yField = `chan_${yChan}_intensity`;
        
        const xValues = Array.from(allChartData.typedArrays[xField]).sort((a, b) => a - b);
        const yValues = Array.from(allChartData.typedArrays[yField]).sort((a, b) => a - b);
        
        const xMin = xValues[Math.floor(xValues.length * 0.01)];
        const xMax = xValues[Math.floor(xValues.length * 0.95)];
        const yMin = yValues[Math.floor(yValues.length * 0.01)];
        const yMax = yValues[Math.floor(yValues.length * 0.95)];
        
        currentXLimits = [xMin, xMax];
        currentYLimits = [yMin, yMax];
        
        // Update input fields
        xlimMin.value = Math.round(xMin);
        xlimMax.value = Math.round(xMax);
        ylimMin.value = Math.round(yMin);
        ylimMax.value = Math.round(yMax);
    }

    // Function to update the summary charts (bar chart, heatmap, and sankey)
    function updateSummaryCharts() {
        updateSummaryBarChart();
        updateSummaryHeatmap();
        updateSankeyChart();
    }
    
    // Function to update the summary bar chart
    function updateSummaryBarChart() {
        if (!summaryStats || summaryStats.length === 0) {
            console.log("No summary stats available for bar chart");
            summaryBarChartDom.innerHTML = '<div style="padding: 20px; text-align: center; color: #666;">No summary data available</div>';
            return;
        }
        
        console.log("Updating summary bar chart with data:", summaryStats);
        
        // Prepare data for the bar chart
        const channels = [];
        const unchangedData = [];
        const reassignedData = [];
        const removedData = [];
        const channelLabels = [];
        
        // Process data for each channel
        summaryStats.forEach(stat => {
            // Create channel label that includes gene if available
            const channelLabel = stat.gene ? `${stat.channel} (${stat.gene})` : `${stat.channel}`;
            channelLabels.push(channelLabel);
            channels.push(stat.channel);
            unchangedData.push(stat.unchanged_spots);
            reassignedData.push(stat.reassigned_spots || 0); // Provide default if not available
            removedData.push(stat.removed_spots);
        });
        
        // Create bar chart option
        const barOption = {
            title: {
                text: 'Spots by Channel',
                left: 'center'
            },
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'shadow'
                },
                formatter: function(params) {
                    const channelIndex = params[0].dataIndex;
                    const channel = channelLabels[channelIndex];
                    
                    let html = `<div style="font-weight: bold;">${channel}</div>`;
                    params.forEach(param => {
                        html += `<div style="display: flex; justify-content: space-between; margin: 5px 0;">
                            <span style="display: inline-block; width: 10px; height: 10px; background-color: ${param.color}; border-radius: 50%; margin-right: 5px;"></span>
                            <span style="margin-right: 15px;">${param.seriesName}:</span>
                            <span style="font-weight: bold;">${param.value.toLocaleString()}</span>
                        </div>`;
                    });
                    
                    if (summaryStats[channelIndex].gene) {
                        html += `<div style="margin-top: 5px; font-style: italic;">Gene: ${summaryStats[channelIndex].gene}</div>`;
                    }
                    
                    return html;
                }
            },
            legend: {
                data: ['Unchanged', 'Reassigned', 'Removed'],
                bottom: 0, // Place legend at the bottom
                padding: [5, 10]
            },
            grid: {
                left: '3%',
                right: '4%',
                bottom: 45, // Increase bottom margin to make room for legend
                containLabel: true
            },
            xAxis: {
                type: 'category',
                data: channelLabels.map(label => {
                    const [channel, gene] = label.split(' (');
                    return gene ? `${channel}\n(${gene}` : channel;
                }),
                axisLabel: {
                    //rotate: 90
                }
            },
            yAxis: {
                type: 'value',
                name: 'Number of Spots',
                axisLabel: {
                    formatter: function(value) {
                        return value.toExponential(1);
                    }
                }
            },
            series: [
                {
                    name: 'Unchanged',
                    type: 'bar',
                    stack: 'total',
                    emphasis: {
                        focus: 'series'
                    },
                    data: unchangedData,
                    itemStyle: {
                        color: '#2196F3'  // Blue instead of Green
                    }
                },
                {
                    name: 'Reassigned',
                    type: 'bar',
                    stack: 'total',
                    emphasis: {
                        focus: 'series'
                    },
                    data: reassignedData,
                    itemStyle: {
                        color: '#9C27B0'  // Purple instead of Amber
                    }
                },
                {
                    name: 'Removed',
                    type: 'bar',
                    stack: 'total',
                    emphasis: {
                        focus: 'series'
                    },
                    data: removedData,
                    itemStyle: {
                        color: '#FF5722'  // Deep Orange instead of Red
                    }
                }
            ]
        };
        
        // Set the option and resize the chart
        summaryBarChart.setOption(barOption);
        summaryBarChart.resize();
    }
    // Function to update the summary heatmap
    function updateSummaryHeatmap() {
        if (!ratiosMatrix || ratiosMatrix.length === 0) {
            console.log("No ratios matrix available for heatmap");
            summaryHeatmapDom.innerHTML = '<div style="padding: 20px; text-align: center; color: #666;">No ratios data available</div>';
            return;
        }
        
        console.log("Updating heatmap with ratios:", ratiosMatrix);
        
        // Identify channels from the ratios (use summary stats if available)
        let channels = [];
        if (summaryStats && summaryStats.length > 0) {
            channels = summaryStats.map(stat => stat.channel);
        } else {
            // If no summary stats, just use indices as labels
            channels = Array.from({length: ratiosMatrix.length}, (_, i) => `CH_${i}`);
        }
        
        // Reverse the channels array for vertical flip
        const reversedChannels = [...channels].reverse();
        
        // Prepare data for heatmap
        const data = [];
        const maxValue = 100; // Maximum percentage in ratios matrix
        
        // Transform matrix into heatmap data format with vertical flip
        for (let i = 0; i < ratiosMatrix.length; i++) {
            for (let j = 0; j < ratiosMatrix[i].length; j++) {
                // Flip vertically: map i to (n - 1 - i)
                const flippedI = ratiosMatrix.length - 1 - i;
                data.push([j, flippedI, ratiosMatrix[i][j]]);
            }
        }
        
        // Create heatmap option
        const heatmapOption = {
            title: {
                text: 'Channel Ratio Matrix',
                left: 'center'
            },
            tooltip: {
                position: 'top',
                formatter: function(params) {
                    const flippedI = ratiosMatrix.length - 1 - params.data[1];
                    const original = ratiosMatrix[flippedI][params.data[0]];
                    const sourceChannel = channels[flippedI];
                    const targetChannel = channels[params.data[0]];
                    return `Original: ${sourceChannel}<br>Reassigned: ${targetChannel}<br>Ratio: ${original}%`;
                }
            },
            grid: {
                top: 60,
                bottom: 70 // Increase bottom margin to make room for visualMap
            },
            xAxis: {
                type: 'category',
                data: channels,
                splitArea: {
                    show: true
                }
            },
            yAxis: {
                type: 'category',
                data: reversedChannels,
                splitArea: {
                    show: true
                }
            },
            visualMap: {
                min: 0,
                max: maxValue,
                calculable: true,
                precision: 0,
                orient: 'horizontal',
                left: 'right',
                bottom: 0, // Place at the bottom
                itemWidth: 15,
                itemHeight: 80,
                inRange: {
                    color: ['#FFFFFF', '#00BCD4', '#9C27B0']  // White to Teal to Purple
                }
            },
            series: [{
                name: 'Ratio',
                type: 'heatmap',
                data: data,
                label: {
                    show: true,
                    formatter: function(params) {
                        return params.data[2] > 0 ? params.data[2] + '%' : '';
                    }
                },
                emphasis: {
                    itemStyle: {
                        shadowBlur: 10,
                        shadowColor: 'rgba(0, 0, 0, 0.5)'
                    }
                }
            }]
        };
        
        // Set the option and resize the chart
        summaryHeatmap.setOption(heatmapOption);
        summaryHeatmap.resize();
    }

    // Function to update the Sankey flow chart
    function updateSankeyChart() {
        if (!sankeyData) {
            console.log("No Sankey data available from backend");
            futureChart.setOption({
                title: {
                    text: 'Channel Flow Analysis\n(No data available)',
                    left: 'center',
                    top: 'top',
                    textStyle: {
                        color: '#999',
                        fontStyle: 'italic',
                        fontSize: 16
                    }
                }
            });
            return;
        }

        console.log("Updating Sankey chart with backend data");
        console.log(`Processing ${sankeyData.total_spots} total spots from backend`);

        // Prepare nodes for ECharts Sankey
        const nodes = sankeyData.nodes.map(node => ({
            name: node.name,
            itemStyle: {
                color: node.channel === 'Removed' ? 
                    COLORS.Removed : 
                    (COLORS[node.channel] || COLORS.default)
            }
        }));

        // Prepare links for ECharts Sankey
        const links = sankeyData.links.map(link => {
            const isUnchanged = link.flow_type === 'unchanged';
            const isRemoved = link.flow_type === 'removed';
            
            return {
                source: link.source,
                target: link.target,
                value: link.value,
                itemStyle: {
                    color: isUnchanged ? 
                        (COLORS[link.source.split(' ')[0]] || COLORS.default) : // Use original channel color for unchanged
                        isRemoved ?
                        'rgba(255, 87, 34, 0.6)' : // Orange-red for removed
                        'rgba(150, 150, 150, 0.6)' // Gray for reassigned
                },
                lineStyle: {
                    color: isUnchanged ? 
                        (COLORS[link.source.split(' ')[0]] || COLORS.default) : // Use original channel color for unchanged
                        isRemoved ?
                        'rgba(255, 87, 34, 0.6)' : // Orange-red for removed
                        'rgba(150, 150, 150, 0.6)' // Gray for reassigned
                }
            };
        });

        const sankeyOption = {
            title: {
                text: 'Channel Flow Analysis',
                left: 'center',
                top: 'top',
                textStyle: {
                    fontSize: 14,
                    fontWeight: 'bold'
                }
            },
            tooltip: {
                trigger: 'item',
                triggerOn: 'mousemove',
                formatter: function(params) {
                    if (params.dataType === 'edge') {
                        // Find the corresponding link data from backend
                        const linkData = sankeyData ? sankeyData.links.find(link => 
                            link.source === params.data.source && link.target === params.data.target
                        ) : null;
                        
                        if (linkData) {
                            const [original] = linkData.source.split(' (');
                            const [final] = linkData.target.split(' (');
                            
                            let flowType = '';
                            if (linkData.flow_type === 'unchanged') {
                                flowType = '<span style="color: #4CAF50;">✓ Unchanged</span>';
                            } else if (linkData.flow_type === 'removed') {
                                flowType = '<span style="color: #646464ff;">✗ Removed</span>';
                            } else {
                                flowType = '<span style="color: #ff0000ff;">↻ Reassigned</span>';
                            }
                            
                            return `<div style="font-size: 13px;">
                                    <div style="font-weight: bold; margin-bottom: 5px;">${flowType}</div>
                                    <div>${original} → ${final}</div>
                                    <div style="margin-top: 5px;">
                                        <strong>${linkData.value.toLocaleString()}</strong> spots (${linkData.percentage}%)
                                    </div>
                                    </div>`;
                        }
                        
                        // Fallback for old data structure
                        const totalSpots = sankeyData ? sankeyData.total_spots : (allChartData ? allChartData.length : 1);
                        const percentage = ((params.value / totalSpots) * 100).toFixed(1);
                        const [original] = params.data.source.split(' (');
                        const [final] = params.data.target.split(' (');
                        
                        let flowType = '';
                        if (original === final) {
                            flowType = '<span style="color: #4CAF50;">✓ Unchanged</span>';
                        } else if (final === 'Removed') {
                            flowType = '<span style="color: #636363ff;">✗ Removed</span>';
                        } else {
                            flowType = '<span style="color: #fc0a0aff;">↻ Reassigned</span>';
                        }
                        
                        return `<div style="font-size: 13px;">
                                <div style="font-weight: bold; margin-bottom: 5px;">${flowType}</div>
                                <div>${original} → ${final}</div>
                                <div style="margin-top: 5px;">
                                    <strong>${params.value.toLocaleString()}</strong> spots (${percentage}%)
                                </div>
                                </div>`;
                    } else {
                        // Node tooltip
                        const [channel, type] = params.name.split(' (');
                        const isOriginal = type === 'Original)';
                        
                        // Calculate node total from backend Sankey links data
                        let nodeCount = 0;
                        if (isOriginal) {
                            // Sum all flows starting from this original channel
                            nodeCount = sankeyData.links
                                .filter(link => link.source === params.name)
                                .reduce((sum, link) => sum + link.value, 0);
                        } else {
                            // Sum all flows ending at this final channel
                            nodeCount = sankeyData.links
                                .filter(link => link.target === params.name)
                                .reduce((sum, link) => sum + link.value, 0);
                        }
                        
                        const percentage = ((nodeCount / sankeyData.total_spots) * 100).toFixed(1);
                        return `<div style="font-size: 13px;">
                                <div style="font-weight: bold;">${channel}</div>
                                <div>${isOriginal ? 'Original' : 'Final'} channel</div>
                                <div style="margin-top: 5px;">
                                    <strong>${nodeCount.toLocaleString()}</strong> spots (${percentage}%)
                                </div>
                                </div>`;
                    }
                }
            },
            series: [{
                type: 'sankey',
                layout: 'none',
                emphasis: {
                    focus: 'adjacency'
                },
                data: nodes,
                links: links,
                orient: 'horizontal',
                label: {
                    position: 'outside',
                    fontSize: 11,
                    formatter: function(params) {
                        const [channel] = params.name.split(' (');
                        return channel;
                    }
                },
                lineStyle: {
                    curveness: 0.3
                },
                left: '5%',
                right: '5%',
                top: '15%',
                bottom: '5%'
            }]
        };

        futureChart.setOption(sankeyOption);
        futureChart.resize();
        
        console.log(`Sankey chart created with ${nodes.length} nodes and ${links.length} links from backend data`);
        console.log(`Backend used threshold of ${sankeyData.threshold_used} spots per flow`);
    }

    // Initialize the future chart with a placeholder until data loads
    futureChart.setOption({
        title: {
            text: 'Future Visualization \n (not implemented)',
            left: 'center',
            top: 'middle',
            textStyle: {
                color: '#999',
                fontStyle: 'italic',
                fontSize: 16
            }
        }
    });
}); 