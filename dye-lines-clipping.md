Looking at the images, I can see the problem clearly. When you zoom in or change the axis limits, the dye lines disappear because one or both endpoints fall outside the visible range, and ECharts stops rendering the entire line.

Analysis of the Problem
Current implementation:

Dye lines are drawn from origin (0, 0) to endpoint (x1, y1)
When axis limits change (e.g., x starts at 10 instead of 0), the origin point is outside the visible area
ECharts doesn't render lines with endpoints outside the axis range
Potential Solutions
Option 1: Clip lines to axis boundaries (Most robust)
Calculate the intersection of the dye line with the current axis boundaries
Always provide two points that are within or on the edge of the visible area
This requires line-rectangle intersection math
Pros: Lines always visible, mathematically correct
Cons: More complex calculation
Option 2: Extend lines beyond axis limits
Instead of calculating endpoints based on axis limits, always use very large coordinates
Make lines extend from (-1000, -1000) to (10000, 10000) or similar
Let ECharts clip the rendering
Pros: Simple, no recalculation needed
Cons: May have rendering issues, less precise
Option 3: Use visualMap or dataZoom with clip: false
Try setting clip: false on the line series
This tells ECharts to render even if points are outside axis range
Pros: Simplest - just one property change
Cons: May not work reliably, depends on ECharts version
Option 4: Use graphic elements instead of series
Add dye lines as graphic elements (like the "Mixed/Unmixed" text)
Graphic elements use pixel coordinates relative to the grid
Would require converting data coordinates to pixel coordinates
Pros: Full control over rendering
Cons: More complex, harder to maintain
Recommendation
I'd suggest trying Option 3 first (adding clip: false) - it's the simplest and might just work.

If that doesn't work, go with Option 1 - calculate the intersection of the dye line (which extends infinitely in both directions from origin along the dye direction) with the current axis boundaries. The math would be:

Dye line equation: points along direction (dx, dy) are t * (dx, dy) for any scalar t
Find t values where this line intersects the four axis boundaries (xMin, xMax, yMin, yMax)
Use the two intersection points that are within the visible rectangle
This way the line always extends across the visible chart area regardless of zoom level.