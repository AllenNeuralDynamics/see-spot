/*
 * Bleedthrough diagnostics page.
 *
 * Standalone DOMContentLoaded closure with no exports, matching
 * unmixed_spots.js. Channel colours come from the shared colors.js.
 *
 * The density grid is built from ONE sample of voxels in which every
 * row carries that voxel's value in every channel plus its global
 * position. Panel (i, j) plots column i against column j of that same
 * array, so a pair can only ever be formed from values measured at the
 * same physical point -- co-location is structural here, not a
 * convention that a caller has to remember. It also means a click in
 * any panel resolves to a real voxel with no server round-trip.
 */
document.addEventListener('DOMContentLoaded', function () {
    const el = (id) => document.getElementById(id);

    const pathInput = el('bt-path');
    const datasetSelect = el('bt-dataset-select');
    const detectBtn = el('bt-detect');
    const detectStatus = el('bt-detect-status');
    const channelsBox = el('bt-channels');
    const sideVal = el('bt-side-val');
    const sideSlider = el('bt-side-slider');
    const sizeNote = el('bt-size-note');
    const regionList = el('bt-region-list');
    const clearRegionsBtn = el('bt-clear-regions');
    const runBtn = el('bt-run');
    const figuresBtn = el('bt-figures');
    const figuresOut = el('bt-figures-out');
    const voxelUmInput = el('bt-voxel-um');
    const windowUmInput = el('bt-window-um');
    const brightInput = el('bt-bright');
    const sampleInput = el('bt-sample');
    const nullNote = el('bt-null-note');
    const sliceSlider = el('bt-slice-slider');
    const sliceVal = el('bt-slice-val');
    const sliceAxis = el('bt-slice-axis');
    const viewChannel = el('bt-view-channel');
    const contrastSlider = el('bt-contrast-slider');
    const contrastVal = el('bt-contrast-val');
    const contrastReset = el('bt-contrast-reset');
    const levelSelect = el('bt-level');
    const gateInput = el('bt-gate');
    const fillGridBtn = el('bt-fill-grid');
    const gridCount = el('bt-grid-count');
    const regionWarn = el('bt-region-warn');
    const progressBox = el('bt-progress');
    const barFill = el('bt-bar-fill');
    const progressText = el('bt-progress-text');
    const helpBox = el('bt-help');
    const helpBtn = el('bt-help-btn');
    const viewerImg = el('bt-viewer-img');
    const viewerWrap = el('bt-viewer-wrap');
    const viewerHint = el('bt-viewer-hint');
    const viewerDims = el('bt-viewer-dims');
    const viewerBusy = el('bt-viewer-busy');
    const pinsBox = el('bt-pins');
    const placeholder = el('bt-placeholder');
    const resultsBody = el('bt-results-body');
    const resultsHeader = el('bt-results-header');
    const voxelCard = el('bt-voxel-card');
    const gridDom = el('bt-grid');
    const matrixDom = el('bt-matrix');
    const matrixCaption = el('bt-matrix-caption');

    // Which axis each plane holds fixed, and which it shows (row, col).
    // Indices are into a [z, y, x] triple.
    const PLANES = {
        XY: { fixed: 0, row: 1, col: 2, label: 'Z' },
        ZY: { fixed: 2, row: 0, col: 1, label: 'X' },
        ZX: { fixed: 1, row: 0, col: 2, label: 'Y' }
    };
    const PREVIEW_TARGET_PX = 512;
    // Viewer resolution. Deliberately coarse: the slab is for
    // navigation and contrast, and a smaller plane is what lets a whole
    // chunk-row of them sit in memory at once.
    const VIEW_TARGET_PX = 320;
    // Planes per request. Consecutive planes share chunks, so a slab
    // costs about what one plane costs; this is capped by payload, not
    // by read time.
    const DEFAULT_SLAB_DEPTH = 48;
    const MAX_SLABS = 6;
    const PREFETCH_EDGE = 8;
    const HIST_BINS = 1024;
    // Fraction of the projection's range above the floor that counts as
    // imaged tissue rather than empty margin.
    const CONTENT_CUT = 0.06;
    const CONTENT_PAD = 0.03;
    // Only auto-crop when it saves a real amount of the frame.
    const AUTOFIT_MIN_GAIN = 0.9;
    const POLL_MS = 350;
    // Whole-stack preload: coarser than a slab, but it removes slab
    // boundaries entirely once it lands.
    const VOLUME_TARGET_PX = 192;
    const VOLUME_BUDGET_BYTES = 24 * 1024 * 1024;
    // Quiet period before fetching a sharper slab.
    const SHARPEN_IDLE_MS = 500;
    const PROGRESS_LABELS = {
        queued: 'Queued',
        'reading metadata': 'Reading metadata',
        'reading blocks': 'Reading blocks from S3',
        'checking signal': 'Checking regions for signal',
        normalising: 'Normalising channels',
        'fitting coefficients': 'Fitting coefficients',
        'sampling voxels': 'Sampling voxels',
        'checking registration': 'Checking channel registration',
        done: 'Done'
    };
    const AXIS_LOW = -1.5;
    // Below this many pooled regions the coefficients are not stable:
    // on a real dataset alpha fell ~3x going from 2 regions to 33.
    const MIN_STABLE_REGIONS = 10;
    const DEFAULT_BLOCK_SIDE = 128;
    const MAX_BLOCK_SIDE = 256;
    // Physical field a default block should cover. Wide enough that the
    // displaced null sees tissue structure rather than one object.
    const TARGET_FIELD_UM = 150;
    // Marks drawn per panel. The same voxels are drawn in every panel,
    // so the real cost is this times N^2 -- 3000 x 25 is already 75k
    // marks. ECharts' `large` mode would render more but stops emitting
    // per-point clicks, and clicking through to Neuroglancer matters
    // more here than the extra density.
    const DISPLAY_CAP = 3000;

    let chart = null;
    let matrixChart = null;
    let state = {
        basePath: '',
        channels: [],          // [{name, zarr_path, shape_zyx}]
        selected: new Set(),
        levels: [],            // [{level, shape_zyx}]
        shape0: null,          // [z, y, x] at full resolution
        voxelZyx: [1, 1, 1],   // level-0 voxel size in um
        plane: 'XY',
        slice: 0,              // in LEVEL-0 voxels
        viewLevel: 0,
        contrast: [0.5, 99.5],   // display window, as percentiles
        level: 0,                // pyramid level the ANALYSIS reads
        sizeMode: 'cubic',
        side: 128,
        chunks: [128, 128, 128],
        regions: [],           // [[z, y, x], ...] in level-0 voxels
        result: null,
        hiddenRegions: new Set(),
        slabs: new Map(),      // key -> {data, n, rows, cols, start, hist}
        pending: new Map(),    // in-flight slab requests
        slabDepth: DEFAULT_SLAB_DEPTH,
        loadToken: 0,
        viewReady: false,
        crop: null,            // {row0,row1,col0,col1} at view level
        autoFitDone: false,
        volume: null,          // whole plane stack, low resolution
        volumeKey: null,
        volumeLoading: null,
        sharpenTimer: null,
        targetFieldUm: 0,      // physical field to preserve across levels
        settingSide: false,    // guard: programmatic slider move
        sideClamped: false,
        frame: null,           // reused ImageData
        lut: null              // reused value -> byte table
    };

    /* ---------------- helpers ---------------- */

    /**
     * Show a transient corner message. Mirrors the spots page helper.
     * @param {string} message Text to show.
     * @param {string} type One of 'error', 'success' or 'info'.
     */
    function toast(message, type) {
        const box = document.createElement('div');
        const palette = {
            error: ['#ffebee', '#c62828'],
            success: ['#e8f5e9', '#2e7d32'],
            info: ['#e3f2fd', '#1565c0']
        }[type] || ['#e3f2fd', '#1565c0'];
        Object.assign(box.style, {
            position: 'fixed', top: '60px', right: '20px', zIndex: 10000,
            padding: '10px 14px', borderRadius: '4px', fontSize: '13px',
            maxWidth: '380px', boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
            backgroundColor: palette[0], color: palette[1]
        });
        box.textContent = message;
        document.body.appendChild(box);
        setTimeout(() => box.remove(), 6000);
    }

    /**
     * Pick the coarsest pyramid level still large enough to look at.
     * Mirrors zarr_reader.choose_preview_level so the client always
     * knows which level it is showing and can convert coordinates
     * without waiting for the server to tell it.
     * @param {string} plane Plane name.
     * @returns {number} Level index.
     */
    function chooseLevel(plane) {
        const spec = PLANES[plane];
        for (let i = state.levels.length - 1; i >= 0; i--) {
            const shape = state.levels[i].shape_zyx;
            if (Math.max(shape[spec.row], shape[spec.col]) >= PREVIEW_TARGET_PX) {
                return i;
            }
        }
        return 0;
    }

    /**
     * Ratio between full resolution and a level, per axis.
     * Levels here are anisotropic (z is not halved until level 3), so
     * a single 2**level factor would misplace every region.
     * @param {number} level Level index.
     * @returns {number[]} Multipliers for z, y and x.
     */
    function levelRatio(level) {
        const shape = state.levels[level].shape_zyx;
        return state.shape0.map((full, i) => full / shape[i]);
    }

    /**
     * Current block extent in voxels.
     * @returns {number[]} Size along z, y and x.
     */
    function blockSize() {
        if (state.sizeMode === 'cubic') {
            return [state.side, state.side, state.side];
        }
        return [
            parseInt(el('bt-size-z').value, 10) || 128,
            parseInt(el('bt-size-y').value, 10) || 128,
            parseInt(el('bt-size-x').value, 10) || 128
        ];
    }

    /**
     * Refresh the block size note.
     *
     * Reports the bytes actually transferred as well as the block size.
     * Chunks are read whole, so a block that straddles them costs far
     * more than it yields -- on a volume chunked 256 wide a 128 block
     * can pull tens of times its own size, which is the difference
     * between a run taking seconds and taking a minute.
     */
    function updateSizeNote() {
        const size = blockSize();
        const nChan = Math.max(state.selected.size, 1);
        const nRegions = Math.max(state.regions.length, 1);
        const perChannel = size[0] * size[1] * size[2] * 2;
        const totalMb = (perChannel * nChan * nRegions) / (1024 * 1024);

        // Chunks of the level actually being read, not level 0 -- the
        // levels are chunked differently and the cost follows the one
        // in use.
        const lvlInfo = state.levels[state.level];
        const chunkShape = (lvlInfo && lvlInfo.chunks_zyx) || state.chunks;
        let chunks = 1;
        size.forEach((s, i) => {
            chunks *= Math.ceil(s / chunkShape[i]) + 1;
        });
        const chunkBytes = chunkShape[0] * chunkShape[1] * chunkShape[2] * 2;
        const readMb = (chunks * chunkBytes * nChan * nRegions) /
            (1024 * 1024);

        let note =
            '<b>' + (perChannel / (1024 * 1024)).toFixed(1) + ' MB</b>' +
            ' per channel per region &middot; <b>' + totalMb.toFixed(0) +
            ' MB</b> of voxels';
        // Physical extent matters more than the voxel count: the same
        // 128 voxels is 20 um at level 0 and 157 um at level 3, and the
        // displaced null behaves quite differently across that range.
        const lvl = state.levels[state.level];
        if (lvl && state.shape0) {
            const um = size.map((s, i) =>
                (s * state.voxelZyx[i] * (state.shape0[i] /
                    lvl.shape_zyx[i])).toFixed(1));
            note += '<br>field <b>' + um.join(' &times; ') + ' &micro;m</b>' +
                ' (z, y, x) at level ' + state.level;
            if (state.sideClamped) {
                note += ' &mdash; <b>capped</b> at ' + MAX_BLOCK_SIDE +
                    ' voxels, so the field is smaller than the ' +
                    state.targetFieldUm.toFixed(0) +
                    ' &micro;m carried over';
            }
        }
        if (state.chunks[0]) {
            note += '<br>reads up to <b>' + readMb.toFixed(0) + ' MB</b>' +
                ' (' + chunks + ' chunks of ' + chunkShape.join('&times;') +
                ' each)';
        }
        sizeNote.innerHTML = note;
        sideVal.textContent = state.side;
    }

    /**
     * Sync the voxel-size field to the analysis level.
     *
     * pair_coefficients converts the displacement window from microns
     * into voxels, so the size it is given has to be the size of the
     * voxels the blocks were actually read from. Leaving the level-0
     * value here while sampling level 3 would displace the null by 8x
     * the intended distance.
     */
    function applyLevel(previousLevel) {
        const lvl = state.levels[state.level];
        if (!lvl || !state.shape0) { return; }
        const um = lateralUmAt(state.level);
        voxelUmInput.value = um.toFixed(5);

        // Keep the block covering the same amount of tissue. Without
        // this, moving one level changes the physical field by the
        // downsample factor, and the coefficients change with it for a
        // reason that has nothing to do with the level.
        //
        // Derived from a remembered field rather than by scaling the
        // current side, so a trip out to a level that hits the size cap
        // and back again returns to where it started.
        if (previousLevel !== undefined && previousLevel !== state.level) {
            const want = state.targetFieldUm / um;
            state.sideClamped = false;
            if (state.sizeMode === 'cubic') {
                const stepped = Math.round(want / 16) * 16;
                const side = Math.max(
                    16, Math.min(MAX_BLOCK_SIDE, stepped)
                );
                state.sideClamped = side !== stepped;
                state.settingSide = true;
                sideSlider.noUiSlider.set(side);
                state.settingSide = false;
            } else {
                ['bt-size-z', 'bt-size-y', 'bt-size-x'].forEach((id) => {
                    const node = el(id);
                    const side = Math.max(
                        8, Math.min(MAX_BLOCK_SIDE, Math.round(want))
                    );
                    state.sideClamped =
                        state.sideClamped || side !== Math.round(want);
                    node.value = side;
                });
            }
        }
        updateNullNote();
        updateSizeNote();
    }

    /**
     * Lateral voxel size at a pyramid level, in micrometres.
     * @param {number} level Level index.
     * @returns {number} Micrometres per voxel along x.
     */
    function lateralUmAt(level) {
        const lvl = state.levels[level];
        return state.voxelZyx[2] * (state.shape0[2] / lvl.shape_zyx[2]);
    }

    /**
     * Default analysis level: the one giving a workable physical field.
     *
     * Level 0 is a poor default here. A 128-voxel block at full
     * resolution covers about 20 um -- a couple of cells -- where the
     * fit is dominated by whatever structure happens to be present,
     * and it is also the most expensive level to read. Aiming for a
     * field of roughly a tissue-scale distance gives a steadier
     * coefficient for less I/O.
     *
     * @returns {number} Level index.
     */
    function chooseAnalysisLevel() {
        let best = 0;
        let bestGap = Infinity;
        for (let i = 0; i < state.levels.length; i++) {
            const field = lateralUmAt(i) * DEFAULT_BLOCK_SIDE;
            const gap = Math.abs(field - TARGET_FIELD_UM);
            if (gap < bestGap) { bestGap = gap; best = i; }
        }
        return best;
    }

    /** Warn when too few regions are placed for a stable fit. */
    function updateRegionWarning() {
        const n = state.regions.length;
        if (n === 0 || n >= MIN_STABLE_REGIONS) {
            regionWarn.style.display = 'none';
            return;
        }
        regionWarn.style.display = 'block';
        regionWarn.style.background = '#FFF3E0';
        regionWarn.innerHTML =
            '<b>' + n + ' region' + (n === 1 ? '' : 's') +
            '</b> is too few for a stable coefficient. Pooling few ' +
            'regions inflates alpha several-fold &mdash; use ' +
            '<b>Auto-place grid</b> to get ' + MIN_STABLE_REGIONS + '+.';
    }

    /** Redraw the region list and the run button. */
    function updateRegions() {
        regionList.innerHTML = '';
        if (!state.regions.length) {
            regionList.innerHTML =
                '<div class="bt-empty">Click the viewer<br>to add a region</div>';
            clearRegionsBtn.style.display = 'none';
        } else {
            state.regions.forEach((region, index) => {
                const row = document.createElement('div');
                row.className = 'bt-region';
                row.innerHTML =
                    '<span>#' + index + ' z' + region[0] + ' y' +
                    region[1] + ' x' + region[2] + '</span>' +
                    '<span class="rm" title="Remove">&times;</span>';
                row.querySelector('.rm').addEventListener('click', () => {
                    state.regions.splice(index, 1);
                    updateRegions();
                    drawPins();
                });
                regionList.appendChild(row);
            });
            clearRegionsBtn.style.display = 'block';
        }
        runBtn.textContent = 'Run Diagnostics (' + state.regions.length + ')';
        runBtn.disabled = state.regions.length === 0 ||
            state.selected.size < 2;
        updateSizeNote();
        updateRegionWarning();
    }

    /**
     * Describe the displacement offsets the current settings imply.
     *
     * The null is not one shifted fit but the median of many: every
     * offset in [window, 3 x window], on each of the two lateral axes.
     * Spelling out the actual numbers is the quickest way to see when
     * a window has collapsed to a single offset, or to none at all.
     *
     * @returns {object} ``{count, lo, hi, loUm, hiUm}``; count 0 when
     *     the window is unusable at this voxel size.
     */
    function nullOffsets() {
        const voxel = parseFloat(voxelUmInput.value);
        const window = parseFloat(windowUmInput.value);
        if (!(voxel > 0) || !(window > 0)) {
            return { count: 0, lo: 0, hi: 0, loUm: 0, hiUm: 0 };
        }
        const lo = Math.max(1, Math.ceil(window / voxel));
        const hi = Math.floor((3 * window) / voxel);
        if (hi < lo) {
            return { count: 0, lo: lo, hi: hi, loUm: 0, hiUm: 0 };
        }
        return {
            count: hi - lo + 1,
            lo: lo,
            hi: hi,
            loUm: lo * voxel,
            hiUm: hi * voxel
        };
    }

    /** Fill the method explainer, including the live offset numbers. */
    function renderHelp() {
        const off = nullOffsets();
        const voxel = parseFloat(voxelUmInput.value) || 0;
        let offsetText;
        if (off.count === 0) {
            offsetText =
                '<b>No displaced null with these settings.</b> The ' +
                'window is smaller than one voxel at ' +
                voxel.toFixed(4) + ' &micro;m, so there is no whole ' +
                'number of voxels to shift by. Raise the window, or ' +
                'analyse at a finer level.';
        } else {
            offsetText =
                'Right now: shifts of <b>' + off.lo + '&ndash;' + off.hi +
                ' voxels</b> (' + off.loUm.toFixed(2) + '&ndash;' +
                off.hiUm.toFixed(2) + ' &micro;m) &times; <b>2 axes</b> ' +
                '= <b>' + (off.count * 2) + ' fits</b>, and the null is ' +
                'their median.';
        }

        helpBox.innerHTML =
            '<h4>What is measured</h4>' +
            'For an ordered pair, every voxel where the <i>source</i> ' +
            'is bright contributes to a straight-line fit through the ' +
            'origin. That slope is <b>observed</b>. But two channels ' +
            'imaging the same tissue correlate even with no light ' +
            'leaking between them, so the observed slope on its own ' +
            'says nothing.' +

            '<h4>The displaced null</h4>' +
            'The same fit is repeated with the destination channel ' +
            'slid sideways. Real optical coupling is co-located, so it ' +
            'disappears under a shift; shared tissue structure is much ' +
            'wider than the shift, so it survives. What is left is the ' +
            'part structure explains. <b>&alpha; = observed &minus; ' +
            'displaced</b> is the shaded wedge in the grid.' +

            '<h4>It is a median, not one shift</h4>' +
            'Every offset from <code>window</code> to <code>3 &times; ' +
            'window</code> is tried, on both lateral axes, and the ' +
            'median of those slopes becomes the null. One shift could ' +
            'land on a structure by chance; the median will not.' +
            '<div class="bt-offsets">' + offsetText + '</div>' +

            '<h4>Window &micro;m</h4>' +
            'The one genuinely scientific choice here. It has to sit ' +
            '<b>above the optical resolution</b>, so real coupling has ' +
            'decorrelated, and <b>below the tissue correlation ' +
            'length</b>, so structure has not. Too small and &alpha; ' +
            'collapses to zero; too large and structure decorrelates ' +
            'too, inflating &alpha;.' +

            '<h4>Voxel size &micro;m</h4>' +
            'Read from the OMEZarr metadata and used only to turn the ' +
            'window into whole voxels. It follows the analysis level, ' +
            'so changing level changes it too.' +

            '<h4>Bright n&sigma;</h4>' +
            'Fits use voxels where the <i>source</i> exceeds this many ' +
            'robust sigmas. Selecting on the source and not the ' +
            'destination matters: conditioning on the destination would ' +
            'be conditioning on what is being predicted, and biases ' +
            '&alpha; downward.' +

            '<h4>Sample pts</h4>' +
            'Display only. Every fit uses all voxels; this just caps ' +
            'how many are drawn.' +

            '<h4>Signal gate</h4>' +
            'Regions whose brightest channel has fewer than this ' +
            'fraction of bright voxels are dropped as background. ' +
            'Noise alone puts about 0.13% above 3&sigma;, so 0.002 ' +
            'sits just above chance. Background has nothing to ' +
            'correlate, and a pair whose source is never bright ' +
            'returns NaN.' +

            '<h4>What it cannot tell you</h4>' +
            'Co-location alone cannot separate optical leakage from ' +
            'two genuinely co-expressed labels. A high &alpha; says ' +
            'the channels track each other locally, not why.';
    }

    /** Refresh the note explaining whether a displaced null is possible. */
    function updateNullNote() {
        const off = nullOffsets();
        if (off.count > 0) {
            nullNote.innerHTML =
                'Displaced null <b>on</b>: median of <b>' +
                (off.count * 2) + '</b> shifted fits (' + off.lo +
                '\u2013' + off.hi + ' vox = ' + off.loUm.toFixed(2) +
                '\u2013' + off.hiUm.toFixed(2) +
                ' \u00b5m, both lateral axes). The red wedge between ' +
                'the two fits is the local coupling.';
            nullNote.style.background = '#E8F5E9';
        } else {
            const voxel = parseFloat(voxelUmInput.value);
            // The usual cause at a coarse level is a window smaller
            // than one voxel: there is then no whole number of voxels
            // to displace by, and alpha silently becomes unavailable.
            const hint = voxel > 0
                ? ' At ' + voxel.toFixed(3) + ' µm per voxel on ' +
                  'level ' + state.level + ', the window must be at ' +
                  'least <b>' + voxel.toFixed(2) + ' µm</b> to ' +
                  'displace by one voxel.'
                : '';
            nullNote.innerHTML =
                'Displaced null <b>off</b> &mdash; only the raw observed ' +
                'slope is reported, which conflates coupling with shared ' +
                'tissue structure.' + hint;
            nullNote.style.background = '#FFF3E0';
        }
    }

    /* ---------------- channels + viewer ---------------- */

    /** Render the channel include/exclude chips. */
    function renderChannels() {
        channelsBox.innerHTML = '';
        state.channels.forEach((channel) => {
            const chip = document.createElement('span');
            const on = state.selected.has(channel.name);
            chip.className = 'bt-chan' + (on ? '' : ' off');
            chip.innerHTML = '<span class="dot" style="background:' +
                window.seeSpotColor(channel.name) + '"></span>' + channel.name;
            chip.addEventListener('click', () => {
                if (state.selected.has(channel.name)) {
                    state.selected.delete(channel.name);
                } else {
                    state.selected.add(channel.name);
                }
                renderChannels();
                updateRegions();
            });
            channelsBox.appendChild(chip);
        });
    }

    /** Rebuild the slice slider for the current plane. */
    function buildSliceSlider() {
        const spec = PLANES[state.plane];
        const max = state.shape0[spec.fixed] - 1;
        state.slice = Math.min(state.slice, max);
        sliceAxis.textContent = spec.label;
        if (sliceSlider.noUiSlider) {
            sliceSlider.noUiSlider.destroy();
        }
        noUiSlider.create(sliceSlider, {
            start: [state.slice],
            connect: [true, false],
            step: 1,
            range: { min: 0, max: Math.max(max, 1) },
            format: {
                to: (v) => Math.round(v),
                from: (v) => Number(v)
            }
        });
        // 'update', not 'set': scrubbing is now local, so the image can
        // follow the handle instead of waiting for release.
        sliceSlider.noUiSlider.on('update', (values) => {
            state.slice = parseInt(values[0], 10);
            sliceVal.textContent = state.slice;
            showSlice();
        });
    }

    /**
     * The row/col window currently shown, in view-level voxels.
     *
     * Everything that converts between the canvas and the volume goes
     * through this, so cropping cannot put clicks and pins out of step.
     * @returns {object} ``{row0, row1, col0, col1}``.
     */
    function viewBox() {
        const spec = PLANES[state.plane];
        const shape = state.levels[state.viewLevel].shape_zyx;
        return state.crop || {
            row0: 0, row1: shape[spec.row],
            col0: 0, col1: shape[spec.col]
        };
    }

    /**
     * Fraction across the current view -> level-0 voxel coordinate.
     * @param {number} fracRow Vertical position, 0 to 1.
     * @param {number} fracCol Horizontal position, 0 to 1.
     * @returns {number[]} ``[row, col]`` in level-0 voxels.
     */
    function viewToVolume(fracRow, fracCol) {
        const spec = PLANES[state.plane];
        const box = viewBox();
        const ratio = levelRatio(state.viewLevel);
        return [
            Math.round(
                (box.row0 + fracRow * (box.row1 - box.row0)) *
                ratio[spec.row]
            ),
            Math.round(
                (box.col0 + fracCol * (box.col1 - box.col0)) *
                ratio[spec.col]
            )
        ];
    }

    /**
     * Level-0 coordinate -> fraction across the current view.
     * @param {number} row Level-0 row coordinate.
     * @param {number} col Level-0 column coordinate.
     * @returns {number[]} ``[fracRow, fracCol]``; outside 0..1 when the
     *     point lies beyond the crop.
     */
    function volumeToView(row, col) {
        const spec = PLANES[state.plane];
        const box = viewBox();
        const ratio = levelRatio(state.viewLevel);
        return [
            (row / ratio[spec.row] - box.row0) / (box.row1 - box.row0),
            (col / ratio[spec.col] - box.col0) / (box.col1 - box.col0)
        ];
    }

    /**
     * Bounding box of actual signal in a slab, in view-level voxels.
     *
     * These volumes are a stitched mosaic inside a much larger bounding
     * box, so a lot of every plane is empty. Finding the imaged part
     * costs nothing here -- the slab is already in memory -- and lets
     * the next fetch spend its pixels on tissue.
     *
     * @param {object} rec Slab record.
     * @returns {object|null} ``{row0, row1, col0, col1}`` in level
     *     voxels, or null when nothing stands out from the background.
     */
    function contentBox(rec) {
        const planes = Math.min(rec.n, 6);
        const stepPlane = Math.max(1, Math.floor(rec.n / planes));
        const rows = rec.rows;
        const cols = rec.cols;
        const proj = new Uint16Array(rows * cols);
        for (let k = 0; k < rec.n; k += stepPlane) {
            const base = k * rows * cols;
            for (let i = 0; i < rows * cols; i++) {
                const v = rec.data[base + i];
                if (v > proj[i]) { proj[i] = v; }
            }
        }
        // Background is the flat floor of the projection; anything a
        // little above it counts as imaged.
        let lo = 65535;
        let hi = 0;
        for (let i = 0; i < proj.length; i++) {
            if (proj[i] < lo) { lo = proj[i]; }
            if (proj[i] > hi) { hi = proj[i]; }
        }
        if (hi <= lo) { return null; }
        const cut = lo + (hi - lo) * CONTENT_CUT;

        let r0 = rows, r1 = -1, c0 = cols, c1 = -1;
        for (let r = 0; r < rows; r++) {
            const base = r * cols;
            for (let c = 0; c < cols; c++) {
                if (proj[base + c] > cut) {
                    if (r < r0) { r0 = r; }
                    if (r > r1) { r1 = r; }
                    if (c < c0) { c0 = c; }
                    if (c > c1) { c1 = c; }
                }
            }
        }
        if (r1 < r0 || c1 < c0) { return null; }

        // Back to level voxels, through this slab's own decimation and
        // whatever crop produced it, plus a small margin.
        const box = viewBox();
        const scaleRow = (box.row1 - box.row0) / rows;
        const scaleCol = (box.col1 - box.col0) / cols;
        const padRow = (r1 - r0) * scaleRow * CONTENT_PAD;
        const padCol = (c1 - c0) * scaleCol * CONTENT_PAD;
        return {
            row0: Math.max(0, Math.round(box.row0 + r0 * scaleRow - padRow)),
            row1: Math.round(box.row0 + (r1 + 1) * scaleRow + padRow),
            col0: Math.max(0, Math.round(box.col0 + c0 * scaleCol - padCol)),
            col1: Math.round(box.col0 + (c1 + 1) * scaleCol + padCol)
        };
    }

    /**
     * Key for one cached slab.
     * @param {object} ch Channel record.
     * @param {number} start First plane index at the view level.
     * @returns {string} Cache key.
     */
    function slabKey(ch, start) {
        const b = viewBox();
        return [ch.name, state.plane, state.viewLevel, start,
                b.row0, b.row1, b.col0, b.col1].join('|');
    }

    /**
     * First plane of the chunk-aligned slab containing an index.
     *
     * Snapping to the chunk grid is what keeps this cheap: a slab that
     * straddles two rows of chunks fetches twice as many as one that
     * sits inside a single row.
     * @param {number} pos Plane index at the view level.
     * @returns {number} Aligned start index.
     */
    function slabStart(pos) {
        return Math.floor(pos / state.slabDepth) * state.slabDepth;
    }

    /**
     * Fetch one slab, or return the cached copy.
     * @param {object} ch Channel record.
     * @param {number} start Aligned first plane index.
     * @returns {Promise<object>} Resolves to the cached slab record.
     */
    function fetchSlab(ch, start) {
        const key = slabKey(ch, start);
        const hit = state.slabs.get(key);
        if (hit) { return Promise.resolve(hit); }
        const pending = state.pending.get(key);
        if (pending) { return pending; }

        const url = '/api/bleedthrough/slab?zarr_path=' +
            encodeURIComponent(ch.zarr_path) +
            '&plane=' + state.plane +
            '&start=' + start +
            '&count=' + state.slabDepth +
            '&level=' + state.viewLevel +
            '&target_px=' + VIEW_TARGET_PX +
            (state.crop
                ? '&crop=' + [state.crop.row0, state.crop.row1,
                              state.crop.col0, state.crop.col1].join(',')
                : '');

        const job = fetch(url)
            .then((r) => {
                if (!r.ok) { throw new Error('slab ' + r.status); }
                const dims = (r.headers.get('X-Slab-Shape') || '')
                    .split(',').map(Number);
                const step = parseInt(r.headers.get('X-Step'), 10) || 1;
                return r.arrayBuffer().then((buf) => ({
                    data: new Uint16Array(buf),
                    n: dims[0], rows: dims[1], cols: dims[2],
                    start: start, step: step,
                    hist: new Map()
                }));
            })
            .then((rec) => {
                state.slabs.set(key, rec);
                // Bound memory: a slab is a few MB and a session can
                // wander a long way through a volume.
                if (state.slabs.size > MAX_SLABS) {
                    state.slabs.delete(state.slabs.keys().next().value);
                }
                state.pending.delete(key);
                return rec;
            })
            .catch((err) => {
                state.pending.delete(key);
                throw err;
            });
        state.pending.set(key, job);
        return job;
    }

    /**
     * Percentile bounds for one plane, from a cached histogram.
     *
     * The histogram is built once per plane; after that a contrast
     * change is two lookups, which is why the slider feels immediate.
     * @param {object} rec Slab record.
     * @param {number} index Plane index inside the slab.
     * @returns {number[]} The low and high intensity cut.
     */
    function planeBounds(rec, index) {
        let hist = rec.hist.get(index);
        if (!hist) {
            const bins = new Uint32Array(HIST_BINS);
            const len = rec.rows * rec.cols;
            const base = index * len;
            // Every 3rd pixel. Percentiles over 20k+ samples are
            // indistinguishable from percentiles over all of them, and
            // this runs on every new plane while scrubbing.
            const stride = 3;
            let max = 1;
            for (let i = 0; i < len; i += stride) {
                const v = rec.data[base + i];
                if (v > max) { max = v; }
            }
            const scale = (HIST_BINS - 1) / max;
            let total = 0;
            for (let i = 0; i < len; i += stride) {
                bins[(rec.data[base + i] * scale) | 0] += 1;
                total += 1;
            }
            hist = { bins: bins, max: max, total: total };
            rec.hist.set(index, hist);
        }
        const pick = (pct) => {
            const want = (pct / 100) * hist.total;
            let seen = 0;
            for (let b = 0; b < HIST_BINS; b++) {
                seen += hist.bins[b];
                if (seen >= want) {
                    return (b * hist.max) / (HIST_BINS - 1);
                }
            }
            return hist.max;
        };
        return [pick(state.contrast[0]), pick(state.contrast[1])];
    }

    /**
     * Paint one plane of a slab onto the canvas with the current window.
     * @param {object} rec Slab record.
     * @param {number} index Plane index inside the slab.
     */
    function paint(rec, index) {
        const [low, high] = planeBounds(rec, index);
        const span = Math.max(high - low, 1);
        const len = rec.rows * rec.cols;
        const base = index * len;
        const ctx = viewerImg.getContext('2d');
        if (viewerImg.width !== rec.cols || viewerImg.height !== rec.rows) {
            viewerImg.width = rec.cols;
            viewerImg.height = rec.rows;
            state.frame = null;
        }
        // Reuse the pixel buffer across repaints; scrubbing allocates a
        // quarter-megabyte per frame otherwise.
        if (!state.frame) {
            state.frame = ctx.createImageData(rec.cols, rec.rows);
            state.frame.data.fill(255);   // alpha, written once
        }
        const out = state.frame.data;

        // A value -> byte table beats per-pixel arithmetic only when
        // there are more pixels than table entries. Building a 65k
        // table for a 24k-pixel preview plane costs more than it saves,
        // and the window moves on every plane while scrubbing.
        const hist = rec.hist.get(index);
        const lutSize = hist.max + 2;
        if (len > lutSize) {
            if (!state.lut || state.lut.length < lutSize) {
                state.lut = new Uint8Array(Math.max(lutSize, 4096));
            }
            const lut = state.lut;
            for (let v = 0; v < lutSize; v++) {
                const scaled = ((v - low) / span) * 255;
                lut[v] = scaled < 0 ? 0 : (scaled > 255 ? 255 : scaled);
            }
            for (let i = 0; i < len; i++) {
                const g = lut[rec.data[base + i]];
                const o = i << 2;
                out[o] = g;
                out[o + 1] = g;
                out[o + 2] = g;
            }
        } else {
            const gain = 255 / span;
            for (let i = 0; i < len; i++) {
                let g = (rec.data[base + i] - low) * gain;
                g = g < 0 ? 0 : (g > 255 ? 255 : g);
                const o = i << 2;
                out[o] = g;
                out[o + 1] = g;
                out[o + 2] = g;
            }
        }
        ctx.putImageData(state.frame, 0, 0);
        viewerImg.style.display = 'block';
        const shape = state.levels[state.viewLevel].shape_zyx;
        const spec = PLANES[state.plane];
        viewerDims.textContent = rec.cols + ' x ' + rec.rows +
            '  (L' + state.viewLevel + ' /' + rec.step + ' of ' +
            shape[spec.col] + 'x' + shape[spec.row] + ')';
        drawPins();
    }

    /**
     * Show the current slice, fetching its slab only if needed.
     *
     * Everything the viewer does interactively -- scrubbing Z, moving
     * the contrast handles -- is served from the slab already in
     * memory, so it costs a repaint rather than a request.
     */
    function showSlice() {
        if (!state.channels.length || !state.levels.length) { return; }
        // refreshView picks the view level; until it has, state.viewLevel
        // is still 0. A slab at full resolution spans thousands of
        // chunks and never returns, so never paint before it is set.
        if (!state.viewReady) { return; }
        const ch = state.channels.find(
            (c) => c.name === viewChannel.value) || state.channels[0];
        const spec = PLANES[state.plane];
        const ratio = levelRatio(state.viewLevel);
        const pos = Math.floor(state.slice / ratio[spec.fixed]);
        const start = slabStart(pos);
        const rec = state.slabs.get(slabKey(ch, start));

        if (rec) {
            paint(rec, Math.min(pos - start, rec.n - 1));
            prefetchNeighbour(ch, pos, start);
            preloadVolume(ch);
            return;
        }
        // No sharp slab here yet, but the whole stack may already be
        // held coarsely; showing that beats showing nothing.
        if (state.volume && pos < state.volume.n) {
            paint(state.volume, pos);
            // Sharpen only once the slider settles. Firing per step
            // would launch a slab read for every position crossed, and
            // each one costs hundreds of megabytes server-side.
            clearTimeout(state.sharpenTimer);
            state.sharpenTimer = setTimeout(() => {
                const now = Math.floor(
                    state.slice / levelRatio(state.viewLevel)[spec.fixed]
                );
                const wanted = slabStart(now);
                fetchSlab(ch, wanted)
                    .then((loaded) => {
                        const latest = Math.floor(
                            state.slice /
                            levelRatio(state.viewLevel)[spec.fixed]
                        );
                        if (slabStart(latest) === wanted) {
                            paint(
                                loaded,
                                Math.min(latest - wanted, loaded.n - 1)
                            );
                        }
                    })
                    .catch(() => {});
            }, SHARPEN_IDLE_MS);
            return;
        }
        viewerBusy.style.display = 'block';
        viewerHint.textContent = 'Loading slab…';
        const token = ++state.loadToken;
        fetchSlab(ch, start)
            .then((loaded) => {
                if (token !== state.loadToken) { return; }
                paint(loaded, Math.min(pos - start, loaded.n - 1));
                viewerHint.textContent =
                    'Click to place a block · ' + loaded.n +
                    ' planes cached';
                if (autoFit(loaded)) { return; }
                prefetchNeighbour(ch, pos, start);
                preloadVolume(ch);
            })
            .catch(() => {
                if (token === state.loadToken) {
                    viewerHint.textContent = 'Could not load planes';
                }
            })
            .finally(() => {
                if (token === state.loadToken) {
                    viewerBusy.style.display = 'none';
                }
            });
    }

    /**
     * Pull the whole plane stack at low resolution, in the background.
     *
     * The coarse levels are small enough to hold entirely, and once one
     * is in memory every Z is instant with no slab boundaries to cross.
     * Deliberately coarser than a slab: this is the always-available
     * fallback, and a cached slab still wins when there is one.
     *
     * @param {object} ch Channel record.
     */
    function preloadVolume(ch) {
        const spec = PLANES[state.plane];
        const lvl = state.levels[state.viewLevel];
        const box = viewBox();
        const planes = lvl.shape_zyx[spec.fixed];
        const step = Math.max(
            1,
            Math.ceil(
                Math.max(box.row1 - box.row0, box.col1 - box.col0) /
                VOLUME_TARGET_PX
            )
        );
        const rows = Math.ceil((box.row1 - box.row0) / step);
        const cols = Math.ceil((box.col1 - box.col0) / step);
        const bytes = planes * rows * cols * 2;
        if (bytes > VOLUME_BUDGET_BYTES) {
            return;   // too deep to hold; slabs remain the only source
        }

        const key = [ch.name, state.plane, state.viewLevel,
                     box.row0, box.row1, box.col0, box.col1].join('|');
        if (state.volumeKey === key || state.volumeLoading === key) {
            return;
        }
        state.volumeLoading = key;

        const url = '/api/bleedthrough/slab?zarr_path=' +
            encodeURIComponent(ch.zarr_path) +
            '&plane=' + state.plane +
            '&start=0&count=' + planes +
            '&level=' + state.viewLevel +
            '&target_px=' + VOLUME_TARGET_PX +
            (state.crop
                ? '&crop=' + [state.crop.row0, state.crop.row1,
                              state.crop.col0, state.crop.col1].join(',')
                : '');

        fetch(url)
            .then((r) => {
                if (!r.ok) { throw new Error('volume ' + r.status); }
                const dims = (r.headers.get('X-Slab-Shape') || '')
                    .split(',').map(Number);
                const st = parseInt(r.headers.get('X-Step'), 10) || 1;
                return r.arrayBuffer().then((buf) => ({
                    data: new Uint16Array(buf),
                    n: dims[0], rows: dims[1], cols: dims[2],
                    start: 0, step: st, hist: new Map()
                }));
            })
            .then((rec) => {
                if (state.volumeLoading !== key) { return; }
                state.volume = rec;
                state.volumeKey = key;
                state.volumeLoading = null;
                viewerHint.textContent =
                    'Click to place a block · all ' + rec.n +
                    ' planes cached';
            })
            .catch(() => { state.volumeLoading = null; });
    }

    /**
     * Crop to the imaged region once, after the first slab arrives.
     *
     * Only worth a refetch when the margin is actually large; a volume
     * that already fills its bounding box is left alone.
     *
     * @param {object} rec The slab just loaded.
     * @returns {boolean} True when a refetch was started.
     */
    function autoFit(rec) {
        if (state.autoFitDone || state.crop) { return false; }
        state.autoFitDone = true;
        const box = contentBox(rec);
        if (!box) { return false; }
        const view = viewBox();
        const area = (box.row1 - box.row0) * (box.col1 - box.col0);
        const whole = (view.row1 - view.row0) * (view.col1 - view.col0);
        if (area >= whole * AUTOFIT_MIN_GAIN) { return false; }
        state.crop = box;
        state.slabs.clear();
        state.pending.clear();
        state.loadToken += 1;
        showSlice();
        return true;
    }

    /**
     * Set or clear the crop and reload.
     * @param {object|null} box Crop in view-level voxels, or null.
     */
    function setCrop(box) {
        state.crop = box;
        state.slabs.clear();
        state.pending.clear();
        state.loadToken += 1;
        state.frame = null;
        state.volume = null;
        state.volumeKey = null;
        state.volumeLoading = null;
        showSlice();
    }

    /**
     * Warm the next slab when the slider nears the edge of this one.
     * @param {object} ch Channel record.
     * @param {number} pos Current plane index at the view level.
     * @param {number} start First index of the current slab.
     */
    function prefetchNeighbour(ch, pos, start) {
        const spec = PLANES[state.plane];
        const extent = state.levels[state.viewLevel].shape_zyx[spec.fixed];
        const within = pos - start;
        let next = null;
        if (within > state.slabDepth - PREFETCH_EDGE) {
            next = start + state.slabDepth;
        } else if (within < PREFETCH_EDGE && start > 0) {
            next = start - state.slabDepth;
        }
        if (next === null || next < 0 || next >= extent) { return; }
        if (state.slabs.has(slabKey(ch, next))) { return; }
        fetchSlab(ch, next).catch(() => {});
    }

    /**
     * Re-pick the view level, drop cached slabs and repaint.
     *
     * Called whenever the cached planes stop being the right ones: a
     * different plane orientation, level or channel.
     */
    function refreshView() {
        if (!state.levels.length) { return; }
        state.viewLevel = chooseLevel(state.plane);
        const spec = PLANES[state.plane];
        const lvl = state.levels[state.viewLevel];
        // Slabs are cheapest inside one row of chunks, so prefer the
        // chunk depth along the fixed axis and cap it for payload.
        const chunk = (lvl.chunks_zyx && lvl.chunks_zyx[spec.fixed]) || 0;
        state.slabDepth = Math.max(
            1, Math.min(chunk || DEFAULT_SLAB_DEPTH, DEFAULT_SLAB_DEPTH)
        );
        state.slabs.clear();
        state.pending.clear();
        state.loadToken += 1;
        state.viewReady = true;
        // A new plane or level means a new content box.
        state.crop = null;
        state.autoFitDone = false;
        state.frame = null;
        state.volume = null;
        state.volumeKey = null;
        state.volumeLoading = null;
        setZoomToggle('fit');
        showSlice();
    }

    /** Draw region markers over the viewer image. */
    function drawPins() {
        pinsBox.innerHTML = '';
        if (!viewerImg.clientWidth || !state.shape0) { return; }
        const spec = PLANES[state.plane];
        const rect = viewerImg.getBoundingClientRect();
        const wrapRect = viewerWrap.getBoundingClientRect();
        const offsetLeft = rect.left - wrapRect.left;
        const offsetTop = rect.top - wrapRect.top;
        const size = blockSize();

        state.regions.forEach((region, index) => {
            const frac = volumeToView(region[spec.row], region[spec.col]);
            const fracRow = frac[0];
            const fracCol = frac[1];
            if (fracRow < 0 || fracRow > 1 || fracCol < 0 || fracCol > 1) {
                return;   // outside the zoomed view
            }
            const distance = Math.abs(region[spec.fixed] - state.slice);
            const inSlab = distance <= size[spec.fixed] / 2;
            const pin = document.createElement('div');
            pin.className = 'bt-pin';
            pin.style.left = (offsetLeft + fracCol * rect.width) + 'px';
            pin.style.top = (offsetTop + fracRow * rect.height) + 'px';
            pin.style.opacity = inSlab ? '1' : '0.3';
            pin.innerHTML = '<span>' + index + '</span>';
            pinsBox.appendChild(pin);
        });
    }

    viewerImg.addEventListener('click', function (event) {
        if (!state.shape0) { return; }
        const spec = PLANES[state.plane];
        const rect = viewerImg.getBoundingClientRect();
        const fracCol = (event.clientX - rect.left) / rect.width;
        const fracRow = (event.clientY - rect.top) / rect.height;
        const mapped = viewToVolume(fracRow, fracCol);
        const region = [0, 0, 0];
        region[spec.fixed] = state.slice;
        region[spec.row] = mapped[0];
        region[spec.col] = mapped[1];
        state.regions.push(region);
        updateRegions();
        drawPins();
    });

    window.addEventListener('resize', () => {
        drawPins();
        if (chart) { chart.resize(); }
        if (matrixChart) { matrixChart.resize(); }
    });

    /* ---------------- detect ---------------- */

    /** Detect channels and grid metadata for the current path. */
    function detect() {
        const basePath = pathInput.value.trim();
        if (!basePath) { return; }
        detectBtn.disabled = true;
        detectStatus.textContent = 'Detecting channels…';
        fetch('/api/bleedthrough/detect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ base_path: basePath })
        })
            .then((response) => response.json().then((data) => ({
                ok: response.ok, data
            })))
            .then(({ ok, data }) => {
                if (!ok) {
                    detectStatus.textContent = data.error || 'Detection failed';
                    toast(data.error || 'Detection failed', 'error');
                    return;
                }
                state.basePath = basePath;
                state.channels = data.channels;
                state.selected = new Set(data.channels.map((c) => c.name));
                state.levels = data.levels;
                state.shape0 = data.shape_zyx;
                state.chunks = data.chunks_zyx;
                state.voxelZyx = data.voxel_size_zyx;
                state.slice = Math.floor(data.shape_zyx[0] / 2);

                // Analysis level. Coarser levels read far less data, so
                // many more regions are affordable -- which matters more
                // for a trustworthy coefficient than full resolution.
                levelSelect.innerHTML = data.levels.map((l) => {
                    const um = (data.voxel_size_zyx[2] *
                        (data.shape_zyx[2] / l.shape_zyx[2])).toFixed(3);
                    return '<option value="' + l.level + '">L' + l.level +
                        '  ' + um + ' µm/vox  ' +
                        l.shape_zyx.join('×') + '</option>';
                }).join('');
                state.level = chooseAnalysisLevel();
                levelSelect.value = String(state.level);
                state.targetFieldUm =
                    DEFAULT_BLOCK_SIDE * lateralUmAt(state.level);
                state.settingSide = true;
                sideSlider.noUiSlider.set(DEFAULT_BLOCK_SIDE);
                state.settingSide = false;
                applyLevel();
                detectStatus.innerHTML = data.channels.length +
                    ' channels &middot; ' + data.shape_zyx.join(' x ') +
                    ' &middot; ' + data.levels.length + ' levels';
                viewChannel.innerHTML = data.channels
                    .map((c) => '<option>' + c.name + '</option>').join('');
                if (data.channels.length > 1) { viewChannel.selectedIndex = 1; }

                renderChannels();
                buildSliceSlider();
                updateNullNote();
                updateRegions();
                refreshView();
            })
            .catch((error) => {
                detectStatus.textContent = 'Detection failed';
                toast('Detection failed: ' + error.message, 'error');
            })
            .finally(() => { detectBtn.disabled = false; });
    }

    let detectTimer = null;
    pathInput.addEventListener('input', function () {
        clearTimeout(detectTimer);
        detectTimer = setTimeout(detect, 700);
    });
    detectBtn.addEventListener('click', detect);
    datasetSelect.addEventListener('change', function () {
        if (this.value) {
            pathInput.value = this.value;
            detect();
        }
    });

    /* ---------------- density grid ---------------- */

    /**
     * Build the ECharts option for the N x N density grid.
     * @param {object} result Payload from /api/bleedthrough/run.
     * @returns {object} An ECharts option object.
     */
    function buildGridOption(result) {
        const names = result.channels;
        const count = names.length;
        const visible = result.points.filter(
            (p) => !state.hiddenRegions.has(p[0]));

        // Even stride rather than a random draw, so toggling a region
        // does not reshuffle every other point on screen.
        const stride = Math.max(1, Math.ceil(visible.length / DISPLAY_CAP));
        const rows = visible.filter((_, i) => i % stride === 0);
        // Kept so a click's dataIndex resolves back to a real voxel.
        state.displayRows = rows;

        let high = 6;
        rows.forEach((p) => {
            for (let c = 0; c < count; c++) {
                if (p[4 + c] > high) { high = p[4 + c]; }
            }
        });
        high = Math.min(high, 40);

        const grids = [];
        const xAxes = [];
        const yAxes = [];
        const series = [];
        const titles = [];
        const padX = 5.5;
        const padY = 4.0;
        const cellW = (94 - padX) / count;
        const cellH = (93 - padY) / count;

        for (let row = 0; row < count; row++) {
            for (let col = 0; col < count; col++) {
                const k = row * count + col;
                grids.push({
                    left: (padX + col * cellW) + '%',
                    top: (padY + row * cellH) + '%',
                    width: (cellW - 1.6) + '%',
                    height: (cellH - 2.2) + '%'
                });
                const common = {
                    gridIndex: k,
                    min: AXIS_LOW,
                    max: high,
                    axisLabel: { fontSize: 9, color: '#888' },
                    splitLine: { show: false }
                };
                xAxes.push(Object.assign({ type: 'value' }, common));
                yAxes.push(Object.assign({ type: 'value' }, common));

                if (row === col) {
                    // Diagonal: that channel's own intensity histogram.
                    const bins = 48;
                    const counts = new Array(bins).fill(0);
                    rows.forEach((p) => {
                        const v = p[4 + row];
                        const b = Math.floor(
                            ((v - AXIS_LOW) / (high - AXIS_LOW)) * bins);
                        if (b >= 0 && b < bins) { counts[b] += 1; }
                    });
                    series.push({
                        type: 'bar',
                        xAxisIndex: k,
                        yAxisIndex: k,
                        barWidth: '100%',
                        silent: true,
                        itemStyle: { color: window.seeSpotColor(names[row]) },
                        data: counts.map((n, b) => [
                            AXIS_LOW + (b + 0.5) * (high - AXIS_LOW) / bins,
                            n
                        ])
                    });
                    yAxes[k] = Object.assign({}, common, {
                        type: 'log', min: 1, max: null,
                        axisLabel: { show: false }
                    });
                    titles.push({
                        left: (padX + col * cellW + cellW / 2) + '%',
                        top: (padY + row * cellH + 0.4) + '%',
                        text: names[row],
                        textAlign: 'center',
                        textStyle: {
                            fontSize: 12, fontWeight: 'bold',
                            color: window.seeSpotColor(names[row])
                        }
                    });
                } else {
                    // Off-diagonal: column channel against row channel,
                    // one mark per sampled voxel.
                    const data = rows.map((p) => [p[4 + row], p[4 + col]]);
                    series.push({
                        type: 'scatter',
                        xAxisIndex: k,
                        yAxisIndex: k,
                        symbolSize: 2.5,
                        progressive: 2000,
                        progressiveThreshold: 2000,
                        itemStyle: {
                            color: window.seeSpotColor(names[row]),
                            opacity: 0.45
                        },
                        data: data,
                        _pair: [names[row], names[col]]
                    });

                    const entry = result.coefficients[
                        names[row] + '|' + names[col]] || {};
                    const observed = entry.observed;
                    const displaced = entry.displaced;
                    const haveBoth =
                        observed !== null && observed !== undefined &&
                        displaced !== null && displaced !== undefined;

                    // The wedge between the two fits IS the local
                    // coupling: alpha is their difference, so the
                    // shaded area grows with it and closes to nothing
                    // when the displaced null explains the whole slope.
                    // Both fits pass through the origin, so the wedge is
                    // a triangle and can be drawn exactly -- a stacked
                    // area would split at zero when alpha goes negative.
                    // Mirrors the shaded wedge in plot_density_grid.
                    if (haveBoth) {
                        series.push({
                            type: 'custom',
                            xAxisIndex: k,
                            yAxisIndex: k,
                            silent: true,
                            clip: true,
                            data: [[0, 0]],
                            renderItem: (function (obs, nul) {
                                return function (params, api) {
                                    return {
                                        type: 'polygon',
                                        shape: {
                                            points: [
                                                api.coord([0, 0]),
                                                api.coord([high, obs * high]),
                                                api.coord([high, nul * high])
                                            ]
                                        },
                                        style: {
                                            fill: 'rgba(211, 47, 47, 0.22)'
                                        }
                                    };
                                };
                            }(observed, displaced))
                        });
                    }

                    if (observed !== null && observed !== undefined) {
                        series.push({
                            type: 'line', xAxisIndex: k, yAxisIndex: k,
                            silent: true, symbol: 'none',
                            lineStyle: { color: '#111', width: 1.4 },
                            data: [[0, 0], [high, observed * high]]
                        });
                    }
                    if (displaced !== null && displaced !== undefined) {
                        series.push({
                            type: 'line', xAxisIndex: k, yAxisIndex: k,
                            silent: true, symbol: 'none',
                            lineStyle: {
                                color: '#555', width: 1.2, type: 'dashed'
                            },
                            data: [[0, 0], [high, displaced * high]]
                        });
                    }
                    const alpha = entry.alpha;
                    titles.push({
                        left: (padX + col * cellW + 0.6) + '%',
                        top: (padY + row * cellH + 0.4) + '%',
                        text: (alpha === null || alpha === undefined)
                            ? 'obs ' + (observed || 0).toFixed(2)
                            : 'a = ' + alpha.toFixed(3),
                        textStyle: {
                            fontSize: 11, fontWeight: 'bold', color: '#c62828'
                        }
                    });
                }
            }
        }

        // Axis captions along the edges.
        names.forEach((name, i) => {
            titles.push({
                left: '0.4%',
                top: (padY + i * cellH + cellH / 2) + '%',
                text: name,
                textVerticalAlign: 'middle',
                textStyle: {
                    fontSize: 12, fontWeight: 'bold',
                    color: window.seeSpotColor(name)
                }
            });
            titles.push({
                left: (padX + i * cellW + cellW / 2) + '%',
                top: '0.6%',
                text: name,
                textAlign: 'center',
                textStyle: {
                    fontSize: 12, fontWeight: 'bold',
                    color: window.seeSpotColor(name)
                }
            });
        });
        titles.push({
            left: '0.4%', top: '98%',
            text: 'rows = source (x)   columns = destination (y)   ' +
                'axes in sigmas   solid = observed, dashed = displaced null',
            textStyle: { fontSize: 10, color: '#888' }
        });

        return {
            animation: false,
            grid: grids,
            xAxis: xAxes,
            yAxis: yAxes,
            series: series,
            graphic: titles.map((t) => ({
                type: 'text',
                left: t.left,
                top: t.top,
                style: {
                    text: t.text,
                    fontSize: t.textStyle.fontSize,
                    fontWeight: t.textStyle.fontWeight,
                    fill: t.textStyle.color,
                    textAlign: t.textAlign,
                    textVerticalAlign: t.textVerticalAlign
                }
            }))
        };
    }

    /**
     * Build the N x N local-coupling matrix.
     *
     * One cell per ordered pair, holding the alpha the density grid
     * fits: observed slope minus displaced null, so it is the part of
     * the correlation that survives subtracting what shared tissue
     * structure explains.
     *
     * The colour scale tops out at the largest off-diagonal alpha, as
     * asked. The diagonal is excluded rather than drawn: a channel
     * against itself fits a slope of exactly 1 by construction, so its
     * alpha is large and meaningless, and leaving it in would flatten
     * every real value into the bottom of the ramp.
     *
     * @param {object} result Payload from /api/bleedthrough/run.
     * @returns {object|null} An ECharts option, or null with no data.
     */
    function buildMatrixOption(result) {
        const names = result.channels;
        const cells = [];
        let peak = 0;
        let anyFinite = false;

        names.forEach((src, row) => {
            names.forEach((dst, col) => {
                if (src === dst) { return; }
                const entry = result.coefficients[src + '|' + dst] || {};
                const alpha = entry.alpha;
                if (alpha === null || alpha === undefined) {
                    cells.push({ value: [col, row, '-'], entry: entry });
                    return;
                }
                anyFinite = true;
                if (alpha > peak) { peak = alpha; }
                cells.push({ value: [col, row, alpha], entry: entry });
            });
        });
        if (!anyFinite) { return null; }
        const top = peak > 0 ? peak : 1;

        // Dark cells need light text; the ramp crosses over around 55%.
        cells.forEach((cell) => {
            const v = cell.value[2];
            const frac = typeof v === 'number' ? v / top : 0;
            cell.label = { color: frac > 0.55 ? '#fff' : '#263238' };
        });

        return {
            animation: false,
            tooltip: {
                formatter: function (params) {
                    const src = names[params.value[1]];
                    const dst = names[params.value[0]];
                    const e = params.data.entry || {};
                    const fmt = (x) => (x === null || x === undefined)
                        ? 'n/a' : Number(x).toFixed(4);
                    return '<b>' + src + ' &rarr; ' + dst + '</b><br>' +
                        'observed &nbsp;' + fmt(e.observed) + '<br>' +
                        'displaced null &nbsp;' + fmt(e.displaced) + '<br>' +
                        '<b>alpha &nbsp;' + fmt(e.alpha) + '</b><br>' +
                        'fit over ' +
                        (e.n_voxels || 0).toLocaleString() + ' voxels';
                }
            },
            grid: { left: 70, right: 96, top: 34, bottom: 44 },
            xAxis: {
                type: 'category',
                data: names,
                name: 'destination',
                nameLocation: 'middle',
                nameGap: 28,
                nameTextStyle: { fontSize: 11, color: '#666' },
                // No splitArea: its alternating bands read as a
                // checkerboard of data, and the empty diagonal is
                // clearer against plain background.
                splitArea: { show: false },
                axisLabel: {
                    fontSize: 12,
                    fontWeight: 'bold',
                    color: (v) => window.seeSpotColor(v)
                }
            },
            yAxis: {
                type: 'category',
                data: names,
                inverse: true,
                name: 'source',
                nameLocation: 'middle',
                nameGap: 52,
                nameTextStyle: { fontSize: 11, color: '#666' },
                splitArea: { show: false },
                axisLabel: {
                    fontSize: 12,
                    fontWeight: 'bold',
                    color: (v) => window.seeSpotColor(v)
                }
            },
            visualMap: {
                min: 0,
                max: top,
                calculable: true,
                orient: 'vertical',
                right: 8,
                top: 'middle',
                itemHeight: 190,
                precision: 3,
                text: ['α ' + top.toFixed(3), '0'],
                textStyle: { fontSize: 11 },
                inRange: {
                    color: [
                        '#f7f7f7', '#ffe0b2', '#ffb74d',
                        '#f57c00', '#bf360c'
                    ]
                }
            },
            series: [{
                type: 'heatmap',
                data: cells,
                label: {
                    show: true,
                    fontSize: 11,
                    formatter: (p) => (typeof p.value[2] === 'number')
                        ? p.value[2].toFixed(3) : 'n/a'
                },
                itemStyle: {
                    borderColor: '#fff',
                    borderWidth: 2
                },
                emphasis: {
                    itemStyle: {
                        borderColor: '#263238',
                        borderWidth: 2
                    }
                }
            }]
        };
    }

    /** Draw the coupling matrix for a finished run. */
    function renderMatrix(result) {
        const option = buildMatrixOption(result);
        if (!option) {
            matrixCaption.textContent =
                'No coupling matrix: alpha needs the displaced null, ' +
                'which needs both a voxel size and a window.';
            matrixDom.style.display = 'none';
            return;
        }
        // Size the canvas so the cells come out square: a matrix read
        // as a grid of equal weights, not stretched bars.
        const cell = 74;
        const n = result.channels.length;
        matrixDom.style.display = 'block';
        matrixDom.style.height = (34 + 44 + cell * n) + 'px';
        matrixDom.style.width = (70 + 96 + cell * n) + 'px';
        matrixDom.style.maxWidth = '100%';
        const top = option.visualMap.max;
        matrixCaption.innerHTML =
            '<b>Local coupling &alpha;</b> = observed slope &minus; ' +
            'displaced null, per ordered pair. Scale runs 0 to the ' +
            'largest value, <b>' + top.toFixed(3) + '</b>. Diagonal ' +
            'omitted: a channel fits slope 1 against itself.';
        if (!matrixChart) { matrixChart = echarts.init(matrixDom); }
        matrixChart.resize();
        matrixChart.setOption(option, true);
        setTimeout(() => matrixChart.resize(), 0);
    }

    /**
     * Discard the results and go back to the placeholder.
     *
     * Results belong to the regions they were computed from, so once
     * those are gone the charts are stale. Leaving them up invites
     * reading coefficients that no longer correspond to anything on
     * screen.
     */
    function clearResults() {
        state.result = null;
        state.displayRows = [];
        state.hiddenRegions = new Set();
        resultsBody.style.display = 'none';
        placeholder.style.display = 'block';
        resultsHeader.innerHTML = '';
        voxelCard.style.display = 'none';
        voxelCard.innerHTML = '';
        figuresOut.innerHTML = '';
        figuresBtn.style.display = 'none';
        matrixCaption.innerHTML = '';
        if (chart) { chart.clear(); }
        if (matrixChart) { matrixChart.clear(); }
    }

    /** Render the results header, region toggles and the grid. */
    function renderResults() {
        const result = state.result;
        placeholder.style.display = 'none';
        resultsBody.style.display = 'block';
        figuresBtn.style.display = 'block';

        const misaligned = Object.entries(result.registration || {})
            .filter(([, v]) => v.conclusive && !v.aligned);
        const regPill = Object.keys(result.registration || {}).length === 0
            ? ''
            : (misaligned.length === 0
                ? '<span class="bt-pill good">channel registration ok</span>'
                : '<span class="bt-pill warn" title="' +
                  misaligned.map(([k, v]) =>
                      k + ' dy' + v.dy + ' dx' + v.dx).join(', ') +
                  '">' + misaligned.length +
                  ' pair(s) look shifted &mdash; alpha underestimated</span>');

        const nKept = result.regions.length;
        const nDropped = (result.dropped || []).length;
        const dropPill = nDropped
            ? '<span class="bt-pill" title="' +
              (result.dropped || []).map((d) =>
                  'z' + d.center[0] + ' y' + d.center[1] + ' x' +
                  d.center[2] + '  ' +
                  (d.bright_fraction * 100).toFixed(3) + '%').join('\n') +
              '">' + nDropped + ' dropped as background</span>'
            : '';
        // Few regions is the single biggest source of a wrong answer
        // here, so it is stated on the result, not just before the run.
        const stabilityPill = nKept < MIN_STABLE_REGIONS
            ? '<span class="bt-pill warn">only ' + nKept +
              ' region(s) pooled &mdash; alpha likely overestimated</span>'
            : '<span class="bt-pill good">' + nKept +
              ' regions pooled</span>';

        resultsHeader.innerHTML =
            stabilityPill + dropPill +
            '<span class="bt-pill">L' + result.level + ' &middot; ' +
            (result.block_um || []).join('&times;') + ' &micro;m blocks</span>' +
            '<span class="bt-pill">' +
            result.voxels_per_region.toLocaleString() +
            ' voxels each</span>' +
            '<span class="bt-pill">' +
            (result.has_null ? 'displaced null on' : 'no displaced null') +
            '</span>' + regPill +
            '<span style="font-size:12px;color:#777;">show region:</span>';

        result.regions.forEach((region) => {
            const label = document.createElement('label');
            label.style.cssText = 'font-size:12px;cursor:pointer;';
            const shown = !state.hiddenRegions.has(region.index);
            label.innerHTML = '<input type="checkbox"' +
                (shown ? ' checked' : '') + '> #' + region.index;
            label.querySelector('input').addEventListener('change', (ev) => {
                if (ev.target.checked) {
                    state.hiddenRegions.delete(region.index);
                } else {
                    state.hiddenRegions.add(region.index);
                }
                chart.setOption(buildGridOption(state.result), true);
            });
            resultsHeader.appendChild(label);
        });

        renderMatrix(result);

        // Keep panels roughly square as the channel count grows.
        gridDom.style.height =
            Math.max(520, 165 * result.channels.length) + 'px';
        if (!chart) { chart = echarts.init(gridDom); }
        chart.resize();
        chart.setOption(buildGridOption(result), true);
        chart.off('click');
        chart.on('click', function (params) {
            if (params.componentType !== 'series') { return; }
            if (params.seriesType !== 'scatter') { return; }
            const point = state.displayRows[params.dataIndex];
            if (point) { showVoxel(point); }
        });
        setTimeout(() => chart.resize(), 0);
    }

    /**
     * Show the clicked voxel and offer a Neuroglancer link.
     * @param {number[]} point A row of the shared sample.
     */
    function showVoxel(point) {
        const result = state.result;
        const names = result.channels;
        const values = names.map(
            (n, i) => n + ' ' + point[4 + i].toFixed(2) + 'σ').join('   ');
        voxelCard.style.display = 'block';
        voxelCard.innerHTML =
            '<b>Voxel</b> &mdash; region #' + point[0] +
            ' &middot; global z=' + point[1] + ' y=' + point[2] +
            ' x=' + point[3] +
            '<div class="vals">' + values + '</div>' +
            '<button class="bt-btn" id="bt-ng" style="width:auto;padding:6px 12px;">' +
            'Open in Neuroglancer &#8599;</button>';
        el('bt-ng').addEventListener('click', function () {
            this.disabled = true;
            this.textContent = 'Building link…';
            fetch('/api/bleedthrough/ng-link', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    zarr_paths: result.zarr_paths,
                    position: [point[1], point[2], point[3]]
                })
            })
                .then((r) => r.json())
                .then((data) => {
                    if (data.url) {
                        window.open(data.url, '_blank');
                    } else {
                        toast(data.error || 'Could not build link', 'error');
                    }
                })
                .catch((e) => toast('Link failed: ' + e.message, 'error'))
                .finally(() => {
                    this.disabled = false;
                    this.innerHTML = 'Open in Neuroglancer &#8599;';
                });
        });
    }

    /* ---------------- run ---------------- */

    runBtn.addEventListener('click', function () {
        const voxel = parseFloat(voxelUmInput.value);
        const windowUm = parseFloat(windowUmInput.value);
        runBtn.disabled = true;
        runBtn.textContent = 'Running\u2026';
        showProgress({ stage: 'queued', done: 0, total: 0 });
        figuresOut.innerHTML = '';
        state.hiddenRegions = new Set();

        fetch('/api/bleedthrough/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                base_path: state.basePath,
                channels: Array.from(state.selected),
                regions: state.regions,
                block_size: blockSize(),
                bright_nsigma: parseFloat(brightInput.value) || 3.0,
                voxel_size_um: voxel > 0 ? voxel : null,
                window_um: windowUm > 0 ? windowUm : null,
                n_sample: parseInt(sampleInput.value, 10) || 12000,
                level: state.level,
                min_bright_fraction: parseFloat(gateInput.value) || 0,
                check_registration: true
            })
        })
            .then((r) => r.json().then((d) => ({ ok: r.ok, data: d })))
            .then(({ ok, data }) => {
                if (!ok || !data.job_id) {
                    throw new Error(data.error || 'could not start run');
                }
                return pollRun(data.job_id);
            })
            .then((result) => {
                state.result = result;
                renderResults();
                const dropped = (result.dropped || []).length;
                toast('Pooled ' + result.regions.length + ' region(s)' +
                    (dropped ? ', ' + dropped + ' dropped as background'
                             : ''), 'success');
            })
            .catch((error) => toast('Run failed: ' + error.message, 'error'))
            .finally(() => {
                showProgress(null);
                runBtn.disabled = false;
                updateRegions();
            });
    });

    /**
     * Update or hide the progress bar.
     * @param {object|null} status ``{stage, done, total}``, or null to
     *     hide the bar entirely.
     */
    function showProgress(status) {
        if (!status) {
            progressBox.style.display = 'none';
            return;
        }
        progressBox.style.display = 'block';
        const label = PROGRESS_LABELS[status.stage] || status.stage || '';
        if (status.total > 0) {
            const pct = Math.round((status.done / status.total) * 100);
            barFill.classList.remove('indeterminate');
            barFill.style.width = pct + '%';
            progressText.textContent =
                label + '  ' + status.done + '/' + status.total +
                '  (' + pct + '%)';
        } else {
            // Stages without countable steps: keep the bar moving
            // rather than parking it at a misleading percentage.
            barFill.classList.add('indeterminate');
            progressText.textContent = label + '…';
        }
    }

    /**
     * Poll a run to completion, driving the progress bar.
     * @param {string} jobId Identifier from /run.
     * @returns {Promise<object>} The finished result payload.
     */
    function pollRun(jobId) {
        return new Promise((resolve, reject) => {
            const tick = () => {
                fetch('/api/bleedthrough/run-status?job_id=' +
                    encodeURIComponent(jobId))
                    .then((r) => r.json().then((d) => ({
                        ok: r.ok, data: d
                    })))
                    .then(({ ok, data }) => {
                        if (!ok) {
                            reject(new Error(data.error || 'run failed'));
                            return;
                        }
                        if (data.state === 'done') {
                            showProgress({
                                stage: 'done', done: 1, total: 1
                            });
                            resolve(data.result);
                            return;
                        }
                        showProgress(data);
                        setTimeout(tick, POLL_MS);
                    })
                    .catch(reject);
            };
            tick();
        });
    }

    figuresBtn.addEventListener('click', function () {
        if (!state.result) { return; }
        figuresBtn.disabled = true;
        figuresBtn.textContent = 'Rendering…';
        fetch('/api/bleedthrough/figures', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ run_id: state.result.run_id })
        })
            .then((r) => r.json())
            .then((data) => {
                if (data.error) { toast(data.error, 'error'); return; }
                let html = '<h3 style="font-size:14px;">Density grid</h3>' +
                    '<img src="' + data.density_grid + '">';
                Object.entries(data.composites || {}).forEach(([pair, src]) => {
                    html += '<h3 style="font-size:14px;">' +
                        pair.replace('|', ' &rarr; ') + '</h3>' +
                        '<img src="' + src + '">';
                });
                figuresOut.innerHTML = html;
            })
            .catch((e) => toast('Figures failed: ' + e.message, 'error'))
            .finally(() => {
                figuresBtn.disabled = false;
                figuresBtn.textContent = 'Download figures';
            });
    });

    /* ---------------- wiring ---------------- */

    document.querySelectorAll('#bt-viewer-bar .bt-toggle[data-plane]').forEach((node) => {
        node.addEventListener('click', function () {
            document.querySelectorAll('#bt-viewer-bar .bt-toggle[data-plane]')
                .forEach((n) => n.classList.remove('active'));
            this.classList.add('active');
            state.plane = this.dataset.plane;
            state.slice = Math.floor(
                state.shape0[PLANES[state.plane].fixed] / 2);
            buildSliceSlider();
            refreshView();
        });
    });

    document.querySelectorAll('#bt-sidebar .bt-toggle').forEach((node) => {
        node.addEventListener('click', function () {
            document.querySelectorAll('#bt-sidebar .bt-toggle')
                .forEach((n) => n.classList.remove('active'));
            this.classList.add('active');
            state.sizeMode = this.dataset.mode;
            el('bt-cubic').style.display =
                state.sizeMode === 'cubic' ? 'block' : 'none';
            el('bt-custom').style.display =
                state.sizeMode === 'custom' ? 'block' : 'none';
            updateSizeNote();
        });
    });

    noUiSlider.create(sideSlider, {
        start: [128],
        connect: [true, false],
        step: 16,
        range: { min: 16, max: 256 },
        format: { to: (v) => Math.round(v), from: (v) => Number(v) }
    });
    sideSlider.noUiSlider.on('update', (values) => {
        state.side = parseInt(values[0], 10);
        // A deliberate change redefines the field to preserve; a
        // programmatic one is just the level conversion echoing back.
        if (!state.settingSide && state.levels.length) {
            state.targetFieldUm = state.side * lateralUmAt(state.level);
            state.sideClamped = false;
        }
        updateSizeNote();
    });

    // Contrast window. Percentiles rather than raw counts so one
    // setting behaves sensibly across channels with very different
    // brightness. Non-linear range: almost all the useful adjustment
    // lives in the top couple of percent, where the signal tail is.
    noUiSlider.create(contrastSlider, {
        start: state.contrast,
        connect: true,
        behaviour: 'drag',
        range: { min: 0, '70%': 95, max: 100 },
        format: {
            to: (v) => Math.round(v * 10) / 10,
            from: (v) => Number(v)
        }
    });
    sliderLabel();
    contrastSlider.noUiSlider.on('update', sliderLabel);
    // Windowing is local now, so follow the handle continuously.
    contrastSlider.noUiSlider.on('update', function (values) {
        state.contrast = [parseFloat(values[0]), parseFloat(values[1])];
        showSlice();
    });
    contrastReset.addEventListener('click', function () {
        contrastSlider.noUiSlider.set([0.5, 99.5]);
    });

    /**
     * Reflect the active zoom mode in the Fit/Full toggle.
     * @param {string} mode Either 'fit' or 'full'.
     */
    function setZoomToggle(mode) {
        document.querySelectorAll('[data-zoom]').forEach((node) => {
            node.classList.toggle('active', node.dataset.zoom === mode);
        });
    }

    document.querySelectorAll('[data-zoom]').forEach((node) => {
        node.addEventListener('click', function () {
            setZoomToggle(this.dataset.zoom);
            if (this.dataset.zoom === 'full') {
                setCrop(null);
                state.autoFitDone = true;   // do not re-crop behind them
            } else {
                state.autoFitDone = false;
                setCrop(null);              // reload, then auto-fit again
            }
        });
    });

    /** Write the current contrast window into its readout. */
    function sliderLabel() {
        const v = contrastSlider.noUiSlider.get();
        contrastVal.textContent = v[0] + '–' + v[1] + '%';
    }

    levelSelect.addEventListener('change', function () {
        const previous = state.level;
        state.level = parseInt(this.value, 10) || 0;
        applyLevel(previous);
    });

    /** Keep the grid button's count in step with the inputs. */
    function gridCounts() {
        const c = ['bt-grid-z', 'bt-grid-y', 'bt-grid-x'].map(
            (id) => Math.max(1, parseInt(el(id).value, 10) || 1));
        gridCount.textContent = c[0] * c[1] * c[2];
        return c;
    }
    ['bt-grid-z', 'bt-grid-y', 'bt-grid-x'].forEach(
        (id) => el(id).addEventListener('input', gridCounts));
    gridCounts();

    fillGridBtn.addEventListener('click', function () {
        if (!state.basePath) { return; }
        const counts = gridCounts();
        this.disabled = true;
        this.textContent = 'Placing…';
        fetch('/api/bleedthrough/grid', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                base_path: state.basePath,
                counts: counts,
                level: state.level,
                block_size: blockSize()
            })
        })
            .then((r) => r.json())
            .then((data) => {
                if (data.error) { toast(data.error, 'error'); return; }
                state.regions = data.regions;
                updateRegions();
                drawPins();
                toast('Placed ' + data.count + ' regions. Ones without ' +
                    'signal are dropped at run time.', 'success');
            })
            .catch((e) => toast('Grid failed: ' + e.message, 'error'))
            .finally(() => {
                this.disabled = false;
                this.innerHTML = 'Auto-place grid (<span id="bt-grid-count">' +
                    gridCount.textContent + '</span>)';
            });
    });

    viewChannel.addEventListener('change', refreshView);
    clearRegionsBtn.addEventListener('click', function () {
        state.regions = [];
        clearResults();
        updateRegions();
        drawPins();
    });
    helpBtn.addEventListener('click', function () {
        const open = helpBox.style.display !== 'none';
        helpBox.style.display = open ? 'none' : 'block';
        this.classList.toggle('on', !open);
        if (!open) { renderHelp(); }
    });
    [voxelUmInput, windowUmInput].forEach(
        (node) => node.addEventListener('input', function () {
            updateNullNote();
            if (helpBox.style.display !== 'none') { renderHelp(); }
        }));
    ['bt-size-z', 'bt-size-y', 'bt-size-x'].forEach(
        (id) => el(id).addEventListener('input', updateSizeNote));

    fetch('/api/bleedthrough/dataset-paths')
        .then((r) => r.json())
        .then((data) => {
            const options = ['<option value="">— pick a cached dataset —</option>'];
            (data.datasets || []).forEach((d) => {
                options.push('<option value="' + d.base_path + '">' +
                    d.dataset + '</option>');
            });
            datasetSelect.innerHTML = options.join('');
        })
        .catch(() => {
            datasetSelect.innerHTML = '<option value="">(none cached)</option>';
        });

    updateRegions();
    updateNullNote();
});
