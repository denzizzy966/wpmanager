/* ============================================================
 * DataGrid — komponen datatable kustom ala DevExtreme DataGrid
 * Vanilla JS, tanpa dependensi.
 *
 * Fitur:
 *  - Sorting multi-kolom (klik / Shift+klik)
 *  - Filter row per kolom dengan pilihan operator
 *  - Header filter (daftar centang nilai unik)
 *  - Pencarian global (search panel)
 *  - Column chooser (tampil / sembunyi kolom)
 *  - Tambah / hapus kolom saat runtime (addColumn / removeColumn)
 *  - Freeze kolom (sticky kiri) via menu klik-kanan header
 *  - Resize kolom (drag tepi header) & reorder kolom (drag header)
 *  - Grouping: drag header ke panel grup, grup bertingkat,
 *    baris grup collapsible dengan agregat sum/avg/min/max/count
 *  - Summary footer (total)
 *  - Seleksi multi baris + checkbox + pilih semua (bulk)
 *  - Paging, virtual scroll, infinite scroll
 *  - Export CSV / Excel (semua data atau baris terpilih)
 *  - Custom label kolom (caption + ubah label runtime)
 *  - Auto-formatting: currency, percent, fixedPoint, date, boolean
 *  - Tema via CSS variables (lihat themes.css)
 * ============================================================ */
(function (global) {
  'use strict';

  var SEP = '\u0001'; // pemisah path grup bertingkat
  var SEL_W = 44; // lebar kolom checkbox seleksi

  // ---------- util ----------
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function debounce(fn, ms) {
    var t = null;
    return function () {
      var self = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }
  var _fmtCache = new Map();
  function numFmt(locale, opts) {
    var k = locale + JSON.stringify(opts);
    if (!_fmtCache.has(k)) _fmtCache.set(k, new Intl.NumberFormat(locale, opts));
    return _fmtCache.get(k);
  }
  function dateFmt(locale, opts) {
    var k = 'd' + locale + JSON.stringify(opts);
    if (!_fmtCache.has(k)) _fmtCache.set(k, new Intl.DateTimeFormat(locale, opts));
    return _fmtCache.get(k);
  }
  function cmpVal(a, b) {
    var an = a == null, bn = b == null;
    if (an && bn) return 0;
    if (an) return -1;
    if (bn) return 1;
    if (a instanceof Date && b instanceof Date) return a.getTime() - b.getTime();
    if (typeof a === 'number' && typeof b === 'number') return a - b;
    if (typeof a === 'boolean' && typeof b === 'boolean') return (a ? 1 : 0) - (b ? 1 : 0);
    return String(a).localeCompare(String(b), undefined, { sensitivity: 'base', numeric: true });
  }
  function toDateInput(d) {
    if (!(d instanceof Date) || isNaN(d)) return '';
    var m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + '-' + (m < 10 ? '0' + m : m) + '-' + (day < 10 ? '0' + day : day);
  }
  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }
  function humanize(s) {
    return String(s).replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2')
      .replace(/^./, function (c) { return c.toUpperCase(); });
  }

  // ---------- teks UI (Bahasa Indonesia) ----------
  var M = {
    search: 'Cari...',
    searchCol: 'Cari',
    groupHint: 'Seret header kolom ke sini untuk mengelompokkan',
    columnChooser: 'Kolom',
    export: 'Ekspor',
    expandAll: 'Buka Semua',
    collapseAll: 'Tutup Semua',
    rows: 'baris',
    of: 'dari',
    perPage: 'Baris per halaman',
    selected: 'terpilih',
    filtered: 'terfilter',
    total: 'Total',
    noData: 'Tidak ada data',
    selectAll: '(Pilih Semua)',
    ok: 'OK',
    cancel: 'Batal',
    clear: 'Hapus Filter',
    exportCsvAll: 'CSV — semua data',
    exportCsvSel: 'CSV — baris terpilih',
    exportXlsAll: 'Excel — semua data',
    exportXlsSel: 'Excel — baris terpilih',
    sortAsc: 'Urutkan Menaik',
    sortDesc: 'Urutkan Menurun',
    sortNone: 'Hapus Pengurutan',
    groupThis: 'Kelompokkan Kolom Ini',
    ungroupThis: 'Hapus dari Grup',
    fixCol: 'Bekukan Kolom',
    unfixCol: 'Lepas Beku',
    hideCol: 'Sembunyikan Kolom',
    renameCol: 'Ubah Label...',
    yes: 'Ya',
    no: 'Tidak',
  };

  var OPS = {
    string: [
      { op: 'contains',    sym: '∗',  label: 'Mengandung' },
      { op: 'notcontains', sym: '!∗', label: 'Tidak Mengandung' },
      { op: 'startswith',  sym: 'a…', label: 'Diawali Dengan' },
      { op: 'endswith',    sym: '…z', label: 'Diakhiri Dengan' },
      { op: 'eq',          sym: '=',  label: 'Sama Dengan' },
      { op: 'ne',          sym: '≠',  label: 'Tidak Sama' },
    ],
    numeric: [
      { op: 'eq', sym: '=', label: 'Sama Dengan' },
      { op: 'ne', sym: '≠', label: 'Tidak Sama' },
      { op: 'lt', sym: '<', label: 'Kurang Dari' },
      { op: 'gt', sym: '>', label: 'Lebih Dari' },
      { op: 'le', sym: '≤', label: 'Kurang / Sama' },
      { op: 'ge', sym: '≥', label: 'Lebih / Sama' },
    ],
  };

  var FUNNEL = '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M0 0h10L6 5v4L4 8V5z" fill="currentColor"/></svg>';

  var AGG_LABEL = { sum: 'Σ', avg: 'x̄', min: 'Min', max: 'Maks', count: 'n' };

  // ============================================================
  // DataGrid
  // ============================================================
  function DataGrid(container, options) {
    this.el = typeof container === 'string' ? document.querySelector(container) : container;
    if (!this.el) throw new Error('DataGrid: container tidak ditemukan');
    options = options || {};
    this.opt = options;
    this.locale = options.locale || 'id-ID';
    this.keyExpr = options.keyExpr || null;
    this.rowHeight = options.rowHeight || 36;
    this.pageSize = (options.paging && options.paging.pageSize) || 20;
    this.pageSizes = (options.paging && options.paging.allowedPageSizes) || [10, 20, 50, 100];
    this.scrollMode = (options.scrolling && options.scrolling.mode) || 'standard';
    this.selectionMode = options.selection === false ? 'none'
      : (options.selection && options.selection.mode) || 'multiple';
    this.summary = options.summary || { groupItems: [], totalItems: [] };
    this.showFilterRow = options.filterRow !== false;
    this.showHeaderFilter = options.headerFilter !== false;
    this.showSearch = options.searchPanel !== false;
    this.showGroupPanel = options.groupPanel !== false;
    this.onSelectionChanged = options.onSelectionChanged || null;

    // ---- state ----
    this.data = [];
    this.columns = [];        // definisi master
    this.columnOrder = [];    // urutan dataField
    this.sortOrder = [];      // [{field, desc}]
    this.filters = {};        // field -> {op, value}
    this.headerFilters = {};  // field -> Set(formatted string)
    this.searchText = '';
    this.groups = [];         // [field]
    this.groupDirs = {};      // field -> 1 | -1
    this._collapsedDefault = false;
    this._collapsedEx = new Set();
    this.selected = new Set();
    this._selAnchor = null;
    this.page = 0;
    this._loaded = 60;        // infinite scroll: jumlah baris termuat
    this._filteredRows = [];
    this._flat = [];
    this._addedCols = [];

    this._initColumns(options.columns || []);
    this._buildShell();
    this._bindEvents();

    if (options.dataSource) this.setData(options.dataSource);
    else this._rebuild();
  }

  var P = DataGrid.prototype;

  // ---------- kolom ----------
  P._initColumns = function (cols) {
    var self = this;
    this.columns = cols.map(function (c) { return self._normCol(c); });
    this.columnOrder = this.columns.map(function (c) { return c.dataField; });
    // groupIndex ala DevExtreme
    var grouped = this.columns.filter(function (c) { return c.groupIndex != null; })
      .sort(function (a, b) { return a.groupIndex - b.groupIndex; });
    grouped.forEach(function (c) {
      self.groups.push(c.dataField);
      self.groupDirs[c.dataField] = 1;
      c._groupHidden = true;
    });
  };

  P._normCol = function (c) {
    return {
      dataField: c.dataField,
      caption: c.caption || humanize(c.dataField),
      dataType: c.dataType || null,
      width: c.width || 140,
      visible: c.visible !== false,
      fixed: !!c.fixed,
      format: c.format || null,
      currency: c.currency || 'IDR',
      align: c.align || null,
      cellTemplate: c.cellTemplate || null,
      calculateCellValue: c.calculateCellValue || null,
      groupIndex: c.groupIndex != null ? c.groupIndex : null,
      allowSorting: c.allowSorting !== false,
      allowGrouping: c.allowGrouping !== false,
      allowFiltering: c.allowFiltering !== false,
      allowHiding: c.allowHiding !== false,
      allowReordering: c.allowReordering !== false,
      allowResizing: c.allowResizing !== false,
      allowFixing: c.allowFixing !== false,
      _groupHidden: false,
    };
  };

  P._col = function (field) {
    for (var i = 0; i < this.columns.length; i++)
      if (this.columns[i].dataField === field) return this.columns[i];
    return null;
  };

  P._val = function (row, col) {
    return col.calculateCellValue ? col.calculateCellValue(row) : row[col.dataField];
  };

  P._inferTypes = function () {
    var self = this;
    this.columns.forEach(function (col) {
      if (col.dataType) return;
      for (var i = 0; i < Math.min(self.data.length, 100); i++) {
        var v = self._val(self.data[i], col);
        if (v == null) continue;
        if (v instanceof Date) { col.dataType = 'date'; return; }
        if (typeof v === 'number') { col.dataType = 'number'; return; }
        if (typeof v === 'boolean') { col.dataType = 'boolean'; return; }
        col.dataType = 'string';
        return;
      }
      col.dataType = 'string';
    });
    this.columns.forEach(function (col) {
      if (!col.align) col.align = col.dataType === 'number' ? 'right'
        : col.dataType === 'boolean' ? 'center' : 'left';
    });
  };

  P._visibleCols = function () {
    var self = this;
    var order = this.columnOrder
      .map(function (f) { return self._col(f); })
      .filter(function (c) { return c && c.visible && !c._groupHidden; });
    var fixed = order.filter(function (c) { return c.fixed; });
    var rest = order.filter(function (c) { return !c.fixed; });
    return fixed.concat(rest);
  };

  P._fixedLefts = function (vcols) {
    var lefts = {}, left = this.selectionMode !== 'none' ? SEL_W : 0;
    var lastFixed = null;
    for (var i = 0; i < vcols.length; i++) {
      if (!vcols[i].fixed) break;
      lefts[vcols[i].dataField] = left;
      left += vcols[i].width;
      lastFixed = vcols[i].dataField;
    }
    return { lefts: lefts, last: lastFixed };
  };

  // ---------- data ----------
  P.setData = function (rows) {
    this.data = rows || [];
    if (!this.keyExpr) {
      for (var i = 0; i < this.data.length; i++)
        if (this.data[i].__dgKey == null) this.data[i].__dgKey = i;
    }
    this._inferTypes();
    this.selected.clear();
    this.page = 0;
    this._loaded = 60;
    this._rebuild();
    this._renderAll();
  };

  P._key = function (row) {
    return this.keyExpr ? row[this.keyExpr] : row.__dgKey;
  };

  // ---------- format ----------
  P.formatValue = function (col, v) {
    if (v == null || v === '') return '';
    if (typeof col.format === 'function') return String(col.format(v));
    if (col.dataType === 'date') {
      var d = v instanceof Date ? v : new Date(v);
      if (isNaN(d)) return String(v);
      return dateFmt(this.locale, { day: '2-digit', month: 'short', year: 'numeric' }).format(d);
    }
    if (col.dataType === 'boolean') return v ? M.yes : M.no;
    if (col.dataType === 'number') {
      var f = col.format;
      var type = typeof f === 'string' ? f : (f && f.type) || null;
      var prec = (f && f.precision != null) ? f.precision : null;
      if (type === 'currency')
        return numFmt(this.locale, {
          style: 'currency', currency: col.currency,
          minimumFractionDigits: prec != null ? prec : 0,
          maximumFractionDigits: prec != null ? prec : 0,
        }).format(v);
      if (type === 'percent')
        return numFmt(this.locale, {
          style: 'percent',
          minimumFractionDigits: prec != null ? prec : 0,
          maximumFractionDigits: prec != null ? prec : 0,
        }).format(v);
      if (type === 'fixedPoint')
        return numFmt(this.locale, {
          minimumFractionDigits: prec != null ? prec : 2,
          maximumFractionDigits: prec != null ? prec : 2,
        }).format(v);
      return numFmt(this.locale, { maximumFractionDigits: 2 }).format(v);
    }
    return String(v);
  };

  // ---------- pipeline: filter -> sort -> group/flatten ----------
  P._matchFilter = function (row) {
    var self = this;
    // filter row
    for (var field in this.filters) {
      var flt = this.filters[field];
      if (flt.value === '' || flt.value == null) continue;
      var col = this._col(field);
      if (!col) continue;
      var raw = this._val(row, col);
      if (!this._applyOp(col, raw, flt.op, flt.value)) return false;
    }
    // header filter
    for (var hf in this.headerFilters) {
      var set = this.headerFilters[hf];
      if (!set) continue;
      var c2 = this._col(hf);
      if (!c2) continue;
      var fv;
      if (c2.dataType === 'date') {
        // kolom tanggal difilter per hari (kunci "YYYY-M-D", M = indeks bulan)
        var dv = this._val(row, c2);
        fv = (dv instanceof Date && !isNaN(dv))
          ? dv.getFullYear() + '-' + dv.getMonth() + '-' + dv.getDate()
          : '(kosong)';
      } else {
        fv = this.formatValue(c2, this._val(row, c2)) || '(kosong)';
      }
      if (!set.has(fv)) return false;
    }
    // pencarian global
    if (this.searchText) {
      var q = this.searchText.toLowerCase();
      var vcols = this._searchCols || this._visibleCols();
      var hit = false;
      for (var i = 0; i < vcols.length; i++) {
        var txt = this.formatValue(vcols[i], this._val(row, vcols[i]));
        if (txt && txt.toLowerCase().indexOf(q) !== -1) { hit = true; break; }
      }
      if (!hit) return false;
    }
    return true;
  };

  P._applyOp = function (col, raw, op, value) {
    if (col.dataType === 'number') {
      var n = parseFloat(value);
      if (isNaN(n)) return true;
      if (raw == null) return false;
      switch (op) {
        case 'eq': return raw === n;
        case 'ne': return raw !== n;
        case 'lt': return raw < n;
        case 'gt': return raw > n;
        case 'le': return raw <= n;
        case 'ge': return raw >= n;
      }
      return true;
    }
    if (col.dataType === 'date') {
      var parts = String(value).split('-');
      if (parts.length !== 3) return true;
      var fd = new Date(+parts[0], +parts[1] - 1, +parts[2]);
      if (isNaN(fd)) return true;
      if (!(raw instanceof Date)) return false;
      switch (op) {
        case 'eq': return sameDay(raw, fd);
        case 'ne': return !sameDay(raw, fd);
        case 'lt': return raw.getTime() < fd.getTime();
        case 'gt': return raw.getTime() >= fd.getTime() + 86400000;
        case 'le': return raw.getTime() < fd.getTime() + 86400000;
        case 'ge': return raw.getTime() >= fd.getTime();
      }
      return true;
    }
    var s = raw == null ? '' : String(raw).toLowerCase();
    var q = String(value).toLowerCase();
    switch (op) {
      case 'contains': return s.indexOf(q) !== -1;
      case 'notcontains': return s.indexOf(q) === -1;
      case 'startswith': return s.indexOf(q) === 0;
      case 'endswith': return s.length >= q.length && s.lastIndexOf(q) === s.length - q.length;
      case 'eq': return s === q;
      case 'ne': return s !== q;
    }
    return true;
  };

  P._rebuild = function () {
    var self = this;
    this._searchCols = this._visibleCols();

    // 1. filter
    var rows = this.data.filter(function (r) { return self._matchFilter(r); });

    // 2. sort: field grup dulu, lalu sort order pengguna
    var keys = [];
    this.groups.forEach(function (f) {
      keys.push({ col: self._col(f), desc: (self.groupDirs[f] || 1) < 0 });
    });
    this.sortOrder.forEach(function (s) {
      var col = self._col(s.field);
      if (col) keys.push({ col: col, desc: s.desc });
    });
    if (keys.length) {
      rows = rows.slice().sort(function (a, b) {
        for (var i = 0; i < keys.length; i++) {
          var k = keys[i];
          var c = cmpVal(self._val(a, k.col), self._val(b, k.col));
          if (c !== 0) return k.desc ? -c : c;
        }
        return 0;
      });
    }
    this._filteredRows = rows;

    // 3. group + flatten
    this._flat = this._buildFlat(rows);

    // klem halaman
    var pages = Math.max(1, Math.ceil(this._flat.length / this.pageSize));
    if (this.page >= pages) this.page = pages - 1;
    if (this._loaded > this._flat.length) this._loaded = Math.max(60, this._flat.length ? Math.min(this._loaded, this._flat.length) : 60);
  };

  P._buildFlat = function (rows) {
    var self = this;
    var flat = [];
    if (!this.groups.length) {
      for (var i = 0; i < rows.length; i++) flat.push({ t: 'd', r: rows[i], level: 0 });
      return flat;
    }
    var gi = (this.summary.groupItems || []);
    function walk(subset, level, path) {
      var field = self.groups[level];
      var col = self._col(field);
      if (!col) { subset.forEach(function (r) { flat.push({ t: 'd', r: r, level: level }); }); return; }
      var i = 0;
      while (i < subset.length) {
        var fv = self.formatValue(col, self._val(subset[i], col)) || '(kosong)';
        var j = i + 1;
        while (j < subset.length &&
          (self.formatValue(col, self._val(subset[j], col)) || '(kosong)') === fv) j++;
        var slice = subset.slice(i, j);
        var pk = path ? path + SEP + fv : fv;
        var aggs = gi.map(function (item) {
          var acol = self._col(item.column);
          if (!acol) return null;
          return { col: acol, type: item.type, value: self._agg(slice, acol, item.type) };
        }).filter(Boolean);
        flat.push({ t: 'g', level: level, col: col, value: fv, count: slice.length, aggs: aggs, pk: pk });
        if (!self._isCollapsed(pk)) {
          if (level + 1 < self.groups.length) walk(slice, level + 1, pk);
          else slice.forEach(function (r) { flat.push({ t: 'd', r: r, level: level + 1 }); });
        }
        i = j;
      }
    }
    walk(rows, 0, '');
    return flat;
  };

  P._agg = function (rows, col, type) {
    var self = this;
    if (type === 'count') return rows.length;
    var sum = 0, min = null, max = null, n = 0;
    for (var i = 0; i < rows.length; i++) {
      var v = this._val(rows[i], col);
      if (v == null || typeof v !== 'number') continue;
      sum += v; n++;
      if (min == null || v < min) min = v;
      if (max == null || v > max) max = v;
    }
    switch (type) {
      case 'sum': return sum;
      case 'avg': return n ? sum / n : null;
      case 'min': return min;
      case 'max': return max;
    }
    return null;
  };

  P._isCollapsed = function (pk) {
    return this._collapsedDefault ? !this._collapsedEx.has(pk) : this._collapsedEx.has(pk);
  };

  // ---------- shell ----------
  P._buildShell = function () {
    this.el.classList.add('dg');
    this.el.innerHTML =
      '<div class="dg-toolbar">' +
        '<div class="dg-toolbar-left">' +
          '<button class="dg-btn" data-act="expand" title="' + M.expandAll + '">⊞ ' + M.expandAll + '</button>' +
          '<button class="dg-btn" data-act="collapse" title="' + M.collapseAll + '">⊟ ' + M.collapseAll + '</button>' +
        '</div>' +
        '<div class="dg-toolbar-right">' +
          (this.showSearch ? '<div class="dg-search"><span>⌕</span><input type="text" class="dg-search-input" placeholder="' + M.search + '"></div>' : '') +
          '<button class="dg-btn" data-act="chooser">▦ ' + M.columnChooser + '</button>' +
          '<button class="dg-btn" data-act="export">⇩ ' + M.export + '</button>' +
        '</div>' +
      '</div>' +
      (this.showGroupPanel ? '<div class="dg-group-panel"><span class="dg-group-hint">' + M.groupHint + '</span></div>' : '') +
      '<div class="dg-body"><table class="dg-table"><colgroup></colgroup><thead></thead><tbody></tbody><tfoot></tfoot></table></div>' +
      '<div class="dg-footer"><div class="dg-status"></div><div class="dg-pager"></div></div>' +
      '<div class="dg-chooser" hidden></div>' +
      '<div class="dg-menu" hidden></div>' +
      '<div class="dg-dropdown" hidden></div>';

    this.$body = this.el.querySelector('.dg-body');
    this.$table = this.el.querySelector('.dg-table');
    this.$colgroup = this.$table.querySelector('colgroup');
    this.$thead = this.$table.querySelector('thead');
    this.$tbody = this.$table.querySelector('tbody');
    this.$tfoot = this.$table.querySelector('tfoot');
    this.$groupPanel = this.el.querySelector('.dg-group-panel');
    this.$status = this.el.querySelector('.dg-status');
    this.$pager = this.el.querySelector('.dg-pager');
    this.$chooser = this.el.querySelector('.dg-chooser');
    this.$menu = this.el.querySelector('.dg-menu');
    this.$dropdown = this.el.querySelector('.dg-dropdown');
  };

  // ---------- render ----------
  P._renderAll = function () {
    this._renderGroupPanel();
    this._renderColgroup();
    this._renderHeader();
    this._renderBody();
    this._renderFoot();
    this._renderPager();
    this._updateStatus();
  };

  P._refresh = function () {
    this._rebuild();
    this._renderColgroup();
    this._renderBody();
    this._renderFoot();
    this._renderPager();
    this._updateStatus();
  };

  P._renderGroupPanel = function () {
    if (!this.$groupPanel) return;
    var self = this;
    var html = '';
    if (!this.groups.length) {
      html = '<span class="dg-group-hint">' + M.groupHint + '</span>';
    } else {
      this.groups.forEach(function (f) {
        var col = self._col(f);
        if (!col) return;
        var dir = (self.groupDirs[f] || 1) > 0 ? '▲' : '▼';
        html += '<span class="dg-chip" data-field="' + esc(f) + '" title="Klik untuk membalik urutan">' +
          esc(col.caption) + ' <b>' + dir + '</b><i class="dg-chip-x" title="Hapus grup">✕</i></span>';
      });
    }
    this.$groupPanel.innerHTML = html;
    var exBtns = this.el.querySelectorAll('[data-act="expand"],[data-act="collapse"]');
    for (var i = 0; i < exBtns.length; i++)
      exBtns[i].classList.toggle('dg-disabled', !this.groups.length);
  };

  P._renderColgroup = function () {
    var vcols = this._visibleCols();
    var html = '', total = 0;
    if (this.selectionMode !== 'none') { html += '<col style="width:' + SEL_W + 'px">'; total += SEL_W; }
    vcols.forEach(function (c) { html += '<col style="width:' + c.width + 'px">'; total += c.width; });
    this.$colgroup.innerHTML = html;
    this.$table.style.width = total + 'px';
  };

  P._renderHeader = function () {
    var self = this;
    var vcols = this._visibleCols();
    var fx = this._fixedLefts(vcols);
    var html = '<tr class="dg-header-row">';

    if (this.selectionMode !== 'none') {
      html += '<th class="dg-h dg-selcell dg-fixed" style="left:0">' +
        (this.selectionMode === 'multiple' ? '<input type="checkbox" class="dg-check-all" title="Pilih semua (bulk)">' : '') +
        '</th>';
    }

    vcols.forEach(function (col) {
      var f = col.dataField;
      var fixed = fx.lefts[f] != null;
      var cls = 'dg-h' + (fixed ? ' dg-fixed' : '') + (fx.last === f ? ' dg-fixed-last' : '');
      var style = fixed ? ' style="left:' + fx.lefts[f] + 'px"' : '';
      var sortIdx = -1, sortDesc = false;
      for (var i = 0; i < self.sortOrder.length; i++)
        if (self.sortOrder[i].field === f) { sortIdx = i; sortDesc = self.sortOrder[i].desc; }
      var sortHtml = sortIdx >= 0
        ? '<span class="dg-sort">' + (sortDesc ? '▼' : '▲') +
          (self.sortOrder.length > 1 ? '<i>' + (sortIdx + 1) + '</i>' : '') + '</span>'
        : '';
      var hfActive = !!self.headerFilters[f];
      var hfHtml = (self.showHeaderFilter && col.allowFiltering)
        ? '<span class="dg-hf-btn' + (hfActive ? ' dg-active' : '') + '" data-field="' + esc(f) + '" title="Filter nilai">' + FUNNEL + '</span>'
        : '';
      html += '<th class="' + cls + '" data-field="' + esc(f) + '" draggable="true"' + style + '>' +
        '<div class="dg-h-inner"><span class="dg-h-caption">' + esc(col.caption) + '</span>' +
        sortHtml + hfHtml + '</div>' +
        (col.allowResizing ? '<div class="dg-resize" data-field="' + esc(f) + '"></div>' : '') +
        '</th>';
    });
    html += '</tr>';

    // baris filter per kolom
    if (this.showFilterRow) {
      html += '<tr class="dg-filter-row">';
      if (this.selectionMode !== 'none')
        html += '<td class="dg-f dg-selcell dg-fixed" style="left:0"></td>';
      vcols.forEach(function (col) {
        var f = col.dataField;
        var fixed = fx.lefts[f] != null;
        var cls = 'dg-f' + (fixed ? ' dg-fixed' : '') + (fx.last === f ? ' dg-fixed-last' : '');
        var style = fixed ? ' style="left:' + fx.lefts[f] + 'px"' : '';
        if (!col.allowFiltering) {
          html += '<td class="' + cls + '"' + style + '></td>';
          return;
        }
        var flt = self.filters[f] || {};
        var isNum = col.dataType === 'number' || col.dataType === 'date';
        var ops = isNum ? OPS.numeric : OPS.string;
        var curOp = flt.op || ops[0].op;
        var sym = '=';
        for (var i = 0; i < ops.length; i++) if (ops[i].op === curOp) sym = ops[i].sym;
        var inputType = col.dataType === 'date' ? 'date' : col.dataType === 'number' ? 'number' : 'text';
        html += '<td class="' + cls + '" data-field="' + esc(f) + '"' + style + '><div class="dg-f-wrap">' +
          '<button class="dg-f-op" data-field="' + esc(f) + '" title="Pilih operator">' + sym + '</button>' +
          '<input class="dg-f-input" data-field="' + esc(f) + '" type="' + inputType + '"' +
          ' placeholder="' + M.searchCol + '" value="' + esc(flt.value != null ? flt.value : '') + '">' +
          '</div></td>';
      });
      html += '</tr>';
    }
    this.$thead.innerHTML = html;
    this._updateCheckAll();
  };

  P._windowRange = function () {
    var total = this._flat.length;
    if (this.scrollMode === 'standard') {
      var s = this.page * this.pageSize;
      return { start: s, end: Math.min(total, s + this.pageSize), total: total, virtual: false };
    }
    var limit = this.scrollMode === 'infinite' ? Math.min(this._loaded, total) : total;
    var st = this.$body.scrollTop, vh = this.$body.clientHeight || 400;
    var start = Math.max(0, Math.floor(st / this.rowHeight) - 6);
    var end = Math.min(limit, Math.ceil((st + vh) / this.rowHeight) + 6);
    return { start: start, end: end, total: limit, virtual: true };
  };

  P._renderBody = function () {
    var self = this;
    var vcols = this._visibleCols();
    var fx = this._fixedLefts(vcols);
    var nCols = vcols.length + (this.selectionMode !== 'none' ? 1 : 0);
    var w = this._windowRange();
    var html = '';

    if (!this._flat.length) {
      html = '<tr class="dg-nodata-row"><td colspan="' + nCols + '"><div class="dg-nodata">' + M.noData + '</div></td></tr>';
      this.$tbody.innerHTML = html;
      return;
    }

    if (w.virtual && w.start > 0)
      html += '<tr class="dg-spacer"><td colspan="' + nCols + '" style="height:' + (w.start * this.rowHeight) + 'px"></td></tr>';

    for (var i = w.start; i < w.end; i++) {
      var item = this._flat[i];
      if (item.t === 'g') {
        var open = !this._isCollapsed(item.pk);
        var aggTxt = item.aggs.map(function (a) {
          return AGG_LABEL[a.type] + ' ' + esc(a.col.caption) + ': <b>' +
            esc(self.formatValue(a.col, a.value)) + '</b>';
        }).join('<span class="dg-agg-sep">•</span>');
        html += '<tr class="dg-grouprow" data-pk="' + esc(item.pk) + '">' +
          '<td colspan="' + nCols + '"><div class="dg-group-inner" style="padding-left:' + (10 + item.level * 22) + 'px">' +
          '<span class="dg-arrow' + (open ? ' dg-open' : '') + '">▶</span>' +
          '<span class="dg-group-label">' + esc(item.col.caption) + ': <b>' + esc(item.value) + '</b></span>' +
          '<span class="dg-group-count">(' + item.count.toLocaleString(this.locale) + ' ' + M.rows + ')</span>' +
          (aggTxt ? '<span class="dg-group-aggs">' + aggTxt + '</span>' : '') +
          '</div></td></tr>';
        continue;
      }
      var row = item.r;
      var key = this._key(row);
      var isSel = this.selected.has(key);
      var trCls = 'dg-row' + (isSel ? ' dg-selected' : '') + (i % 2 ? ' dg-alt' : '');
      html += '<tr class="' + trCls + '" data-key="' + esc(key) + '" data-fi="' + i + '">';
      if (this.selectionMode !== 'none')
        html += '<td class="dg-cell dg-selcell dg-fixed" style="left:0">' +
          '<input type="checkbox" class="dg-check" ' + (isSel ? 'checked' : '') + '></td>';
      for (var cIdx = 0; cIdx < vcols.length; cIdx++) {
        var col = vcols[cIdx];
        var fixed = fx.lefts[col.dataField] != null;
        var cls = 'dg-cell' + (fixed ? ' dg-fixed' : '') +
          (fx.last === col.dataField ? ' dg-fixed-last' : '') +
          (col.align === 'right' ? ' dg-num' : col.align === 'center' ? ' dg-center' : '');
        var style = fixed ? ' style="left:' + fx.lefts[col.dataField] + 'px"' : '';
        var raw = this._val(row, col);
        var content = col.cellTemplate
          ? col.cellTemplate(raw, row, this)
          : esc(this.formatValue(col, raw));
        html += '<td class="' + cls + '"' + style + '>' + content + '</td>';
      }
      html += '</tr>';
    }

    if (w.virtual && w.end < w.total)
      html += '<tr class="dg-spacer"><td colspan="' + nCols + '" style="height:' + ((w.total - w.end) * this.rowHeight) + 'px"></td></tr>';

    this.$tbody.innerHTML = html;
  };

  P._renderFoot = function () {
    var items = this.summary.totalItems || [];
    if (!items.length) { this.$tfoot.innerHTML = ''; return; }
    var self = this;
    var vcols = this._visibleCols();
    var fx = this._fixedLefts(vcols);
    var byField = {};
    items.forEach(function (it) {
      (byField[it.column] = byField[it.column] || []).push(it);
    });
    var html = '<tr class="dg-summary-row">';
    if (this.selectionMode !== 'none')
      html += '<td class="dg-s dg-selcell dg-fixed" style="left:0"></td>';
    vcols.forEach(function (col) {
      var f = col.dataField;
      var fixed = fx.lefts[f] != null;
      var cls = 'dg-s' + (fixed ? ' dg-fixed' : '') + (fx.last === f ? ' dg-fixed-last' : '') +
        (col.align === 'right' ? ' dg-num' : '');
      var style = fixed ? ' style="left:' + fx.lefts[f] + 'px"' : '';
      var lines = (byField[f] || []).map(function (it) {
        var v = self._agg(self._filteredRows, col, it.type);
        var txt = it.type === 'count'
          ? v.toLocaleString(self.locale) + ' ' + M.rows
          : self.formatValue(col, v);
        return '<div class="dg-s-line"><span>' + AGG_LABEL[it.type] + '</span> ' + esc(txt) + '</div>';
      }).join('');
      html += '<td class="' + cls + '"' + style + '>' + lines + '</td>';
    });
    html += '</tr>';
    this.$tfoot.innerHTML = html;
  };

  P._renderPager = function () {
    if (this.scrollMode !== 'standard') {
      var totalRows = this._flat.length;
      this.$pager.innerHTML = '<span class="dg-page-info">' +
        (this.scrollMode === 'virtual' ? 'Virtual scroll — ' : 'Infinite scroll — ') +
        totalRows.toLocaleString(this.locale) + ' ' + M.rows + '</span>';
      return;
    }
    var total = this._flat.length;
    var pages = Math.max(1, Math.ceil(total / this.pageSize));
    var p = this.page;
    var from = total ? p * this.pageSize + 1 : 0;
    var to = Math.min(total, (p + 1) * this.pageSize);
    var html = '<span class="dg-page-info">' + from.toLocaleString(this.locale) + '–' +
      to.toLocaleString(this.locale) + ' ' + M.of + ' ' + total.toLocaleString(this.locale) + '</span>';
    html += '<select class="dg-pagesize" title="' + M.perPage + '">';
    var self = this;
    this.pageSizes.forEach(function (s) {
      html += '<option value="' + s + '"' + (s === self.pageSize ? ' selected' : '') + '>' + s + '</option>';
    });
    html += '</select>';
    html += '<button class="dg-pbtn" data-page="0" ' + (p === 0 ? 'disabled' : '') + '>«</button>';
    html += '<button class="dg-pbtn" data-page="' + (p - 1) + '" ' + (p === 0 ? 'disabled' : '') + '>‹</button>';
    var start = Math.max(0, Math.min(p - 2, pages - 5));
    var end = Math.min(pages, start + 5);
    for (var i = start; i < end; i++)
      html += '<button class="dg-pbtn' + (i === p ? ' dg-current' : '') + '" data-page="' + i + '">' + (i + 1) + '</button>';
    html += '<button class="dg-pbtn" data-page="' + (p + 1) + '" ' + (p >= pages - 1 ? 'disabled' : '') + '>›</button>';
    html += '<button class="dg-pbtn" data-page="' + (pages - 1) + '" ' + (p >= pages - 1 ? 'disabled' : '') + '>»</button>';
    this.$pager.innerHTML = html;
  };

  P._updateStatus = function () {
    var total = this.data.length;
    var filtered = this._filteredRows.length;
    var sel = this.selected.size;
    var txt = M.total + ': <b>' + total.toLocaleString(this.locale) + '</b> ' + M.rows;
    if (filtered !== total)
      txt += ' • ' + M.filtered + ': <b>' + filtered.toLocaleString(this.locale) + '</b>';
    if (sel)
      txt += ' • <span class="dg-status-sel">' + sel.toLocaleString(this.locale) + ' ' + M.selected + '</span>';
    this.$status.innerHTML = txt;
    this._updateCheckAll();
  };

  P._updateCheckAll = function () {
    var cb = this.el.querySelector('.dg-check-all');
    if (!cb) return;
    var n = 0;
    for (var i = 0; i < this._filteredRows.length; i++)
      if (this.selected.has(this._key(this._filteredRows[i]))) n++;
    cb.checked = n > 0 && n === this._filteredRows.length;
    cb.indeterminate = n > 0 && n < this._filteredRows.length;
  };

  // ---------- events ----------
  P._bindEvents = function () {
    var self = this;
    var root = this.el;

    // --- toolbar ---
    root.addEventListener('click', function (e) {
      var btn = e.target.closest('.dg-btn');
      if (!btn || !root.contains(btn)) return;
      var act = btn.getAttribute('data-act');
      if (act === 'expand') self.expandAll();
      else if (act === 'collapse') self.collapseAll();
      else if (act === 'chooser') self._toggleChooser(btn);
      else if (act === 'export') self._showExportMenu(btn);
    });

    var searchInput = root.querySelector('.dg-search-input');
    if (searchInput) {
      searchInput.addEventListener('input', debounce(function () {
        self.searchText = searchInput.value.trim();
        self.page = 0;
        self._refresh();
      }, 250));
    }

    // --- header: sort, menu konteks, header filter, resize, drag ---
    this.$thead.addEventListener('click', function (e) {
      if (self._resizing) return;
      var hf = e.target.closest('.dg-hf-btn');
      if (hf) { e.stopPropagation(); self._showHeaderFilter(hf); return; }
      var opBtn = e.target.closest('.dg-f-op');
      if (opBtn) { e.stopPropagation(); self._showOpMenu(opBtn); return; }
      var th = e.target.closest('th.dg-h');
      if (!th || !th.dataset.field) return;
      var col = self._col(th.dataset.field);
      if (!col || !col.allowSorting) return;
      self._toggleSort(col.dataField, e.shiftKey);
    });

    this.$thead.addEventListener('contextmenu', function (e) {
      var th = e.target.closest('th.dg-h');
      if (!th || !th.dataset.field) return;
      e.preventDefault();
      self._showColumnMenu(th, th.dataset.field);
    });

    this.$thead.addEventListener('input', debounce(function (e) {
      var input = e.target.closest('.dg-f-input');
      if (!input) return;
      var f = input.dataset.field;
      var col = self._col(f);
      if (!col) return;
      var isNum = col.dataType === 'number' || col.dataType === 'date';
      var cur = self.filters[f] || { op: isNum ? 'eq' : 'contains' };
      cur.value = input.value;
      self.filters[f] = cur;
      self.page = 0;
      self._refresh();
    }, 300));

    // resize kolom
    this.$thead.addEventListener('pointerdown', function (e) {
      var handle = e.target.closest('.dg-resize');
      if (!handle) return;
      e.preventDefault();
      e.stopPropagation();
      var col = self._col(handle.dataset.field);
      if (!col) return;
      self._resizing = true;
      var th = handle.closest('th');
      th.draggable = false;
      var startX = e.clientX, startW = col.width;
      function move(ev) {
        col.width = Math.max(50, startW + (ev.clientX - startX));
        self._renderColgroup();
      }
      function up() {
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        th.draggable = true;
        self._renderColgroup();
        self._renderHeader();
        self._renderBody();
        self._renderFoot();
        setTimeout(function () { self._resizing = false; }, 50);
      }
      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
    });

    // drag header: reorder + drag ke panel grup
    this.$thead.addEventListener('dragstart', function (e) {
      var th = e.target.closest('th.dg-h');
      if (!th || !th.dataset.field || self._resizing) { e.preventDefault(); return; }
      self._dragField = th.dataset.field;
      e.dataTransfer.setData('text/plain', th.dataset.field);
      e.dataTransfer.effectAllowed = 'move';
    });
    this.$thead.addEventListener('dragover', function (e) {
      if (!self._dragField) return;
      var th = e.target.closest('th.dg-h');
      if (!th || !th.dataset.field || th.dataset.field === self._dragField) return;
      e.preventDefault();
      var rect = th.getBoundingClientRect();
      var before = (e.clientX - rect.left) < rect.width / 2;
      self._clearDropMarks();
      th.classList.add(before ? 'dg-drop-left' : 'dg-drop-right');
    });
    this.$thead.addEventListener('drop', function (e) {
      if (!self._dragField) return;
      var th = e.target.closest('th.dg-h');
      if (!th || !th.dataset.field) return;
      e.preventDefault();
      var rect = th.getBoundingClientRect();
      var before = (e.clientX - rect.left) < rect.width / 2;
      self._reorderColumn(self._dragField, th.dataset.field, before);
      self._dragField = null;
      self._clearDropMarks();
    });
    root.addEventListener('dragend', function () {
      self._dragField = null;
      self._clearDropMarks();
      if (self.$groupPanel) self.$groupPanel.classList.remove('dg-drop-active');
    });

    // panel grup: drop target + chip
    if (this.$groupPanel) {
      this.$groupPanel.addEventListener('dragover', function (e) {
        if (!self._dragField) return;
        var col = self._col(self._dragField);
        if (!col || !col.allowGrouping || self.groups.indexOf(self._dragField) !== -1) return;
        e.preventDefault();
        self.$groupPanel.classList.add('dg-drop-active');
      });
      this.$groupPanel.addEventListener('dragleave', function () {
        self.$groupPanel.classList.remove('dg-drop-active');
      });
      this.$groupPanel.addEventListener('drop', function (e) {
        e.preventDefault();
        self.$groupPanel.classList.remove('dg-drop-active');
        if (self._dragField) self.groupBy(self._dragField);
        self._dragField = null;
      });
      this.$groupPanel.addEventListener('click', function (e) {
        var x = e.target.closest('.dg-chip-x');
        if (x) { self.ungroup(x.closest('.dg-chip').dataset.field); return; }
        var chip = e.target.closest('.dg-chip');
        if (chip) {
          var f = chip.dataset.field;
          self.groupDirs[f] = -(self.groupDirs[f] || 1);
          self._refreshFull();
        }
      });
    }

    // --- body: grup toggle, seleksi ---
    this.$tbody.addEventListener('click', function (e) {
      var grow = e.target.closest('.dg-grouprow');
      if (grow) {
        var pk = grow.dataset.pk;
        if (self._collapsedEx.has(pk)) self._collapsedEx.delete(pk);
        else self._collapsedEx.add(pk);
        self._rebuild();
        self._renderBody();
        self._renderPager();
        return;
      }
      var tr = e.target.closest('tr.dg-row');
      if (!tr || self.selectionMode === 'none') return;
      var key = self._parseKey(tr.dataset.key);
      if (e.target.classList.contains('dg-check')) return; // ditangani via change
      var fi = parseInt(tr.dataset.fi, 10);
      if (self.selectionMode === 'single') {
        self.selected.clear();
        self.selected.add(key);
      } else if (e.shiftKey && self._selAnchor != null) {
        self._selectRange(self._selAnchor, fi, !e.ctrlKey);
      } else if (e.ctrlKey || e.metaKey) {
        if (self.selected.has(key)) self.selected.delete(key);
        else self.selected.add(key);
        self._selAnchor = fi;
      } else {
        self.selected.clear();
        self.selected.add(key);
        self._selAnchor = fi;
      }
      self._afterSelection();
    });

    this.$tbody.addEventListener('change', function (e) {
      if (!e.target.classList.contains('dg-check')) return;
      var tr = e.target.closest('tr.dg-row');
      if (!tr) return;
      var key = self._parseKey(tr.dataset.key);
      if (e.target.checked) self.selected.add(key);
      else self.selected.delete(key);
      self._selAnchor = parseInt(tr.dataset.fi, 10);
      self._afterSelection();
    });

    // pilih semua (bulk)
    this.$thead.addEventListener('change', function (e) {
      if (!e.target.classList.contains('dg-check-all')) return;
      if (e.target.checked) self.selectAll();
      else self.clearSelection();
    });

    // --- pager ---
    this.$pager.addEventListener('click', function (e) {
      var btn = e.target.closest('.dg-pbtn');
      if (!btn || btn.disabled) return;
      self.page = parseInt(btn.dataset.page, 10);
      self._renderBody();
      self._renderPager();
    });
    this.$pager.addEventListener('change', function (e) {
      if (!e.target.classList.contains('dg-pagesize')) return;
      self.pageSize = parseInt(e.target.value, 10);
      self.page = 0;
      self._refresh();
    });

    // --- scroll: virtual / infinite ---
    var ticking = false;
    this.$body.addEventListener('scroll', function () {
      if (self.scrollMode === 'standard') return;
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        if (self.scrollMode === 'infinite') {
          var nearBottom = self.$body.scrollTop + self.$body.clientHeight >
            Math.min(self._loaded, self._flat.length) * self.rowHeight - 300;
          if (nearBottom && self._loaded < self._flat.length) {
            self._loaded = Math.min(self._loaded + 60, self._flat.length);
            self._renderPager();
          }
        }
        self._renderBody();
      });
    });

    // tutup popup saat klik di luar
    document.addEventListener('mousedown', function (e) {
      if (!self.$menu.hidden && !self.$menu.contains(e.target)) self.$menu.hidden = true;
      if (!self.$dropdown.hidden && !self.$dropdown.contains(e.target) &&
          !e.target.closest('.dg-hf-btn')) self.$dropdown.hidden = true;
      if (!self.$chooser.hidden && !self.$chooser.contains(e.target) &&
          !e.target.closest('[data-act="chooser"]')) self.$chooser.hidden = true;
    });
  };

  P._parseKey = function (s) {
    // kunci disimpan sebagai atribut string; kembalikan ke number bila perlu
    var n = Number(s);
    return !isNaN(n) && String(n) === s ? n : s;
  };

  P._clearDropMarks = function () {
    var marked = this.$thead.querySelectorAll('.dg-drop-left,.dg-drop-right');
    for (var i = 0; i < marked.length; i++)
      marked[i].classList.remove('dg-drop-left', 'dg-drop-right');
  };

  P._afterSelection = function () {
    this._renderBody();
    this._updateStatus();
    if (this.onSelectionChanged)
      this.onSelectionChanged({ keys: this.getSelectedRowKeys(), rows: this.getSelectedRowsData() });
  };

  P._selectRange = function (a, b, replace) {
    if (replace) this.selected.clear();
    var lo = Math.min(a, b), hi = Math.max(a, b);
    for (var i = lo; i <= hi; i++) {
      var it = this._flat[i];
      if (it && it.t === 'd') this.selected.add(this._key(it.r));
    }
  };

  P._toggleSort = function (field, additive) {
    var idx = -1;
    for (var i = 0; i < this.sortOrder.length; i++)
      if (this.sortOrder[i].field === field) idx = i;
    if (!additive) {
      if (idx >= 0 && this.sortOrder.length === 1) {
        var cur = this.sortOrder[0];
        if (!cur.desc) cur.desc = true;
        else this.sortOrder = [];
      } else {
        this.sortOrder = [{ field: field, desc: false }];
      }
    } else {
      if (idx === -1) this.sortOrder.push({ field: field, desc: false });
      else if (!this.sortOrder[idx].desc) this.sortOrder[idx].desc = true;
      else this.sortOrder.splice(idx, 1);
    }
    this._refreshFull();
  };

  P._refreshFull = function () {
    this._rebuild();
    this._renderAll();
  };

  P._reorderColumn = function (dragField, targetField, before) {
    if (dragField === targetField) return;
    var dragCol = this._col(dragField), targetCol = this._col(targetField);
    if (!dragCol || !dragCol.allowReordering) return;
    var order = this.columnOrder;
    var from = order.indexOf(dragField);
    if (from !== -1) order.splice(from, 1);
    var to = order.indexOf(targetField);
    order.splice(before ? to : to + 1, 0, dragField);
    // pindah ke area beku = ikut beku (ala DevExtreme)
    dragCol.fixed = targetCol.fixed;
    this._refreshFull();
  };

  // ---------- menu konteks kolom ----------
  P._showColumnMenu = function (th, field) {
    var self = this;
    var col = this._col(field);
    if (!col) return;
    var isGrouped = this.groups.indexOf(field) !== -1;
    var items = [];
    if (col.allowSorting) {
      items.push({ id: 'asc', label: '▲ ' + M.sortAsc });
      items.push({ id: 'desc', label: '▼ ' + M.sortDesc });
      items.push({ id: 'nosort', label: '✕ ' + M.sortNone });
      items.push({ sep: true });
    }
    if (col.allowGrouping)
      items.push(isGrouped ? { id: 'ungroup', label: '⊟ ' + M.ungroupThis }
        : { id: 'group', label: '⊞ ' + M.groupThis });
    if (col.allowFixing)
      items.push(col.fixed ? { id: 'unfix', label: '🔓 ' + M.unfixCol }
        : { id: 'fix', label: '🔒 ' + M.fixCol });
    if (col.allowHiding)
      items.push({ id: 'hide', label: '👁 ' + M.hideCol });
    items.push({ sep: true });
    items.push({ id: 'rename', label: '✎ ' + M.renameCol });

    this._openMenu(th, items, function (id) {
      switch (id) {
        case 'asc': self.sortOrder = [{ field: field, desc: false }]; self._refreshFull(); break;
        case 'desc': self.sortOrder = [{ field: field, desc: true }]; self._refreshFull(); break;
        case 'nosort':
          self.sortOrder = self.sortOrder.filter(function (s) { return s.field !== field; });
          self._refreshFull(); break;
        case 'group': self.groupBy(field); break;
        case 'ungroup': self.ungroup(field); break;
        case 'fix': col.fixed = true; self._refreshFull(); break;
        case 'unfix': col.fixed = false; self._refreshFull(); break;
        case 'hide': self.showColumn(field, false); break;
        case 'rename': self._renameColumn(field); break;
      }
    });
  };

  P._openMenu = function (anchor, items, onPick) {
    var self = this;
    var html = '';
    items.forEach(function (it, i) {
      if (it.sep) { html += '<div class="dg-menu-sep"></div>'; return; }
      html += '<div class="dg-menu-item" data-i="' + i + '">' + it.label + '</div>';
    });
    this.$menu.innerHTML = html;
    this.$menu.hidden = false;
    this._positionPopup(this.$menu, anchor);
    this.$menu.onclick = function (e) {
      var mi = e.target.closest('.dg-menu-item');
      if (!mi) return;
      self.$menu.hidden = true;
      var it = items[parseInt(mi.dataset.i, 10)];
      if (it && it.id) onPick(it.id);
    };
  };

  P._positionPopup = function (popup, anchor) {
    var rootRect = this.el.getBoundingClientRect();
    var aRect = anchor.getBoundingClientRect();
    var left = aRect.left - rootRect.left;
    var top = aRect.bottom - rootRect.top + 2;
    popup.style.visibility = 'hidden';
    popup.style.left = '0px';
    popup.style.top = '0px';
    var w = popup.offsetWidth || 220;
    if (left + w > this.el.clientWidth - 8) left = Math.max(4, this.el.clientWidth - w - 8);
    popup.style.left = left + 'px';
    popup.style.top = top + 'px';
    popup.style.visibility = '';
  };

  P._renameColumn = function (field) {
    var self = this;
    var col = this._col(field);
    var th = this.$thead.querySelector('th[data-field="' + CSS.escape(field) + '"] .dg-h-caption');
    if (!th || !col) return;
    var input = document.createElement('input');
    input.className = 'dg-rename-input';
    input.value = col.caption;
    th.textContent = '';
    th.appendChild(input);
    input.focus();
    input.select();
    function commit() {
      var v = input.value.trim();
      if (v) col.caption = v;
      self._renderHeader();
      self._renderGroupPanel();
      self._renderBody();
    }
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') commit();
      else if (e.key === 'Escape') self._renderHeader();
      e.stopPropagation();
    });
    input.addEventListener('blur', commit);
    input.addEventListener('click', function (e) { e.stopPropagation(); });
  };

  // ---------- filter op menu & header filter ----------
  P._showOpMenu = function (btn) {
    var self = this;
    var field = btn.dataset.field;
    var col = this._col(field);
    if (!col) return;
    var isNum = col.dataType === 'number' || col.dataType === 'date';
    var ops = isNum ? OPS.numeric : OPS.string;
    var items = ops.map(function (o) {
      return { id: o.op, label: '<b class="dg-op-sym">' + o.sym + '</b> ' + o.label };
    });
    this._openMenu(btn, items, function (op) {
      var cur = self.filters[field] || { value: '' };
      cur.op = op;
      self.filters[field] = cur;
      self._renderHeader();
      if (cur.value) { self.page = 0; self._refresh(); }
    });
  };

  P._showHeaderFilter = function (btn) {
    var self = this;
    var field = btn.dataset.field;
    var col = this._col(field);
    if (!col) return;

    // kolom tanggal: daftar hierarkis tahun -> bulan (ala DevExtreme)
    if (col.dataType === 'date') return this._showDateHeaderFilter(btn, field, col);

    // nilai unik (dari data penuh, dibatasi 1000)
    var counts = new Map();
    for (var i = 0; i < this.data.length; i++) {
      var fv = this.formatValue(col, this._val(this.data[i], col)) || '(kosong)';
      counts.set(fv, (counts.get(fv) || 0) + 1);
      if (counts.size > 1000) break;
    }
    var values = Array.from(counts.keys()).sort(function (a, b) {
      return String(a).localeCompare(String(b), undefined, { numeric: true });
    });
    var current = this.headerFilters[field];

    var html = '<div class="dg-hf-search"><input type="text" placeholder="' + M.search + '"></div>' +
      '<label class="dg-hf-item dg-hf-all"><input type="checkbox" class="dg-hf-selall" ' +
      (!current ? 'checked' : '') + '> ' + M.selectAll + '</label>' +
      '<div class="dg-hf-list">';
    values.forEach(function (v, i) {
      var checked = !current || current.has(v);
      html += '<label class="dg-hf-item" data-v="' + esc(v) + '">' +
        '<input type="checkbox" class="dg-hf-cb" data-i="' + i + '" ' + (checked ? 'checked' : '') + '> ' +
        '<span>' + esc(v) + '</span><em>' + counts.get(v).toLocaleString(this.locale) + '</em></label>';
    }, this);
    html += '</div><div class="dg-hf-actions">' +
      '<button class="dg-btn dg-hf-clear">' + M.clear + '</button>' +
      '<span class="dg-hf-spacer"></span>' +
      '<button class="dg-btn dg-hf-cancel">' + M.cancel + '</button>' +
      '<button class="dg-btn dg-primary dg-hf-ok">' + M.ok + '</button></div>';

    var dd = this.$dropdown;
    dd.innerHTML = html;
    dd.hidden = false;
    dd.onclick = null;   // bersihkan handler milik filter tanggal
    dd.onchange = null;
    this._positionPopup(dd, btn);

    var searchBox = dd.querySelector('.dg-hf-search input');
    searchBox.addEventListener('input', function () {
      var q = searchBox.value.toLowerCase();
      var items = dd.querySelectorAll('.dg-hf-list .dg-hf-item');
      for (var i = 0; i < items.length; i++)
        items[i].style.display = items[i].dataset.v.toLowerCase().indexOf(q) === -1 ? 'none' : '';
    });
    dd.querySelector('.dg-hf-selall').addEventListener('change', function (e) {
      var cbs = dd.querySelectorAll('.dg-hf-cb');
      for (var i = 0; i < cbs.length; i++) cbs[i].checked = e.target.checked;
    });
    dd.querySelector('.dg-hf-ok').addEventListener('click', function () {
      var cbs = dd.querySelectorAll('.dg-hf-cb');
      var set = new Set();
      var all = true;
      for (var i = 0; i < cbs.length; i++) {
        if (cbs[i].checked) set.add(values[parseInt(cbs[i].dataset.i, 10)]);
        else all = false;
      }
      if (all) delete self.headerFilters[field];
      else self.headerFilters[field] = set;
      dd.hidden = true;
      self.page = 0;
      self._rebuild();
      self._renderAll();
    });
    dd.querySelector('.dg-hf-cancel').addEventListener('click', function () { dd.hidden = true; });
    dd.querySelector('.dg-hf-clear').addEventListener('click', function () {
      delete self.headerFilters[field];
      dd.hidden = true;
      self.page = 0;
      self._rebuild();
      self._renderAll();
    });
  };

  // Header filter khusus tanggal: tree 3 tingkat tahun -> bulan -> tanggal
  // (ala DevExtreme), dibangun dari data yang ada. Kunci filter yang disimpan:
  // "YYYY-M-D" (M = indeks bulan 0-11, D = tanggal 1-31) atau "(kosong)".
  P._showDateHeaderFilter = function (btn, field, col) {
    var self = this;

    // hitung jumlah baris per tahun -> bulan -> tanggal
    var years = new Map();
    var emptyCount = 0;
    for (var i = 0; i < this.data.length; i++) {
      var v = this._val(this.data[i], col);
      if (!(v instanceof Date) || isNaN(v)) { emptyCount++; continue; }
      var y = v.getFullYear(), m = v.getMonth(), d = v.getDate();
      if (!years.has(y)) years.set(y, new Map());
      var mm = years.get(y);
      if (!mm.has(m)) mm.set(m, new Map());
      mm.get(m).set(d, (mm.get(m).get(d) || 0) + 1);
    }
    var yearList = Array.from(years.keys()).sort(function (a, b) { return a - b; });
    var current = this.headerFilters[field]; // Set kunci, atau undefined = semua
    function monthName(m) {
      return dateFmt(self.locale, { month: 'long' }).format(new Date(2000, m, 1));
    }
    function has(key) { return !current || current.has(key); }

    var html = '<div class="dg-hf-search"><input type="text" placeholder="' + M.search + '"></div>' +
      '<label class="dg-hf-item dg-hf-all"><input type="checkbox" class="dg-hf-selall" ' +
      (!current ? 'checked' : '') + '> ' + M.selectAll + '</label>' +
      '<div class="dg-hf-list">';

    yearList.forEach(function (y) {
      var months = Array.from(years.get(y).keys()).sort(function (a, b) { return a - b; });
      var yTotal = 0;
      months.forEach(function (m) {
        years.get(y).get(m).forEach(function (cnt) { yTotal += cnt; });
      });
      html += '<div class="dg-hf-item dg-hf-year" data-v="' + y + '" data-y="' + y + '">' +
        '<span class="dg-hf-arrow">▶</span>' +
        '<input type="checkbox" class="dg-hf-ycb" data-y="' + y + '">' +
        '<span>' + y + '</span><em>' + yTotal.toLocaleString(self.locale) + '</em></div>';
      html += '<div class="dg-hf-months" data-y="' + y + '" hidden>';
      months.forEach(function (m) {
        var dayMap = years.get(y).get(m);
        var days = Array.from(dayMap.keys()).sort(function (a, b) { return a - b; });
        var mTotal = 0;
        days.forEach(function (d) { mTotal += dayMap.get(d); });
        var ym = y + '-' + m;
        var mv = y + ' ' + monthName(m);
        html += '<div class="dg-hf-item dg-hf-month" data-v="' + mv + '" data-ym="' + ym + '">' +
          '<span class="dg-hf-arrow">▶</span>' +
          '<input type="checkbox" class="dg-hf-mcb" data-y="' + y + '" data-ym="' + ym + '">' +
          '<span>' + monthName(m) + '</span><em>' + mTotal.toLocaleString(self.locale) + '</em></div>';
        html += '<div class="dg-hf-days" data-ym="' + ym + '" hidden>';
        days.forEach(function (d) {
          var key = ym + '-' + d;
          html += '<label class="dg-hf-item dg-hf-day" data-v="' + mv + ' ' + d + '">' +
            '<input type="checkbox" class="dg-hf-dcb" data-key="' + key + '" data-y="' + y + '" data-ym="' + ym + '" ' +
            (has(key) ? 'checked' : '') + '> <span>' + d + '</span>' +
            '<em>' + dayMap.get(d).toLocaleString(self.locale) + '</em></label>';
        });
        html += '</div>';
      });
      html += '</div>';
    });
    if (emptyCount) {
      html += '<label class="dg-hf-item" data-v="(kosong)">' +
        '<input type="checkbox" class="dg-hf-dcb" data-key="(kosong)" ' + (has('(kosong)') ? 'checked' : '') + '> ' +
        '<span>(kosong)</span><em>' + emptyCount.toLocaleString(self.locale) + '</em></label>';
    }
    html += '</div><div class="dg-hf-actions">' +
      '<button class="dg-btn dg-hf-clear">' + M.clear + '</button>' +
      '<span class="dg-hf-spacer"></span>' +
      '<button class="dg-btn dg-hf-cancel">' + M.cancel + '</button>' +
      '<button class="dg-btn dg-primary dg-hf-ok">' + M.ok + '</button></div>';

    var dd = this.$dropdown;
    dd.innerHTML = html;
    dd.hidden = false;
    this._positionPopup(dd, btn);

    // status induk (bulan & tahun) dihitung dari daun (checkbox tanggal)
    function syncParents() {
      var mcbs = dd.querySelectorAll('.dg-hf-mcb');
      for (var i = 0; i < mcbs.length; i++) {
        var dcbs = dd.querySelectorAll('.dg-hf-dcb[data-ym="' + mcbs[i].dataset.ym + '"]');
        var n = 0;
        for (var j = 0; j < dcbs.length; j++) if (dcbs[j].checked) n++;
        mcbs[i].checked = n > 0 && n === dcbs.length;
        mcbs[i].indeterminate = n > 0 && n < dcbs.length;
      }
      var ycbs = dd.querySelectorAll('.dg-hf-ycb');
      for (var k = 0; k < ycbs.length; k++) {
        var all = dd.querySelectorAll('.dg-hf-dcb[data-y="' + ycbs[k].dataset.y + '"]');
        var c = 0;
        for (var l = 0; l < all.length; l++) if (all[l].checked) c++;
        ycbs[k].checked = c > 0 && c === all.length;
        ycbs[k].indeterminate = c > 0 && c < all.length;
      }
    }
    syncParents();

    // klik baris tahun/bulan (selain checkbox) = buka/tutup anaknya
    // pakai assignment (bukan addEventListener) agar tidak menumpuk antar pembukaan
    dd.onclick = function (e) {
      if (e.target.type === 'checkbox') return;
      var yrow = e.target.closest('.dg-hf-year');
      if (yrow) {
        var box = dd.querySelector('.dg-hf-months[data-y="' + yrow.dataset.y + '"]');
        box.hidden = !box.hidden;
        yrow.querySelector('.dg-hf-arrow').classList.toggle('dg-open', !box.hidden);
        return;
      }
      var mrow = e.target.closest('.dg-hf-month');
      if (mrow) {
        var dbox = dd.querySelector('.dg-hf-days[data-ym="' + mrow.dataset.ym + '"]');
        dbox.hidden = !dbox.hidden;
        mrow.querySelector('.dg-hf-arrow').classList.toggle('dg-open', !dbox.hidden);
      }
    };

    dd.onchange = function (e) {
      var t = e.target;
      if (t.classList.contains('dg-hf-ycb')) {
        var dy = dd.querySelectorAll('.dg-hf-dcb[data-y="' + t.dataset.y + '"]');
        for (var i = 0; i < dy.length; i++) dy[i].checked = t.checked;
        syncParents();
      } else if (t.classList.contains('dg-hf-mcb')) {
        var dm = dd.querySelectorAll('.dg-hf-dcb[data-ym="' + t.dataset.ym + '"]');
        for (var j = 0; j < dm.length; j++) dm[j].checked = t.checked;
        syncParents();
      } else if (t.classList.contains('dg-hf-dcb')) {
        syncParents();
      } else if (t.classList.contains('dg-hf-selall')) {
        var all = dd.querySelectorAll('.dg-hf-dcb');
        for (var k = 0; k < all.length; k++) all[k].checked = t.checked;
        syncParents();
      }
    };

    var searchBox = dd.querySelector('.dg-hf-search input');
    searchBox.addEventListener('input', function () {
      var q = searchBox.value.toLowerCase();
      var items = dd.querySelectorAll('.dg-hf-list .dg-hf-item');
      for (var i = 0; i < items.length; i++)
        items[i].style.display = items[i].dataset.v.toLowerCase().indexOf(q) === -1 ? 'none' : '';
      // saat mencari, buka semua cabang agar hasilnya terlihat
      var boxes = dd.querySelectorAll('.dg-hf-months, .dg-hf-days');
      for (var j = 0; j < boxes.length; j++) boxes[j].hidden = q === '';
    });

    dd.querySelector('.dg-hf-ok').addEventListener('click', function () {
      var dcbs = dd.querySelectorAll('.dg-hf-dcb');
      var set = new Set();
      var all = true;
      for (var i = 0; i < dcbs.length; i++) {
        if (dcbs[i].checked) set.add(dcbs[i].dataset.key);
        else all = false;
      }
      if (all) delete self.headerFilters[field];
      else self.headerFilters[field] = set;
      dd.hidden = true;
      self.page = 0;
      self._rebuild();
      self._renderAll();
    });
    dd.querySelector('.dg-hf-cancel').addEventListener('click', function () { dd.hidden = true; });
    dd.querySelector('.dg-hf-clear').addEventListener('click', function () {
      delete self.headerFilters[field];
      dd.hidden = true;
      self.page = 0;
      self._rebuild();
      self._renderAll();
    });
  };

  // ---------- column chooser ----------
  P._toggleChooser = function (btn) {
    var self = this;
    if (!this.$chooser.hidden) { this.$chooser.hidden = true; return; }
    var html = '<div class="dg-chooser-title">' + M.columnChooser + '</div>';
    this.columns.forEach(function (col) {
      if (col._groupHidden) return;
      html += '<label class="dg-chooser-item">' +
        '<input type="checkbox" data-field="' + esc(col.dataField) + '" ' +
        (col.visible ? 'checked' : '') + (col.allowHiding ? '' : ' disabled') + '> ' +
        esc(col.caption) + '</label>';
    });
    this.$chooser.innerHTML = html;
    this.$chooser.hidden = false;
    this._positionPopup(this.$chooser, btn);
    this.$chooser.onchange = function (e) {
      var cb = e.target;
      if (!cb.dataset.field) return;
      self.showColumn(cb.dataset.field, cb.checked);
    };
  };

  P._showExportMenu = function (btn) {
    var self = this;
    var hasSel = this.selected.size > 0;
    var items = [
      { id: 'csv-all', label: '📄 ' + M.exportCsvAll },
      { id: 'xls-all', label: '📊 ' + M.exportXlsAll },
    ];
    if (hasSel) {
      items.push({ sep: true });
      items.push({ id: 'csv-sel', label: '📄 ' + M.exportCsvSel });
      items.push({ id: 'xls-sel', label: '📊 ' + M.exportXlsSel });
    }
    this._openMenu(btn, items, function (id) {
      var onlySel = id.indexOf('sel') !== -1;
      if (id.indexOf('csv') === 0) self.exportCSV(onlySel);
      else self.exportExcel(onlySel);
    });
  };

  // ---------- export ----------
  P._exportRows = function (onlySelected) {
    var self = this;
    var rows = this._filteredRows;
    if (onlySelected)
      rows = rows.filter(function (r) { return self.selected.has(self._key(r)); });
    return rows;
  };

  P._download = function (blob, name) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 100);
  };

  P.exportCSV = function (onlySelected) {
    var self = this;
    var vcols = this._visibleCols();
    var rows = this._exportRows(onlySelected);
    var lines = [vcols.map(function (c) { return '"' + c.caption.replace(/"/g, '""') + '"'; }).join(';')];
    rows.forEach(function (r) {
      lines.push(vcols.map(function (c) {
        var txt = self.formatValue(c, self._val(r, c));
        return '"' + String(txt).replace(/"/g, '""') + '"';
      }).join(';'));
    });
    var blob = new Blob(['\uFEFF' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    this._download(blob, 'datagrid-export.csv');
  };

  P.exportExcel = function (onlySelected) {
    var self = this;
    var vcols = this._visibleCols();
    var rows = this._exportRows(onlySelected);
    var xml = '<?xml version="1.0"?><?mso-application progid="Excel.Sheet"?>' +
      '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" ' +
      'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">' +
      '<Styles><Style ss:ID="h"><Font ss:Bold="1"/><Interior ss:Color="#EEEEEE" ss:Pattern="Solid"/></Style></Styles>' +
      '<Worksheet ss:Name="Data"><Table>';
    xml += '<Row>' + vcols.map(function (c) {
      return '<Cell ss:StyleID="h"><Data ss:Type="String">' + esc(c.caption) + '</Data></Cell>';
    }).join('') + '</Row>';
    rows.forEach(function (r) {
      xml += '<Row>' + vcols.map(function (c) {
        var raw = self._val(r, c);
        if (c.dataType === 'number' && typeof raw === 'number')
          return '<Cell><Data ss:Type="Number">' + raw + '</Data></Cell>';
        return '<Cell><Data ss:Type="String">' + esc(self.formatValue(c, raw)) + '</Data></Cell>';
      }).join('') + '</Row>';
    });
    xml += '</Table></Worksheet></Workbook>';
    var blob = new Blob([xml], { type: 'application/vnd.ms-excel' });
    this._download(blob, 'datagrid-export.xls');
  };

  // ---------- API publik ----------
  P.groupBy = function (field) {
    var col = this._col(field);
    if (!col || !col.allowGrouping || this.groups.indexOf(field) !== -1) return;
    this.groups.push(field);
    this.groupDirs[field] = 1;
    col._groupHidden = true;
    this._collapsedDefault = false;
    this._collapsedEx.clear();
    this.page = 0;
    this._refreshFull();
  };

  P.ungroup = function (field) {
    var idx = this.groups.indexOf(field);
    if (idx === -1) return;
    this.groups.splice(idx, 1);
    delete this.groupDirs[field];
    var col = this._col(field);
    if (col) col._groupHidden = false;
    this._collapsedEx.clear();
    this.page = 0;
    this._refreshFull();
  };

  P.clearGrouping = function () {
    var self = this;
    this.groups.slice().forEach(function (f) { self.ungroup(f); });
  };

  P.expandAll = function () {
    if (!this.groups.length) return;
    this._collapsedDefault = false;
    this._collapsedEx.clear();
    this._refreshFull();
  };

  P.collapseAll = function () {
    if (!this.groups.length) return;
    this._collapsedDefault = true;
    this._collapsedEx.clear();
    this.page = 0;
    this._refreshFull();
  };

  P.selectAll = function () {
    var self = this;
    this._filteredRows.forEach(function (r) { self.selected.add(self._key(r)); });
    this._afterSelection();
  };

  P.clearSelection = function () {
    this.selected.clear();
    this._selAnchor = null;
    this._afterSelection();
  };

  P.getSelectedRowKeys = function () {
    return Array.from(this.selected);
  };

  P.getSelectedRowsData = function () {
    var self = this;
    return this.data.filter(function (r) { return self.selected.has(self._key(r)); });
  };

  P.showColumn = function (field, visible) {
    var col = this._col(field);
    if (!col) return;
    col.visible = visible !== false;
    this._refreshFull();
  };

  P.addColumn = function (def) {
    var col = this._normCol(def);
    this.columns.push(col);
    this.columnOrder.push(col.dataField);
    this._inferTypes();
    this._addedCols.push(col.dataField);
    this._refreshFull();
  };

  P.removeColumn = function (field) {
    var self = this;
    var idx = this.columns.findIndex(function (c) { return c.dataField === field; });
    if (idx === -1) return;
    this.columns.splice(idx, 1);
    this.columnOrder = this.columnOrder.filter(function (f) { return f !== field; });
    this.sortOrder = this.sortOrder.filter(function (s) { return s.field !== field; });
    delete this.filters[field];
    delete this.headerFilters[field];
    var gIdx = this.groups.indexOf(field);
    if (gIdx !== -1) { this.groups.splice(gIdx, 1); delete this.groupDirs[field]; }
    this._addedCols = this._addedCols.filter(function (f) { return f !== field; });
    this._refreshFull();
  };

  P.setScrollMode = function (mode) {
    this.scrollMode = mode;
    this.page = 0;
    this._loaded = 60;
    this.$body.scrollTop = 0;
    this._refreshFull();
  };

  P.searchByText = function (text) {
    this.searchText = text || '';
    var input = this.el.querySelector('.dg-search-input');
    if (input) input.value = this.searchText;
    this.page = 0;
    this._refresh();
  };

  P.refresh = function () {
    this._refreshFull();
  };

  global.DataGrid = DataGrid;
})(window);
