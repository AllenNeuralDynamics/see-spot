document.addEventListener('DOMContentLoaded', function () {
    const chartDom = document.getElementById('main-chart');
    const spotsTableBody = document.getElementById('data-table-body');
    const spotsTable = document.getElementById('data-table');
    const clearButton = document.getElementById('clear_spots_button');
    const addLassoButton = document.getElementById('add_lasso_selection_button');
    const exportCsvButton = document.getElementById('export_csv_button');
    const labelInput = document.getElementById('label_input');
    const activeLabelDisplay = document.getElementById('active_label_display');
    const prevChannelButton = document.getElementById('prev_channel_pair');
    const nextChannelButton = document.getElementById('next_channel_pair');
    const currentChannelDisplay = document.getElementById('current_channel_display');
    const sampleSizeInput = document.getElementById('sample-size-input');
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
    const validSpotToggle = document.getElementById('valid_spot_toggle');
    const validSpotStatus = document.getElementById('valid_spot_status');
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
    let highlightReassigned = false;
    let highlightRemoved = false;
    let displayChanMode = 'unmixed'; // 'unmixed' or 'mixed'
    let isNeuroglancerMode = false;
    let spotDetails = {}; // Will store the spot details for neuroglancer lookup
    let fusedS3Paths = {}; // Will store the fused S3 paths from the API
    let summaryStats = null; // Will store the summary stats from the API
    let ratiosMatrix = null; // Will store the ratios matrix from the API
    let selectedSpots = new Set();
    
    // Chart limits variables
    let chartLimitsMode = 'auto'; // 'auto', 'fixed', 'minmax', 'percentile'
    let currentXLimits = [0, 2000];
    let currentYLimits = [0, 2000];
    
    // Neuroglancer click debounce variables
    let lastNeuroglancerClickTime = 0;
    let lastNeuroglancerSpotId = null;
    const NEUROGLANCER_CLICK_DEBOUNCE_MS = 1000; // Prevent duplicate clicks within 1 second
    
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

    function updateDatasetTable() {
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
            
            // Truncate long dataset names for display
            const displayName = dataset.name.length > 35 ? 
                dataset.name.substring(0, 32) + '...' : dataset.name;
            
            row.innerHTML = `
                <td title="${dataset.name}">${displayName}</td>
                <td>${dataset.creation_date}</td>
                <td><span class="status-indicator ${statusClass}"></span>${statusText}</td>
            `;
            
            row.addEventListener('click', () => selectDataset(dataset.name, row));
            datasetTableBody.appendChild(row);
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
                showDatasetMessage(`Successfully loaded dataset: ${data.dataset_name}`, 'success');
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
        updateSampleSizeNote(currentSampleSize);
        
        // Show loading state
        myChart.showLoading({
            text: 'Loading new sample...',
            maskColor: 'rgba(255, 255, 255, 0.8)',
            fontSize: 14
        });
        
        // Fetch data with new sample size
        fetchData(currentSampleSize, false);
    });

    // Handle refresh button click (force reload data from server)
    refreshButton.addEventListener('click', function() {
        if (confirm("This will reload data from the server. Continue?")) {
            refreshData(true);
        }
    });

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
    
    // Initial data fetch
    fetchData(currentSampleSize, false);
    
    // Initialize button states
    updateButtonStates();
    
    // Fetch data function
    function fetchData(sampleSize, forceRefresh = false) {
        const validSpotsOnly = validSpotToggle.checked;
        const url = `/api/real_spots_data?sample_size=${sampleSize}${forceRefresh ? '&force_refresh=true' : ''}${validSpotsOnly ? '&valid_spots_only=true' : '&valid_spots_only=false'}`;
        console.log(`Fetching data with URL: ${url}`);
        
        fetch(url)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! Status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                console.log(`Fetched unmixed spots data with sample size ${sampleSize}:`, data);
                
                if (!data.spots_data || !data.channel_pairs || data.spots_data.length === 0) {
                    throw new Error("Invalid or empty data received from API");
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

        // Create channel selector buttons
        createChannelSelector();

        // Set initial channel pair
        currentPairIndex = 0;
        updateChart(spotsData);
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
        
        for (let i = 0; i < allChartData.length; i++) {
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
        
        const series = sortedChannels.map(channel => ({
            name: channel, // Remove the Mixed/Unmixed prefix from individual labels
            type: 'scatter',
            data: seriesData[channel],
            symbol: channel === 'Removed' ? 'triangle' : 'circle', // Use triangle symbol for removed spots 
            symbolSize: channel === 'Removed' ? 8 : 5, // Larger size for removed symbols
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
                    const isReassigned = params.data.value[9];
                    const isRemoved = params.data.value[10];
                    
                    // Add border if reassigned or removed
                    if (isReassigned || isRemoved) {
                        return 2;
                    }
                    return 0;
                },
                borderColor: function(params) {
                    const isReassigned = params.data.value[9];
                    const isRemoved = params.data.value[10];
                    
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
        }));
        
        // Configuration for slider positioning and styling
        const sliderConfig = {
            // Space required for all sliders
            width: 60,   // Width of each slider
            gap: 20,     // Gap between sliders
            startRight: 20,  // Distance from right edge of chart

            // Move sliders lower in the plot area
            top: '40%', // Move sliders down (default is 'center')

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
                    acc[chan] = true; // Use channel name without prefix
                    return acc;
                }, {})
            },
            grid: {
                right: totalSliderWidth + sliderConfig.startRight + 40, // Make room for sliders and legend
                bottom: 70 // Still need some bottom space for axis labels
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
            visualMap: [
                {
                    // R-value filter
                    right: sliderConfig.startRight,
                    top: 'center',
                    dimension: 2, // The 'r' value is at index 2 in each data point array
                    min: 0,
                    max: r99Percentile,
                    precision: 2,
                    text: ['R Value'],
                    textStyle: {
                        fontSize: 12
                    },
                    ...sliderConfig,
                    handleStyle: {
                        color: '#4285f4'
                    },
                    inRange: {
                        opacity: 1
                    },
                    outOfRange: {
                        opacity: 0.01
                    },
                    seriesIndex: seriesIndices, // Explicitly set which series this visualMap controls
                    hoverLink: false // Disable hover highlight when using the slider
                },
                {
                    // Distance filter
                    right: sliderConfig.startRight + sliderConfig.width + sliderConfig.gap,
                    top: 'center',
                    dimension: 6, // The 'dist' value is at index 6 in each data point array
                    min: 0,
                    max: dist99Percentile,
                    precision: 2,
                    text: ['Distance'],
                    textStyle: {
                        fontSize: 12
                    },
                    ...sliderConfig,
                    handleStyle: {
                        color: '#f83628ff'
                    },
                    inRange: {
                        opacity: 1
                    },
                    outOfRange: {
                        opacity: 0.01
                    },
                    seriesIndex: seriesIndices, // Explicitly set which series this visualMap controls
                    hoverLink: false // Disable hover highlight when using the slider
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
    }

    // Event listener for the clear button
    clearButton.addEventListener('click', function() {
        // Remove all rows from the table body
        while (spotsTableBody.firstChild) {
            spotsTableBody.removeChild(spotsTableBody.firstChild);
        }
        console.log("Cleared selected spots table.");
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

    // Event listener for valid spot toggle
    validSpotToggle.addEventListener('change', function() {
        validSpotStatus.textContent = this.checked ? 'On' : 'Off';
        
        // Update toggle style
        const toggleLabel = this.nextElementSibling;
        const toggleSpan = toggleLabel.querySelector('span');
        
        if (this.checked) {
            toggleLabel.style.backgroundColor = '#4CAF50'; // Green when active
            toggleSpan.style.left = '22px';
        } else {
            toggleLabel.style.backgroundColor = '#ccc'; // Gray when inactive
            toggleSpan.style.left = '2px';
        }
        
        // Reload data with new filter setting
        fetchData(currentSampleSize, false);
    });

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

    // Function to update the summary charts (bar chart and heatmap)
    function updateSummaryCharts() {
        updateSummaryBarChart();
        updateSummaryHeatmap();
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
        
        // Prepare data for heatmap
        const data = [];
        const maxValue = 100; // Maximum percentage in ratios matrix
        
        // Transform matrix into heatmap data format
        for (let i = 0; i < ratiosMatrix.length; i++) {
            for (let j = 0; j < ratiosMatrix[i].length; j++) {
                data.push([i, j, ratiosMatrix[i][j]]);
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
                    const original = ratiosMatrix[params.data[0]][params.data[1]];
                    const sourceChannel = channels[params.data[0]];
                    const targetChannel = channels[params.data[1]];
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
                },
                name: 'Reassigned',
                nameLocation: 'middle',
                nameGap: 30
            },
            yAxis: {
                type: 'category',
                data: channels,
                splitArea: {
                    show: true
                },
                name: 'Original',
                nameLocation: 'middle',
                nameGap: 40
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

    // Initialize the future chart with a placeholder
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