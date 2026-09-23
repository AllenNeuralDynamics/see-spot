/*
 * Channel colours shared by every SeeSpot page.
 *
 * Lifted out of unmixed_spots.js so the spots and bleedthrough pages
 * cannot drift apart. These are display colours keyed by laser
 * wavelength, deliberately NOT the physical colour of that wavelength
 * (561 shows as blue): changing them here would silently recolour the
 * existing spots page, so leave them alone unless both pages should
 * change together.
 *
 * Loaded as a plain script before the page script, matching the
 * existing no-bundler CDN-globals style.
 */
window.SEE_SPOT_COLORS = {
    '405': '#7E57C2',    // Violet
    '488': '#4CAF50',    // Green
    '514': '#F44336',    // Red
    '561': '#2196F3',    // Blue
    '594': '#00BCD4',    // Cyan
    '638': '#9C27B0',    // Purple
    'ambiguous': '#9E9E9E',
    'none': '#607D8B',
    'Removed': 'rgba(0, 0, 0, 0.5)',
    'default': '#2196F3'
};

/**
 * Look up a channel's display colour, falling back to the default.
 *
 * @param {string} channel Channel name, normally a wavelength.
 * @returns {string} A CSS colour.
 */
window.seeSpotColor = function (channel) {
    return window.SEE_SPOT_COLORS[channel] || window.SEE_SPOT_COLORS.default;
};
