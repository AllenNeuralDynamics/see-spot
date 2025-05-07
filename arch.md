# See-Spot Architecture Overview

## Core Components

1. **FastAPI Web Application**
   - Entry point: app.py
   - Serves the main web interface and API endpoints
   - Uses Jinja2 templating for HTML pages

2. **S3 Data Management**
   - s3_handler.py: Core implementation of S3 operations
   - s3_utils.py: Higher-level functions for file searching, loading, and caching

3. **Neuroglancer Integration**
   - ng_utils.py: Utilities for generating Neuroglancer visualizations
   - Creates interactive 3D visualizations of imaging data

4. **Frontend Interface**
   - Main page: unmixed_spots.html
   - Interactive JavaScript: unmixed_spots.js
   - Data visualization and interaction

## Key Features

1. **Spot Data Visualization**
   - Loads spot data from S3 (unmixed_spots_*.pkl files)
   - Displays interactive scatter plots of spots
   - Provides filtering and selection tools

2. **3D Visualization**
   - Integrates with Neuroglancer for 3D exploration
   - Supports navigation to specific spot locations
   - Shows multi-channel fluorescence data

3. **Data Analysis**
   - Displays statistical summaries of spot data
   - Shows channel assignment and reassignment information
   - Supports exploration of intensity relationships

4. **API Endpoints**
   - /api/real_spots_data: Retrieves spot data for visualization
   - /api/create-neuroglancer-link: Generates custom Neuroglancer views

## Data Flow

1. User visits /unmixed-spots
2. Frontend requests data through API
3. Backend loads data from S3, processes it, and returns JSON
4. Frontend renders interactive visualizations
5. User can select spots to view in Neuroglancer

## Technology Stack

- **Backend**: Python, FastAPI, Uvicorn
- **Frontend**: HTML, JavaScript, Apache ECharts
- **Storage**: S3 for data files
- **Visualization**: Neuroglancer for 3D images
- **Data Processing**: Pandas, NumPy

## Deployment

- Runs with Uvicorn server
- Requires S3 credentials for data access
- Deployed as a standalone web application