/*
 * Data import for the ZunZunSite3 text data editor: CSV/text files, Excel workbooks, and
 * digitizing a 2D graph image. Adapted from tkinterFit's DataImport.py and ImportDataDialog.py
 * (https://github.com/rossSauce/Tkinterfit).
 *
 * Everything happens in the visitor's browser. Files are never sent to the server: the
 * result is plain text placed in the existing text data editor, which the server validates
 * exactly as if it had been pasted. Text taken from files is only ever inserted with
 * textContent or form values, never as HTML.
 *
 * Excel support loads SheetJS (xlsx-0.20.3.full.min.js, Apache 2.0, from sheetjs.com, not
 * the outdated npm copy) only when a workbook is opened.
 */
(function () {
    'use strict';

    var SHEETJS_URL = '/temp/static_images/xlsx-0.20.3.full.min.js';
    var MAX_FILE_BYTES = 20 * 1024 * 1024;
    var MAX_TEXT_BYTES = 2400000;          // Django rejects form posts over 2.5 MB
    var MAX_IMAGE_PIXELS = 40 * 1000 * 1000;
    var PREVIEW_ROWS = 12;

    // ---------- small helpers ----------

    function el(tag, attributes, text) {
        var e = document.createElement(tag);
        if (attributes) {
            Object.keys(attributes).forEach(function (k) { e.setAttribute(k, attributes[k]); });
        }
        if (text !== undefined) { e.textContent = text; }
        return e;
    }

    function toFloat(cell, commaIsDecimal) {
        if (cell === null || cell === undefined || typeof cell === 'boolean') { return null; }
        var value;
        if (typeof cell === 'number') {
            value = cell;
        } else {
            var text = String(cell).trim().replace(/−/g, '-'); // unicode minus sign
            if (text === '') { return null; }
            if (commaIsDecimal) { text = text.replace(',', '.'); }
            if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(text)) { return null; }
            value = parseFloat(text);
        }
        return isFinite(value) ? value : null;
    }

    function columnLetter(index) {
        var letters = '';
        index += 1;
        while (index > 0) {
            var remainder = (index - 1) % 26;
            letters = String.fromCharCode(65 + remainder) + letters;
            index = Math.floor((index - 1) / 26);
        }
        return letters;
    }

    function formatNumber(v) {
        // full precision without trailing noise
        var s = String(v);
        if (s.length > 17) { s = v.toPrecision(15).replace(/\.?0+(e|$)/, '$1'); }
        return s;
    }

    // ---------- reading delimited text ----------

    function decodeText(buffer) {
        var bytes = new Uint8Array(buffer);
        try {
            return new TextDecoder('utf-8', { fatal: true }).decode(bytes).replace(/^﻿/, '');
        } catch (e) {
            return new TextDecoder('windows-1252').decode(bytes);
        }
    }

    function guessDelimiter(lines) {
        var candidates = ['\t', ';', ',', '|'];
        var best = null, bestScore = 0;
        candidates.forEach(function (d) {
            var counts = lines.filter(function (l) { return l.trim() !== ''; }).slice(0, 50)
                .map(function (l) { return l.split(d).length - 1; });
            if (!counts.length) { return; }
            var first = counts[0];
            var consistent = counts.filter(function (c) { return c === first && c > 0; }).length;
            if (consistent > bestScore) { best = d; bestScore = consistent; }
        });
        return best; // null means whitespace separated
    }

    function parseCSVLine(line, delimiter) {
        var cells = [], cell = '', quoted = false;
        for (var i = 0; i < line.length; i++) {
            var c = line[i];
            if (quoted) {
                if (c === '"' && line[i + 1] === '"') { cell += '"'; i++; }
                else if (c === '"') { quoted = false; }
                else { cell += c; }
            } else if (c === '"') {
                quoted = true;
            } else if (c === delimiter) {
                cells.push(cell.trim()); cell = '';
            } else {
                cell += c;
            }
        }
        cells.push(cell.trim());
        return cells;
    }

    function readDelimitedText(buffer) {
        var lines = decodeText(buffer).split(/\r\n|\r|\n/);
        var delimiter = guessDelimiter(lines);
        var rows = lines.map(function (line) {
            if (delimiter === null) { return line.trim() === '' ? [] : line.trim().split(/\s+/); }
            return parseCSVLine(line, delimiter);
        });
        // "1,5;2,7" style files: semicolon separated with decimal commas
        return { rows: rows, commaIsDecimal: delimiter === ';' };
    }

    // ---------- reading workbooks ----------

    var sheetJSPromise = null;
    function loadSheetJS() {
        if (window.XLSX) { return Promise.resolve(window.XLSX); }
        if (!sheetJSPromise) {
            sheetJSPromise = new Promise(function (resolve, reject) {
                var script = el('script', { src: SHEETJS_URL });
                script.onload = function () { resolve(window.XLSX); };
                script.onerror = function () { sheetJSPromise = null; reject(new Error('could not load the spreadsheet reader')); };
                document.head.appendChild(script);
            });
        }
        return sheetJSPromise;
    }

    function readWorkbook(buffer) {
        return loadSheetJS().then(function (XLSX) {
            // cellFormula false: use the last calculated values, never formulas
            var workbook = XLSX.read(new Uint8Array(buffer), { type: 'array', cellFormula: false, cellHTML: false, dense: true });
            var tables = {};
            workbook.SheetNames.forEach(function (name) {
                tables[name] = XLSX.utils.sheet_to_json(workbook.Sheets[name], { header: 1, raw: true, defval: null, blankrows: true });
            });
            return { sheetNames: workbook.SheetNames.slice(), tables: tables };
        });
    }

    // ---------- table analysis, as in DataImport.py ----------

    function columnCount(rows) {
        return rows.reduce(function (m, r) { return Math.max(m, r.length); }, 0);
    }

    function looksLikeHeader(rows, commaIsDecimal) {
        if (rows.length < 2) { return false; }
        var first = rows[0];
        for (var r = 1; r < Math.min(rows.length, 20); r++) {
            for (var i = 0; i < rows[r].length; i++) {
                if (toFloat(rows[r][i], commaIsDecimal) !== null && i < first.length &&
                    first[i] !== null && first[i] !== '' && toFloat(first[i], commaIsDecimal) === null) {
                    return true;
                }
            }
        }
        return false;
    }

    function numericColumns(rows, header, commaIsDecimal) {
        var result = [], start = header ? 1 : 0, count = columnCount(rows);
        for (var i = 0; i < count; i++) {
            for (var r = start; r < rows.length; r++) {
                if (i < rows[r].length && toFloat(rows[r][i], commaIsDecimal) !== null) { result.push(i); break; }
            }
        }
        return result;
    }

    function columnName(rows, header, i) {
        var name = columnLetter(i);
        if (header && rows.length && i < rows[0].length && rows[0][i] !== null && rows[0][i] !== '') {
            name += ': ' + String(rows[0][i]);
        }
        return name;
    }

    function extractPoints(rows, indices, header, commaIsDecimal) {
        var points = [], skipped = 0;
        for (var r = header ? 1 : 0; r < rows.length; r++) {
            var row = rows[r], values = [], bad = false;
            for (var k = 0; k < indices.length; k++) {
                var v = indices[k] < row.length ? toFloat(row[indices[k]], commaIsDecimal) : null;
                if (v === null) { bad = true; break; }
                values.push(v);
            }
            if (bad) {
                if (row.some(function (c) { return c !== null && c !== ''; })) { skipped++; }
                continue;
            }
            points.push(values);
        }
        return { points: points, skipped: skipped };
    }

    function dataText(points, names, description) {
        // the fitting code ignores lines that are not all numbers
        var lines = [];
        if (description) { lines.push(description); }
        lines.push('');
        lines.push(names.join('   '));
        points.forEach(function (p) { lines.push(p.map(formatNumber).join(' ')); });
        return lines.join('\n') + '\n';
    }

    // ---------- the panel ----------

    function DataImporter(container, form, dim, weightedPossible) {
        this.container = container;
        this.form = form;
        this.dim = dim;
        this.weightedPossible = weightedPossible;
        this.status = el('div', { 'class': 'zzImportStatus' });
        this.build();
    }

    DataImporter.prototype.setStatus = function (message, isError) {
        this.status.textContent = message;
        this.status.style.color = isError ? '#b00000' : '';
    };

    DataImporter.prototype.axisNames = function () {
        return ['X', 'Y', 'Z'].slice(0, this.dim);
    };

    DataImporter.prototype.weightsWanted = function () {
        var on = this.form.querySelector('input[name="weightedFittingChoice"][value="ON"]');
        return this.weightedPossible && on && on.checked;
    };

    DataImporter.prototype.build = function () {
        var self = this;
        var c = this.container;

        var fileRow = el('div');
        fileRow.appendChild(el('span', null, 'Spreadsheet or text file (.csv .txt .tsv .dat .xlsx .xls .ods): '));
        this.fileInput = el('input', { type: 'file', accept: '.csv,.txt,.tsv,.dat,.xlsx,.xlsm,.xls,.ods' });
        this.fileInput.addEventListener('change', function () { self.openSpreadsheet(); });
        fileRow.appendChild(this.fileInput);
        c.appendChild(fileRow);

        if (this.dim === 2) {
            var imageRow = el('div');
            imageRow.style.marginTop = '0.6em';
            imageRow.appendChild(el('span', null, 'Or digitize a graph image (.png .jpg .gif .bmp .webp .svg): '));
            this.imageInput = el('input', { type: 'file', accept: '.png,.jpg,.jpeg,.gif,.bmp,.webp,.svg,image/png,image/jpeg,image/gif,image/bmp,image/webp,image/svg+xml' });
            this.imageInput.addEventListener('change', function () { self.openImage(); });
            imageRow.appendChild(this.imageInput);
            c.appendChild(imageRow);
        }

        c.appendChild(el('div', { 'class': 'zzImportNote' },
            'Files are read by your browser and are not uploaded; the numbers are placed in the text data editor below.'));
        c.appendChild(this.status);

        this.sheetPanel = el('div');
        this.sheetPanel.style.display = 'none';
        c.appendChild(this.sheetPanel);
        this.imagePanel = el('div');
        this.imagePanel.style.display = 'none';
        c.appendChild(this.imagePanel);
    };

    DataImporter.prototype.readFile = function (file) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () { resolve(reader.result); };
            reader.onerror = function () { reject(new Error('could not read the file')); };
            reader.readAsArrayBuffer(file);
        });
    };

    // ----- spreadsheets -----

    DataImporter.prototype.openSpreadsheet = function () {
        var self = this;
        var file = this.fileInput.files[0];
        if (!file) { return; }
        this.imagePanel.style.display = 'none';
        if (file.size > MAX_FILE_BYTES) {
            this.setStatus('That file is larger than 20 MB, which is more data than the site can fit.', true);
            return;
        }
        this.setStatus('Reading ' + file.name + ' ...');
        var isWorkbook = /\.(xlsx|xlsm|xls|ods)$/i.test(file.name);
        this.readFile(file).then(function (buffer) {
            if (isWorkbook) { return readWorkbook(buffer); }
            var text = readDelimitedText(buffer);
            var tables = {};
            tables[file.name] = text.rows;
            return { sheetNames: [file.name], tables: tables, commaIsDecimal: text.commaIsDecimal };
        }).then(function (book) {
            self.book = book;
            self.fileName = file.name;
            self.showSheetPanel();
            self.setStatus('');
        }).catch(function (e) {
            self.setStatus('Could not read ' + file.name + ': ' + e.message, true);
        });
    };

    DataImporter.prototype.showSheetPanel = function () {
        var self = this, p = this.sheetPanel;
        p.textContent = '';
        p.style.display = 'block';

        var controls = el('table', { border: '1', cellpadding: '6' });
        var tr = el('tr');
        controls.appendChild(tr);
        var td = el('td', { align: 'left' });
        tr.appendChild(td);

        if (this.book.sheetNames.length > 1) {
            td.appendChild(el('span', null, 'Sheet: '));
            this.sheetSelect = el('select');
            this.book.sheetNames.forEach(function (n) { self.sheetSelect.appendChild(el('option', { value: n }, n)); });
            this.sheetSelect.addEventListener('change', function () { self.sheetChanged(true); });
            td.appendChild(this.sheetSelect);
            td.appendChild(el('br'));
        } else {
            this.sheetSelect = null;
        }

        this.headerCheck = el('input', { type: 'checkbox' });
        this.headerCheck.addEventListener('change', function () { self.sheetChanged(false); });
        var label = el('label');
        label.appendChild(this.headerCheck);
        label.appendChild(document.createTextNode(' First row contains column names'));
        td.appendChild(label);
        td.appendChild(el('br'));

        this.columnSelects = {};
        var names = this.axisNames().concat(this.weightedPossible ? ['Weight'] : []);
        names.forEach(function (n) {
            td.appendChild(el('span', null, (n === 'Weight' ? 'Weight column (used when weighted fitting is on): ' : n + ' column: ')));
            var s = el('select');
            s.addEventListener('change', function () { self.updateSummary(); });
            self.columnSelects[n] = s;
            td.appendChild(s);
            td.appendChild(el('br'));
        });

        var button = el('input', { type: 'button', value: 'Put this data in the text editor' });
        button.addEventListener('click', function () { self.useSheetData(); });
        td.appendChild(el('br'));
        td.appendChild(button);
        this.summary = el('div');
        td.appendChild(this.summary);

        p.appendChild(el('br'));
        p.appendChild(controls);
        p.appendChild(el('br'));
        this.preview = el('div');
        this.preview.style.overflow = 'auto';
        this.preview.style.maxHeight = '22em';
        this.preview.style.maxWidth = '100%';
        p.appendChild(this.preview);
        this.sheetChanged(true);
    };

    DataImporter.prototype.currentRows = function () {
        var name = this.sheetSelect ? this.sheetSelect.value : this.book.sheetNames[0];
        return this.book.tables[name] || [];
    };

    DataImporter.prototype.sheetChanged = function (detectHeader) {
        var self = this, rows = this.currentRows(), comma = !!this.book.commaIsDecimal;
        if (detectHeader) { this.headerCheck.checked = looksLikeHeader(rows, comma); }
        var header = this.headerCheck.checked;
        var numeric = numericColumns(rows, header, comma);
        var names = Object.keys(this.columnSelects);
        names.forEach(function (n, k) {
            var s = self.columnSelects[n];
            var previous = s.value;
            s.textContent = '';
            if (n === 'Weight') { s.appendChild(el('option', { value: '' }, '(none)')); }
            numeric.forEach(function (i) { s.appendChild(el('option', { value: String(i) }, columnName(rows, header, i))); });
            if (detectHeader || previous === '') {
                // default to the first numeric columns in order; weights to the next one
                var choice = numeric[k];
                s.value = (choice === undefined || (n === 'Weight' && !self.weightsWanted())) ? (n === 'Weight' ? '' : String(numeric[0])) : String(choice);
            } else {
                s.value = previous;
            }
        });
        this.showPreview(rows, header);
        this.updateSummary();
    };

    DataImporter.prototype.showPreview = function (rows, header) {
        var table = el('table', { border: '1', cellpadding: '3' });
        var count = Math.min(columnCount(rows), 30);
        var head = el('tr');
        head.appendChild(el('th', null, ''));
        for (var i = 0; i < count; i++) { head.appendChild(el('th', null, columnLetter(i))); }
        table.appendChild(head);
        for (var r = 0; r < Math.min(rows.length, PREVIEW_ROWS); r++) {
            var tr = el('tr');
            tr.appendChild(el('td', null, String(r + 1)));
            for (var j = 0; j < count; j++) {
                var cell = j < rows[r].length && rows[r][j] !== null ? String(rows[r][j]) : '';
                var td = el('td', null, cell.length > 40 ? cell.slice(0, 40) + '...' : cell);
                if (header && r === 0) { td.style.fontWeight = 'bold'; }
                tr.appendChild(td);
            }
            table.appendChild(tr);
        }
        this.preview.textContent = '';
        this.preview.appendChild(el('div', null, 'First rows of the file:'));
        this.preview.appendChild(table);
        if (rows.length > PREVIEW_ROWS) {
            this.preview.appendChild(el('div', null, '... ' + (rows.length - PREVIEW_ROWS) + ' more rows'));
        }
    };

    DataImporter.prototype.selectedColumns = function () {
        var self = this, indices = [], names = [];
        this.axisNames().forEach(function (n) {
            indices.push(parseInt(self.columnSelects[n].value, 10));
            names.push(n);
        });
        var w = this.columnSelects.Weight;
        if (w && w.value !== '' && this.weightsWanted()) {
            indices.push(parseInt(w.value, 10));
            names.push('Weight');
        }
        return { indices: indices, names: names };
    };

    DataImporter.prototype.updateSummary = function () {
        var columns = this.selectedColumns();
        if (columns.indices.some(isNaN)) {
            this.summary.textContent = 'This sheet does not have enough columns of numbers.';
            return null;
        }
        var result = extractPoints(this.currentRows(), columns.indices, this.headerCheck.checked, !!this.book.commaIsDecimal);
        var text = result.points.length + ' rows of data';
        if (result.skipped) { text += ', ' + result.skipped + ' rows skipped because a chosen column is not a number'; }
        if (this.columnSelects.Weight && this.columnSelects.Weight.value !== '' && !this.weightsWanted()) {
            text += '. The weight column is ignored because weighted fitting is off.';
        }
        this.summary.textContent = text;
        return result;
    };

    DataImporter.prototype.useSheetData = function () {
        var columns = this.selectedColumns();
        var result = this.updateSummary();
        if (!result) { return; }
        if (result.points.length < 2) {
            this.setStatus('There are fewer than two rows of numbers in the chosen columns.', true);
            return;
        }
        var rows = this.currentRows(), header = this.headerCheck.checked, self = this;
        var description = 'Imported from ' + this.fileName + (this.sheetSelect ? ', sheet ' + this.sheetSelect.value : '');
        this.putText(dataText(result.points, columns.names, description));
        // carry column names over to the graph labels
        if (header) {
            var fieldNames = { X: 'dataNameX', Y: 'dataNameY', Z: 'dataNameZ' };
            this.axisNames().forEach(function (n) {
                var field = self.form.elements[fieldNames[n]];
                var i = parseInt(self.columnSelects[n].value, 10);
                if (field && rows[0] && rows[0][i] !== null && rows[0][i] !== undefined && String(rows[0][i]).trim() !== '') {
                    field.value = String(rows[0][i]).trim().slice(0, 60);
                }
            });
        }
    };

    DataImporter.prototype.putText = function (text) {
        if (text.length > MAX_TEXT_BYTES) {
            this.setStatus('That is too much data for one fit (the limit is about 2.4 MB of text). Please use fewer rows.', true);
            return;
        }
        var editor = this.form.elements.textDataEditor;
        editor.value = text;
        // the site's default comma handling would turn decimal commas into separators; the
        // imported text has none, so this choice is safe either way
        this.setStatus('The data is now in the text data editor below.');
    };

    // ----- graph image digitizing (2D) -----

    DataImporter.prototype.openImage = function () {
        var self = this;
        var file = this.imageInput.files[0];
        if (!file) { return; }
        this.sheetPanel.style.display = 'none';
        if (file.size > MAX_FILE_BYTES) {
            this.setStatus('That image is larger than 20 MB.', true);
            return;
        }
        if (this.imageURL) { URL.revokeObjectURL(this.imageURL); }
        // An <img> never runs scripts inside an SVG file, so SVGs are safe to show this way.
        this.imageURL = URL.createObjectURL(file);
        var image = new Image();
        image.onload = function () {
            var w = image.naturalWidth || 1000, h = image.naturalHeight || 800;
            if (w * h > MAX_IMAGE_PIXELS) {
                self.setStatus('That image is too large to digitize; please use a smaller one.', true);
                return;
            }
            self.loadImageIntoCanvas(image, w, h);
            self.setStatus('');
        };
        image.onerror = function () { self.setStatus('Your browser could not open that image.', true); };
        image.src = this.imageURL;
    };

    DataImporter.prototype.loadImageIntoCanvas = function (image, w, h) {
        // an offscreen copy holds the image pixels for colour picking and tracing
        this.pixels = el('canvas');
        this.pixels.width = w;
        this.pixels.height = h;
        var ctx = this.pixels.getContext('2d', { willReadFrequently: true });
        ctx.fillStyle = '#ffffff'; // flatten transparency onto white
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(image, 0, 0, w, h);
        this.imageData = ctx.getImageData(0, 0, w, h).data;
        this.calibration = { X1: null, X2: null, Y1: null, Y2: null };
        this.points = [];
        this.traceArea = null;
        this.curveColor = null;
        this.mode = 'add';
        this.showImagePanel();
    };

    DataImporter.prototype.showImagePanel = function () {
        var self = this, p = this.imagePanel;
        p.textContent = '';
        p.style.display = 'block';

        var steps = el('ol');
        steps.style.textAlign = 'left';
        steps.style.maxWidth = '50em';
        [
            'Enter two known values on each axis, then use each "Place" button and click that value\'s position on the axis.',
            'Click "Add points" and click on each data point; shift-click (or use "Delete points") removes the nearest point.',
            'Or trace a curve automatically: "Pick curve colour" and click on the curve, optionally drag a box around it with "Trace area", then "Trace curve".',
            'Click "Put points in the text editor".'
        ].forEach(function (t) { steps.appendChild(el('li', null, t)); });
        p.appendChild(steps);

        var table = el('table', { border: '1', cellpadding: '6' });
        p.appendChild(table);
        var tr = el('tr');
        table.appendChild(tr);
        var td = el('td', { align: 'left' });
        tr.appendChild(td);

        this.calibrationInputs = {};
        this.logChecks = {};
        ['X', 'Y'].forEach(function (axis) {
            td.appendChild(el('b', null, axis + ' axis: '));
            [1, 2].forEach(function (n) {
                var key = axis + n;
                td.appendChild(el('span', null, ' value ' + n + ' '));
                var input = el('input', { type: 'text', size: '9' });
                self.calibrationInputs[key] = input;
                td.appendChild(input);
                var place = el('input', { type: 'button', value: 'Place ' + key });
                place.addEventListener('click', function () { self.setMode('place' + key); });
                td.appendChild(place);
            });
            var log = el('input', { type: 'checkbox' });
            self.logChecks[axis] = log;
            var label = el('label');
            label.appendChild(document.createTextNode(' '));
            label.appendChild(log);
            label.appendChild(document.createTextNode(' log scale'));
            td.appendChild(label);
            td.appendChild(el('br'));
        });

        td.appendChild(el('br'));
        var buttons = [
            ['Add points', function () { self.setMode('add'); }],
            ['Delete points', function () { self.setMode('delete'); }],
            ['Pick curve colour', function () { self.setMode('color'); }],
            ['Trace area', function () { self.setMode('area'); }],
            ['Trace curve', function () { self.trace(); }],
            ['Clear points', function () { self.points = []; self.redraw(); self.updatePointCount(); }]
        ];
        buttons.forEach(function (b) {
            var button = el('input', { type: 'button', value: b[0] });
            button.addEventListener('click', b[1]);
            td.appendChild(button);
            td.appendChild(document.createTextNode(' '));
        });
        td.appendChild(el('br'));
        td.appendChild(el('span', null, 'Colour tolerance '));
        this.toleranceInput = el('input', { type: 'range', min: '10', max: '200', value: '60' });
        td.appendChild(this.toleranceInput);
        td.appendChild(el('span', null, ' point spacing (pixels) '));
        this.spacingInput = el('input', { type: 'text', size: '3', value: '5' });
        td.appendChild(this.spacingInput);
        this.colorSwatch = el('span', null, '  ');
        this.colorSwatch.style.display = 'inline-block';
        this.colorSwatch.style.width = '1.5em';
        this.colorSwatch.style.border = '1px solid #000';
        this.colorSwatch.style.marginLeft = '0.5em';
        td.appendChild(this.colorSwatch);
        td.appendChild(el('br'));
        td.appendChild(el('br'));
        var useButton = el('input', { type: 'button', value: 'Put points in the text editor' });
        useButton.addEventListener('click', function () { self.useImagePoints(); });
        td.appendChild(useButton);
        this.modeLabel = el('div');
        td.appendChild(this.modeLabel);
        this.pointCount = el('div');
        td.appendChild(this.pointCount);

        // visible canvas, scaled to fit the page; all positions are kept in image pixels
        this.canvas = el('canvas');
        var maxWidth = Math.min(1000, Math.max(300, (this.container.clientWidth || 900) - 20));
        this.scale = Math.min(1.0, maxWidth / this.pixels.width);
        this.canvas.width = Math.round(this.pixels.width * this.scale);
        this.canvas.height = Math.round(this.pixels.height * this.scale);
        this.canvas.style.border = '1px solid #888';
        this.canvas.style.cursor = 'crosshair';
        this.canvas.style.marginTop = '0.6em';
        p.appendChild(el('br'));
        p.appendChild(this.canvas);

        this.canvas.addEventListener('mousedown', function (e) { self.onPress(e); });
        this.canvas.addEventListener('mousemove', function (e) { self.onDrag(e); });
        window.addEventListener('mouseup', function (e) { self.onRelease(e); });
        this.setMode('add');
        this.redraw();
        this.updatePointCount();
    };

    DataImporter.prototype.imagePosition = function (e) {
        var rect = this.canvas.getBoundingClientRect();
        var cx = (e.clientX - rect.left) * (this.canvas.width / rect.width);
        var cy = (e.clientY - rect.top) * (this.canvas.height / rect.height);
        return [cx / this.scale, cy / this.scale];
    };

    DataImporter.prototype.setMode = function (mode) {
        this.mode = mode;
        var text = {
            add: 'Click on data points to add them (shift-click removes the nearest).',
            'delete': 'Click near a point to delete it.',
            color: 'Click on the curve to pick its colour.',
            area: 'Drag a box around the curve to limit tracing to it.'
        }[mode];
        if (!text && mode.indexOf('place') === 0) { text = 'Click on the axis where value ' + mode.slice(5) + ' is.'; }
        this.modeLabel.textContent = text || '';
    };

    DataImporter.prototype.onPress = function (e) {
        var pos = this.imagePosition(e);
        e.preventDefault();
        if (this.mode.indexOf('place') === 0) {
            this.calibration[this.mode.slice(5)] = pos;
            this.setMode('add');
        } else if (this.mode === 'delete' || (this.mode === 'add' && e.shiftKey)) {
            this.deleteNearest(pos);
        } else if (this.mode === 'add') {
            this.points.push(pos);
        } else if (this.mode === 'color') {
            this.curveColor = this.pickColor(pos);
            this.colorSwatch.style.background = 'rgb(' + this.curveColor.join(',') + ')';
            this.setMode('add');
        } else if (this.mode === 'area') {
            this.dragStart = pos;
            this.traceArea = [pos[0], pos[1], pos[0], pos[1]];
        }
        this.redraw();
        this.updatePointCount();
    };

    DataImporter.prototype.onDrag = function (e) {
        if (this.mode === 'area' && this.dragStart) {
            var pos = this.imagePosition(e);
            this.traceArea = [this.dragStart[0], this.dragStart[1], pos[0], pos[1]];
            this.redraw();
        }
    };

    DataImporter.prototype.onRelease = function () {
        if (this.mode === 'area' && this.dragStart) {
            this.dragStart = null;
            this.setMode('add');
        }
    };

    DataImporter.prototype.deleteNearest = function (pos) {
        var best = -1, bestDistance = 15 / this.scale; // within 15 screen pixels
        this.points.forEach(function (p, i) {
            var d = Math.hypot(p[0] - pos[0], p[1] - pos[1]);
            if (d < bestDistance) { best = i; bestDistance = d; }
        });
        if (best >= 0) { this.points.splice(best, 1); }
    };

    DataImporter.prototype.pixel = function (x, y) {
        var i = (y * this.pixels.width + x) * 4, d = this.imageData;
        return [d[i], d[i + 1], d[i + 2]];
    };

    DataImporter.prototype.pickColor = function (pos) {
        // lines are often only a pixel or two wide: take the most colourful pixel near the
        // click, or the darkest one for black and grey curves (as tkinterFit does)
        var radius = 5, best = null, bestSaturation = -1, darkest = null, darkestSum = 1e9;
        for (var y = Math.max(0, Math.round(pos[1]) - radius); y <= Math.min(this.pixels.height - 1, Math.round(pos[1]) + radius); y++) {
            for (var x = Math.max(0, Math.round(pos[0]) - radius); x <= Math.min(this.pixels.width - 1, Math.round(pos[0]) + radius); x++) {
                var c = this.pixel(x, y);
                var saturation = Math.max(c[0], c[1], c[2]) - Math.min(c[0], c[1], c[2]);
                if (saturation > bestSaturation) { best = c; bestSaturation = saturation; }
                var sum = c[0] + c[1] + c[2];
                if (sum < darkestSum) { darkest = c; darkestSum = sum; }
            }
        }
        return bestSaturation >= 60 ? best : darkest;
    };

    DataImporter.prototype.trace = function () {
        if (!this.curveColor) {
            this.setStatus('Use "Pick curve colour" first and click on the curve.', true);
            return;
        }
        var w = this.pixels.width, h = this.pixels.height;
        var left = 0, top = 0, right = w - 1, bottom = h - 1;
        if (this.traceArea) {
            left = Math.max(0, Math.floor(Math.min(this.traceArea[0], this.traceArea[2])));
            right = Math.min(w - 1, Math.ceil(Math.max(this.traceArea[0], this.traceArea[2])));
            top = Math.max(0, Math.floor(Math.min(this.traceArea[1], this.traceArea[3])));
            bottom = Math.min(h - 1, Math.ceil(Math.max(this.traceArea[1], this.traceArea[3])));
        }
        var tolerance = parseFloat(this.toleranceInput.value), t2 = tolerance * tolerance;
        var spacing = Math.max(1, parseInt(this.spacingInput.value, 10) || 5);
        var target = this.curveColor, points = [];
        for (var x = left; x <= right; x += spacing) {
            // in each sampled column, each run of matching pixels gives a point at its centre;
            // long vertical runs (steep parts of the curve) give a point every 'spacing' pixels
            var runStart = -1;
            for (var y = top; y <= bottom + 1; y++) {
                var match = false;
                if (y <= bottom) {
                    var c = this.pixel(x, y);
                    var d = (c[0] - target[0]) * (c[0] - target[0]) + (c[1] - target[1]) * (c[1] - target[1]) + (c[2] - target[2]) * (c[2] - target[2]);
                    match = d <= t2;
                }
                if (match && runStart < 0) { runStart = y; }
                if (!match && runStart >= 0) {
                    var start = runStart, end = y - 1, length = end - start + 1;
                    if (length <= spacing * 2) {
                        points.push([x, (start + end) / 2]);
                    } else {
                        var count = Math.floor(length / spacing);
                        for (var k = 0; k < count; k++) { points.push([x, start + k * (end - start) / (count - 1)]); }
                    }
                    runStart = -1;
                }
            }
        }
        if (points.length > 20000) {
            this.setStatus('The trace matched far too many pixels; pick the colour again, lower the tolerance, or draw a smaller trace area.', true);
            return;
        }
        this.points = this.points.concat(points);
        this.setStatus(points.length ? 'Traced ' + points.length + ' points.' : 'No pixels of that colour were found; try a higher tolerance.', !points.length);
        this.redraw();
        this.updatePointCount();
    };

    DataImporter.prototype.axisCalibration = function (axis) {
        var v1 = toFloat(this.calibrationInputs[axis + '1'].value, false);
        var v2 = toFloat(this.calibrationInputs[axis + '2'].value, false);
        var p1 = this.calibration[axis + '1'], p2 = this.calibration[axis + '2'];
        var log = this.logChecks[axis].checked;
        var coordinate = axis === 'X' ? 0 : 1;
        if (!p1 || !p2) { return { error: 'place both ' + axis + ' reference points on the image' }; }
        if (v1 === null || v2 === null) { return { error: 'enter both ' + axis + ' reference values as numbers' }; }
        if (Math.abs(p1[coordinate] - p2[coordinate]) < 1) { return { error: 'the two ' + axis + ' reference points must be further apart' }; }
        if (v1 === v2) { return { error: 'the two ' + axis + ' reference values must be different' }; }
        if (log && (v1 <= 0 || v2 <= 0)) { return { error: axis + ' reference values must be greater than zero for a log scale' }; }
        return {
            toValue: function (pos) {
                var fraction = (pos[coordinate] - p1[coordinate]) / (p2[coordinate] - p1[coordinate]);
                if (log) {
                    var l1 = Math.log10(v1), l2 = Math.log10(v2);
                    return Math.pow(10, l1 + fraction * (l2 - l1));
                }
                return v1 + fraction * (v2 - v1);
            }
        };
    };

    DataImporter.prototype.useImagePoints = function () {
        var x = this.axisCalibration('X'), y = this.axisCalibration('Y');
        if (x.error || y.error) {
            this.setStatus('Calibration: ' + (x.error || y.error) + '.', true);
            return;
        }
        if (this.points.length < 2) {
            this.setStatus('Add at least two points first.', true);
            return;
        }
        var data = this.points.map(function (p) { return [x.toValue(p), y.toValue(p)]; })
            .filter(function (p) { return isFinite(p[0]) && isFinite(p[1]); });
        data.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
        this.putText(dataText(data, ['X', 'Y'], 'Digitized from ' + this.imageInput.files[0].name));
    };

    DataImporter.prototype.updatePointCount = function () {
        if (this.pointCount) { this.pointCount.textContent = this.points.length + ' points'; }
    };

    DataImporter.prototype.redraw = function () {
        var ctx = this.canvas.getContext('2d'), s = this.scale;
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.drawImage(this.pixels, 0, 0, this.canvas.width, this.canvas.height);
        if (this.traceArea) {
            ctx.strokeStyle = '#0060ff';
            ctx.setLineDash([5, 4]);
            ctx.strokeRect(Math.min(this.traceArea[0], this.traceArea[2]) * s, Math.min(this.traceArea[1], this.traceArea[3]) * s,
                Math.abs(this.traceArea[2] - this.traceArea[0]) * s, Math.abs(this.traceArea[3] - this.traceArea[1]) * s);
            ctx.setLineDash([]);
        }
        var self = this;
        Object.keys(this.calibration).forEach(function (key) {
            var p = self.calibration[key];
            if (!p) { return; }
            ctx.strokeStyle = key[0] === 'X' ? '#d00000' : '#008000';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(p[0] * s - 8, p[1] * s); ctx.lineTo(p[0] * s + 8, p[1] * s);
            ctx.moveTo(p[0] * s, p[1] * s - 8); ctx.lineTo(p[0] * s, p[1] * s + 8);
            ctx.stroke();
            ctx.fillStyle = ctx.strokeStyle;
            ctx.font = '12px sans-serif';
            ctx.fillText(key, p[0] * s + 5, p[1] * s - 5);
        });
        ctx.lineWidth = 1;
        ctx.strokeStyle = '#ff00ff';
        this.points.forEach(function (p) {
            ctx.beginPath();
            ctx.arc(p[0] * s, p[1] * s, 3, 0, 2 * Math.PI);
            ctx.stroke();
        });
    };

    // ---------- start-up ----------

    function init() {
        var containers = document.querySelectorAll('[data-zz-import]');
        Array.prototype.forEach.call(containers, function (container) {
            var form = container.closest('form');
            if (!form || !form.elements.textDataEditor) { return; }
            var dim = parseInt(container.getAttribute('data-zz-import'), 10) || 2;
            new DataImporter(container, form, dim, container.getAttribute('data-zz-weighted') === '1');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // exposed for testing in a browser console or a test page
    window.ZunZunDataImport = { readDelimitedText: readDelimitedText, looksLikeHeader: looksLikeHeader,
                                numericColumns: numericColumns, extractPoints: extractPoints, toFloat: toFloat,
                                dataText: dataText, readWorkbook: readWorkbook, DataImporter: DataImporter };
})();
