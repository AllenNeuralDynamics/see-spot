Feature request:

1. Use the arch.md file in the see-spot app to understand the architecture.
2. Please make minimal changes to the codebase to add a new feature.
3. Please add the following features:
    + During lasso selection, the user can select multiple spots. We already have a button to add those spots to a table. 
    + I would like to add another button to remove the spots from the table. This is the easy part.
    + I would like to add an additional button to generate a neuroglancer links of all the spots in the table. To do this, check the current neuroglancer links generation code. Right now it creates a link for a single clicked spot. Notice how it injests the spot location, and saves the annotations in the neuroglancer link generation. We probably need to create a new function create_link_no_upload_multiple_spots, based on the original. Please look up neuroglancer documententation if confused. We also need to select the first view that is generated during neuroglancer. We can just use the first spot. This is the harder part.
