/*
 * 表格视图（Canvas 自绘）：列标/行号表头、合并单元格、差异红/橙、工作表标签。
 *
 * 结构与桌面版 GridView + SheetTabBar 对齐，使用原生滚动条 +
 * 固定在视口上的 canvas（滚动时反相平移），因此 5 万行也不会创建 DOM 节点。
 */

import { el } from './dom.js';

const HEADER_W = 52;
const HEADER_H = 24;
const MIN_COL = 24;
const MIN_ROW = 18;

export class GridView {
  constructor(container, { onCellClick = null, onSheetSelected = null } = {}) {
    this.container = container;
    this.onCellClick = onCellClick;
    this.onSheetSelected = onSheetSelected;
    this.workbook = null;
    this.comparison = null;
    this.side = 'left';
    this.showTabs = true;
    this.sheet = null;
    this.sheetName = null;
    this.diffs = [];
    this.diffMap = new Map();
    this.onlyDiffs = false;
    this.zoom = 1;
    this.selected = null;
    this.visibleRows = [];
    this.rowOffsets = [0];
    this.colOffsets = [0];
    this.anchors = new Map();
    this.covered = new Set();
    this.offsetX = 0;
    this.offsetY = 0;
    this._resizeMode = null;
    this._resizeIndex = -1;
    this._resizeStart = 0;
    this._resizeOriginal = 0;
    this._theme = null;

    this.scroll = el('div', { class: 'grid-scroll' });
    this.space = el('div', { class: 'grid-space', 'aria-hidden': 'true' });
    this.canvas = el('canvas', { class: 'grid-canvas' });
    this.scroll.append(this.space, this.canvas);
    this.tabs = el('div', { class: 'sheet-tabs' });
    this.root = el('div', { class: 'grid' });
    this.tabLeft = el('button', { class: 'sheet-arrow', type: 'button', 'aria-label': '向左滚动工作表' }, '‹');
    this.tabRight = el('button', { class: 'sheet-arrow', type: 'button', 'aria-label': '向右滚动工作表' }, '›');
    this.tabStrip = el('div', { class: 'sheet-strip' });
    this.tabs.append(this.tabLeft, this.tabStrip, this.tabRight);
    this.root.append(this.scroll, this.tabs);
    this.container.append(this.root);
    this.tabLeft.addEventListener('click', () => this.scrollTabs(-1));
    this.tabRight.addEventListener('click', () => this.scrollTabs(1));
    this.tabStrip.addEventListener('wheel', (event) => {
      if (this.tabStrip.scrollWidth <= this.tabStrip.clientWidth) return;
      event.preventDefault();
      this.tabStrip.scrollLeft += event.deltaY || event.deltaX;
      this._updateTabArrows();
    }, { passive: false });
    this._bindPointerDragTabs();

    this.scroll.addEventListener('scroll', () => {
      this.offsetX = this.scroll.scrollLeft;
      this.offsetY = this.scroll.scrollTop;
      this._positionCanvas();
      this.draw();
    });
    this.scroll.addEventListener('wheel', (event) => {
      if (event.shiftKey) {
        event.preventDefault();
        this.scroll.scrollLeft += event.deltaY;
      }
    }, { passive: false });
    this.canvas.addEventListener('pointerdown', (event) => this._onPointerDown(event));
    this.canvas.addEventListener('pointermove', (event) => this._onPointerMove(event));
    this.canvas.addEventListener('pointerup', (event) => this._onPointerUp(event));
    this.canvas.addEventListener('pointerleave', (event) => this._onPointerUp(event));
    window.addEventListener('resize', () => this._resize());
    if (globalThis.ResizeObserver) {
      this._resizeObserver = new ResizeObserver(() => {
        if (this._resizePending) return;
        this._resizePending = true;
        requestAnimationFrame(() => {
          this._resizePending = false;
          this._resize();
        });
      });
      this._resizeObserver.observe(this.scroll);
    }
    this._resize();
  }

  /* ---------------------------------------------------------------- 数据 */

  load(workbook, comparison, side, { showTabs = true } = {}) {
    this.workbook = workbook;
    this.comparison = comparison;
    this.side = side === 'right' ? 'right' : 'left';
    this.showTabs = Boolean(showTabs);
    this.tabs.style.display = showTabs ? '' : 'none';
    this.onlyDiffs = false;
    this.zoom = 1;
    this._renderTabs();
    const names = this.sheetNames();
    const initial = names.length ? names[0] : null;
    this.showSheet(initial);
  }

  sheetNames() {
    if (this.comparison && this.comparison.sheets.length) {
      return this.comparison.sheets.map((sheet) => sheet.name);
    }
    if (this.workbook) return this.workbook.sheets.map((sheet) => sheet.name);
    return [];
  }

  currentSheet() {
    return this.sheetName;
  }

  showSheet(name, { emit = false } = {}) {
    this.sheetName = name;
    this.sheet = this.workbook && name ? this.workbook.sheet(name) : null;
    this.diffs = [];
    if (this.comparison) {
      const sheetComparison = this.comparison.sheets.find((item) => item.name === name);
      if (sheetComparison) this.diffs = sheetComparison.diffs;
    }
    this.diffMap = new Map();
    for (const diff of this.diffs) {
      const location = this.side === 'left' ? diff.left : diff.right;
      if (location && location.kind === 'cell' && location.row !== null && location.row !== undefined) {
        this.diffMap.set(`${location.row},${location.col}`, diff);
      }
    }
    this._buildAnchors();
    this.selected = null;
    this.scroll.scrollTop = 0;
    this.scroll.scrollLeft = 0;
    this.offsetX = 0;
    this.offsetY = 0;
    this._rebuildGeometry();
    this._updateTabs();
    if (emit && this.onSheetSelected) this.onSheetSelected(name);
  }

  setOnlyDiffs(enabled) {
    this.onlyDiffs = Boolean(enabled);
    this._rebuildGeometry();
  }

  setZoom(zoom) {
    const value = Math.max(0.5, Math.min(2.5, Number(zoom) || 1));
    if (Math.abs(value - this.zoom) < 1e-6) return;
    const hRatio = this._ratio(this.scroll.scrollLeft, this.scroll.scrollWidth - this.scroll.clientWidth);
    const vRatio = this._ratio(this.scroll.scrollTop, this.scroll.scrollHeight - this.scroll.clientHeight);
    this.zoom = value;
    this._rebuildGeometry();
    this.scroll.scrollLeft = hRatio * (this.scroll.scrollWidth - this.scroll.clientWidth);
    this.scroll.scrollTop = vRatio * (this.scroll.scrollHeight - this.scroll.clientHeight);
  }

  reveal(diff) {
    const location = this.side === 'left' ? diff.left : diff.right;
    if (!location || location.kind !== 'cell') {
      if (location && location.kind === 'sheet') this.showSheet(location.sheet);
      return false;
    }
    if (location.sheet && location.sheet !== this.sheetName) this.showSheet(location.sheet);
    if (!this.sheet) return false;
    const row = Number(location.row);
    const col = Number(location.col);
    if (!Number.isFinite(row) || !Number.isFinite(col)) return false;
    let displayIndex = this._rowDisplay.get(row);
    if (displayIndex === undefined) {
      if (!this.visibleRows.length) return false;
      displayIndex = 0;
      let best = Infinity;
      this.visibleRows.forEach((value, index) => {
        const distance = Math.abs(value - row);
        if (distance < best) {
          best = distance;
          displayIndex = index;
        }
      });
    }
    this.selected = [row, col];
    const x = HEADER_W + this.colOffsets[col];
    const y = HEADER_H + this.rowOffsets[displayIndex];
    this.scroll.scrollLeft = Math.max(0, x - this.scroll.clientWidth / 2);
    this.scroll.scrollTop = Math.max(0, y - this.scroll.clientHeight / 2);
    this.draw();
    return true;
  }

  /* ---------------------------------------------------------------- 几何 */

  _ratio(value, max) {
    return max > 0 ? value / max : 0;
  }

  _buildAnchors() {
    this.anchors.clear();
    this.covered.clear();
    const sheet = this.sheet;
    if (!sheet) return;
    for (const [row, col, rowSpan, colSpan] of sheet.merged) {
      if (rowSpan <= 0 || colSpan <= 0 || row >= sheet.nrows || col >= sheet.ncols) continue;
      const rs = Math.min(rowSpan, sheet.nrows - row);
      const cs = Math.min(colSpan, sheet.ncols - col);
      this.anchors.set(`${row},${col}`, [rs, cs]);
      for (let r = row; r < row + rs; r += 1) {
        for (let c = col; c < col + cs; c += 1) {
          if (r !== row || c !== col) this.covered.add(`${r},${c}`);
        }
      }
    }
  }

  _rebuildGeometry() {
    const sheet = this.sheet;
    if (!sheet) {
      this.visibleRows = [];
      this.rowOffsets = [0];
      this.colOffsets = [0];
      this.space.style.width = '100%';
      this.space.style.height = '100%';
      this.draw();
      return;
    }
    const z = this.zoom;
    this.colOffsets = new Array(sheet.ncols + 1);
    this.colOffsets[0] = 0;
    for (let col = 0; col < sheet.ncols; col += 1) {
      const width = sheet.colWidths.has(col) ? sheet.colWidths.get(col) : sheet.defaultColWidth;
      this.colOffsets[col + 1] = this.colOffsets[col] + width * z;
    }
    if (this.onlyDiffs) {
      const rows = new Set();
      for (const diff of this.diffs) {
        const location = this.side === 'left' ? diff.left : diff.right;
        if (!location || location.kind !== 'cell' || location.row === null || location.row === undefined) continue;
        for (const candidate of [location.row - 1, location.row, location.row + 1]) {
          if (candidate >= 0 && candidate < sheet.nrows) rows.add(candidate);
        }
      }
      this.visibleRows = [...rows].sort((a, b) => a - b);
    } else {
      this.visibleRows = Array.from({ length: sheet.nrows }, (_, index) => index);
    }
    this._rowDisplay = new Map(this.visibleRows.map((row, index) => [row, index]));
    this.rowOffsets = new Array(this.visibleRows.length + 1);
    this.rowOffsets[0] = 0;
    this.visibleRows.forEach((row, index) => {
      const height = sheet.rowHeights.has(row) ? sheet.rowHeights.get(row) : sheet.defaultRowHeight;
      this.rowOffsets[index + 1] = this.rowOffsets[index] + height * z;
    });
    const contentW = HEADER_W + this.colOffsets[sheet.ncols];
    const contentH = HEADER_H + this.rowOffsets[this.visibleRows.length];
    this.space.style.width = `${Math.max(contentW, 1)}px`;
    this.space.style.height = `${Math.max(contentH, 1)}px`;
    this._resize();
    this.draw();
    requestAnimationFrame(() => this._updateTabArrows());
  }

  _resize() {
    const rect = this.scroll.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(rect.width));
    const height = Math.max(1, Math.floor(rect.height));
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this.canvas.width = Math.floor(width * dpr);
    this.canvas.height = Math.floor(height * dpr);
    this._ctx = this.canvas.getContext('2d');
    this._dpr = dpr;
    this._positionCanvas();
    this.draw();
    this._updateTabArrows();
  }

  _positionCanvas() {
    this.canvas.style.transform = `translate(${this.scroll.scrollLeft}px, ${this.scroll.scrollTop}px)`;
  }

  /* ---------------------------------------------------------------- 绘制 */

  _colors() {
    const style = getComputedStyle(this.container);
    const get = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    return {
      bg: get('--grid-bg', '#ffffff'),
      headerBg: get('--grid-header-bg', '#fafafa'),
      headerText: get('--grid-header-text', '#616161'),
      grid: get('--grid-line', '#e0e0e0'),
      text: get('--text', '#1b1b1b'),
      secondary: get('--text-secondary', '#616161'),
      diffBg: get('--diff-bg', '#ffd7d5'),
      diffText: get('--diff-text', '#a11212'),
      diffBorder: get('--diff', '#c62828'),
      warnBg: get('--warn-bg', '#ffe2b8'),
      warnText: get('--warn-text', '#8a3b00'),
      warnBorder: get('--warn', '#e65100'),
      accent: get('--accent', '#0067c0'),
      accentSoft: get('--accent-soft', '#e5f1fb'),
      fontFamily: get('--font-ui', 'Segoe UI, system-ui, sans-serif'),
    };
  }

  draw() {
    if (!this._ctx) return;
    const ctx = this._ctx;
    const dpr = this._dpr;
    const width = this.canvas.width / dpr;
    const height = this.canvas.height / dpr;
    const colors = this._colors();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, width, height);
    const sheet = this.sheet;
    if (!sheet) {
      ctx.fillStyle = colors.secondary;
      ctx.font = `13px ${colors.fontFamily}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(this.workbook ? '工作簿中没有工作表' : '未加载表格', width / 2, height / 2);
      return;
    }
    const z = this.zoom;
    const left = this.offsetX;
    const top = this.offsetY;
    const firstCol = Math.max(0, bisect(this.colOffsets, left) - 1);
    const lastCol = Math.min(sheet.ncols - 1, bisect(this.colOffsets, left + width));
    const firstRow = Math.max(0, bisect(this.rowOffsets, top) - 1);
    const lastRow = Math.min(this.visibleRows.length - 1, bisect(this.rowOffsets, top + height));

    // 单元格背景与边框
    for (let index = firstRow; index <= lastRow; index += 1) {
      const row = this.visibleRows[index];
      const y = HEADER_H + this.rowOffsets[index] - top;
      const h = this.rowOffsets[index + 1] - this.rowOffsets[index];
      for (let col = firstCol; col <= lastCol; col += 1) {
        if (this.covered.has(`${row},${col}`)) continue;
        const span = this.anchors.get(`${row},${col}`);
        if (span) continue;
        const x = HEADER_W + this.colOffsets[col] - left;
        const w = this.colOffsets[col + 1] - this.colOffsets[col];
        this._paintCell(ctx, colors, row, col, x, y, w, h, sheet.get(row, col));
      }
    }
    // 合并单元格整体绘制
    for (const [row, col, span] of this._visibleAnchors(firstRow, lastRow, firstCol, lastCol)) {
      const displayIndex = this._rowDisplay.get(row);
      if (displayIndex === undefined) continue;
      const x = HEADER_W + this.colOffsets[col] - left;
      const y = HEADER_H + this.rowOffsets[displayIndex] - top;
      const w = this.colOffsets[Math.min(sheet.ncols, col + span[1])] - this.colOffsets[col];
      const h = this.rowOffsets[Math.min(this.visibleRows.length, displayIndex + span[0])] - this.rowOffsets[displayIndex];
      const diff = this.diffMap.get(`${row},${col}`);
      if (diff) {
        const uncertain = diff.severity === 'uncertain' || diff.needsReview;
        ctx.fillStyle = uncertain ? colors.warnBg : colors.diffBg;
        ctx.fillRect(x, y, w, h);
        ctx.strokeStyle = uncertain ? colors.warnBorder : colors.diffBorder;
        ctx.lineWidth = 2;
        ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
      } else {
        ctx.fillStyle = colors.bg;
        ctx.fillRect(x, y, w, h);
      }
      ctx.strokeStyle = colors.grid;
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
      this._paintText(ctx, colors, sheet.get(row, col), x, y, w, h, diff);
    }

    // 网格线（视口内）
    ctx.strokeStyle = colors.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let col = firstCol; col <= Math.min(sheet.ncols, lastCol + 1); col += 1) {
      const x = Math.floor(HEADER_W + this.colOffsets[col] - left) + 0.5;
      ctx.moveTo(x, HEADER_H);
      ctx.lineTo(x, height);
    }
    for (let index = firstRow; index <= Math.min(this.visibleRows.length, lastRow + 1); index += 1) {
      const y = Math.floor(HEADER_H + this.rowOffsets[index] - top) + 0.5;
      ctx.moveTo(HEADER_W, y);
      ctx.lineTo(width, y);
    }
    ctx.stroke();

    // 表头
    ctx.fillStyle = colors.headerBg;
    ctx.fillRect(0, 0, width, HEADER_H);
    ctx.fillRect(0, 0, HEADER_W, height);
    ctx.strokeStyle = colors.grid;
    ctx.beginPath();
    ctx.moveTo(0, HEADER_H + 0.5);
    ctx.lineTo(width, HEADER_H + 0.5);
    ctx.moveTo(HEADER_W + 0.5, 0);
    ctx.lineTo(HEADER_W + 0.5, height);
    ctx.stroke();

    ctx.font = `11px ${colors.fontFamily}`;
    ctx.fillStyle = colors.headerText;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (let col = firstCol; col <= lastCol; col += 1) {
      const x = HEADER_W + this.colOffsets[col] - left;
      const w = this.colOffsets[col + 1] - this.colOffsets[col];
      if (x + w < HEADER_W) continue;
      ctx.fillText(columnLetter(col), x + w / 2, HEADER_H / 2);
    }
    for (let index = firstRow; index <= lastRow; index += 1) {
      const y = HEADER_H + this.rowOffsets[index] - top;
      const h = this.rowOffsets[index + 1] - this.rowOffsets[index];
      if (y + h < HEADER_H) continue;
      const row = this.visibleRows[index];
      const selectedRow = this.selected && this.selected[0] === row;
      if (selectedRow) {
        ctx.fillStyle = colors.accentSoft;
        ctx.fillRect(0, y, HEADER_W, h);
        ctx.fillStyle = colors.headerText;
      }
      ctx.fillText(String(row + 1), HEADER_W / 2, y + h / 2);
    }
    // 选中列标高亮
    if (this.selected) {
      const col = this.selected[1];
      const x = HEADER_W + this.colOffsets[col] - left;
      const w = this.colOffsets[col + 1] - this.colOffsets[col];
      ctx.fillStyle = colors.accentSoft;
      ctx.fillRect(x, 0, w, HEADER_H);
      ctx.fillStyle = colors.headerText;
      ctx.fillText(columnLetter(col), x + w / 2, HEADER_H / 2);
    }
  }

  _visibleAnchors(firstRow, lastRow, firstCol, lastCol) {
    const out = [];
    for (const [key, span] of this.anchors) {
      const [row, col] = key.split(',').map(Number);
      const displayIndex = this._rowDisplay.get(row);
      if (displayIndex === undefined) continue;
      if (displayIndex < firstRow - span[0] || displayIndex > lastRow + 1) continue;
      if (col < firstCol - span[1] || col > lastCol + 1) continue;
      out.push([row, col, span]);
    }
    return out;
  }

  _paintCell(ctx, colors, row, col, x, y, w, h, cell) {
    const diff = this.diffMap.get(`${row},${col}`);
    if (diff) {
      const uncertain = diff.severity === 'uncertain' || diff.needsReview;
      ctx.fillStyle = uncertain ? colors.warnBg : colors.diffBg;
      ctx.fillRect(x, y, w, h);
    } else if (cell.style && cell.style.fill) {
      ctx.fillStyle = cell.style.fill;
      ctx.fillRect(x, y, w, h);
    }
    if (diff) {
      const uncertain = diff.severity === 'uncertain' || diff.needsReview;
      ctx.strokeStyle = uncertain ? colors.warnBorder : colors.diffBorder;
      ctx.lineWidth = 2;
      ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
      ctx.fillStyle = uncertain ? colors.warnBorder : colors.diffBorder;
      ctx.fillRect(x + 1, y + 1, 3, h - 2);
    }
    if (this.selected && this.selected[0] === row && this.selected[1] === col) {
      ctx.strokeStyle = colors.accent;
      ctx.lineWidth = 2;
      ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
    }
    this._paintText(ctx, colors, cell, x, y, w, h, diff);
  }

  _paintText(ctx, colors, cell, x, y, w, h, diff) {
    const text = cell.display;
    if (!text) return;
    const uncertain = diff && (diff.severity === 'uncertain' || diff.needsReview);
    ctx.fillStyle = diff ? (uncertain ? colors.warnText : colors.diffText) : colors.text;
    const size = Math.max(9, Math.min(22, 12 * this.zoom));
    ctx.font = `${size}px ${colors.fontFamily}`;
    ctx.textBaseline = 'middle';
    const alignment = cell.type === 'number' || cell.type === 'date' ? 'right' : 'left';
    const padding = 5;
    const maxWidth = Math.max(0, w - padding * 2);
    const alignmentX = alignment === 'right' ? x + w - padding : x + padding;
    ctx.textAlign = alignment === 'right' ? 'right' : 'left';
    const lines = cell.style && cell.style.wrap ? String(text).split('\n') : [String(text).replace(/\n/g, ' ')];
    const lineHeight = Math.round(size * 1.35);
    const maxLines = Math.max(1, Math.floor((h - 4) / lineHeight));
    const shownLines = lines.slice(0, maxLines);
    const startY = y + h / 2 - ((shownLines.length - 1) * lineHeight) / 2;
    shownLines.forEach((line, index) => {
      ctx.fillText(fitText(ctx, line, maxWidth), alignmentX, startY + index * lineHeight);
    });
  }

  /* ---------------------------------------------------------------- 交互 */

  _onPointerDown(event) {
    if (!this.sheet) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    // 列宽拖动
    if (y <= HEADER_H && x >= HEADER_W) {
      const col = this._columnBoundaryAt(x);
      if (col !== null) {
        this._resizeMode = 'col';
        this._resizeIndex = col;
        this._resizeStart = x;
        this._resizeOriginal = this._colWidth(col);
        this.canvas.setPointerCapture(event.pointerId);
        event.preventDefault();
        return;
      }
    }
    // 行高拖动
    if (x <= HEADER_W && y >= HEADER_H) {
      const rowBoundary = this._rowBoundaryAt(y);
      if (rowBoundary !== null) {
        this._resizeMode = 'row';
        this._resizeIndex = rowBoundary;
        this._resizeStart = y;
        this._resizeOriginal = this._rowHeight(rowBoundary);
        this.canvas.setPointerCapture(event.pointerId);
        event.preventDefault();
        return;
      }
    }
    const cell = this._cellAt(x, y);
    if (cell) {
      this.selected = cell;
      this.draw();
      if (this.onCellClick) this.onCellClick(cell[0], cell[1]);
    }
  }

  _onPointerMove(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (this._resizeMode === 'col') {
      const delta = x - this._resizeStart;
      const width = Math.max(MIN_COL, this._resizeOriginal + delta);
      this.sheet.colWidths.set(this._resizeIndex, width / this.zoom);
      this._rebuildGeometry();
      return;
    }
    if (this._resizeMode === 'row') {
      const delta = y - this._resizeStart;
      const height = Math.max(MIN_ROW, this._resizeOriginal + delta);
      this.sheet.rowHeights.set(this._resizeIndex, height / this.zoom);
      this._rebuildGeometry();
      return;
    }
    // 边界上显示 resize 光标
    if (y <= HEADER_H && x >= HEADER_W && this._columnBoundaryAt(x) !== null) {
      this.canvas.style.cursor = 'col-resize';
    } else if (x <= HEADER_W && y >= HEADER_H && this._rowBoundaryAt(y) !== null) {
      this.canvas.style.cursor = 'row-resize';
    } else {
      this.canvas.style.cursor = 'default';
    }
  }

  _onPointerUp(event) {
    if (this._resizeMode) {
      this._resizeMode = null;
      this._resizeIndex = -1;
      try {
        this.canvas.releasePointerCapture(event.pointerId);
      } catch {
        /* 已释放 */
      }
      this.draw();
    }
  }

  _columnBoundaryAt(x) {
    const left = this.offsetX;
    for (let col = 0; col < this.sheet.ncols; col += 1) {
      const boundary = HEADER_W + this.colOffsets[col + 1] - left;
      if (Math.abs(x - boundary) <= 4) return col;
      if (boundary > x + 4) break;
    }
    return null;
  }

  _rowBoundaryAt(y) {
    const top = this.offsetY;
    for (let index = 0; index < this.visibleRows.length; index += 1) {
      const boundary = HEADER_H + this.rowOffsets[index + 1] - top;
      if (Math.abs(y - boundary) <= 4) return this.visibleRows[index];
      if (boundary > y + 4) break;
    }
    return null;
  }

  _colWidth(col) {
    return (this.sheet.colWidths.has(col) ? this.sheet.colWidths.get(col) : this.sheet.defaultColWidth) * this.zoom;
  }

  _rowHeight(row) {
    return (this.sheet.rowHeights.has(row) ? this.sheet.rowHeights.get(row) : this.sheet.defaultRowHeight) * this.zoom;
  }

  _cellAt(x, y) {
    if (!this.sheet || x < HEADER_W || y < HEADER_H) return null;
    const contentX = x - HEADER_W + this.offsetX;
    const contentY = y - HEADER_H + this.offsetY;
    const col = bisect(this.colOffsets, contentX) - 1;
    const displayIndex = bisect(this.rowOffsets, contentY) - 1;
    if (col < 0 || col >= this.sheet.ncols) return null;
    if (displayIndex < 0 || displayIndex >= this.visibleRows.length) return null;
    return [this.visibleRows[displayIndex], col];
  }

  /* ---------------------------------------------------------------- 工作表标签 */

  _renderTabs() {
    this.tabStrip.replaceChildren();
    for (const name of this.sheetNames()) {
      this.tabStrip.append(el('button', { class: 'sheet-tab', type: 'button', dataset: { sheet: name } }, name));
    }
    this._updateTabs();
    requestAnimationFrame(() => this._updateTabArrows());
  }

  _updateTabs() {
    const sheetComparisons = new Map();
    if (this.comparison) for (const item of this.comparison.sheets) sheetComparisons.set(item.name, item);
    for (const button of this.tabStrip.querySelectorAll('.sheet-tab')) {
      const name = button.dataset.sheet;
      const comparison = sheetComparisons.get(name);
      button.classList.toggle('active', name === this.sheetName);
      let tone = 'normal';
      if (comparison) {
        if (comparison.diffs.some((diff) => diff.severity === 'uncertain')) tone = 'uncertain';
        else if (comparison.status !== 'equal') tone = 'diff';
      }
      button.dataset.tone = tone;
      if (button.classList.contains('active')) {
        button.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    }
    this._updateTabArrows();
  }

  _updateTabArrows() {
    const hasOverflow = this.tabStrip.scrollWidth > this.tabStrip.clientWidth + 2;
    this.tabLeft.style.visibility = hasOverflow ? 'visible' : 'hidden';
    this.tabRight.style.visibility = hasOverflow ? 'visible' : 'hidden';
  }

  scrollTabs(direction) {
    const page = Math.max(80, this.tabStrip.clientWidth * 0.8);
    this.tabStrip.scrollLeft += direction * page;
    this._updateTabArrows();
  }

  _bindPointerDragTabs() {
    let startX = 0;
    let startLeft = 0;
    let dragging = false;
    let downTab = null;
    this.tabStrip.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      startX = event.clientX;
      startLeft = this.tabStrip.scrollLeft;
      downTab = event.target.closest('.sheet-tab');
      dragging = false;
    });
    this.tabStrip.addEventListener('pointermove', (event) => {
      if (event.buttons !== 1) return;
      const delta = event.clientX - startX;
      if (!dragging && Math.abs(delta) > 5) {
        dragging = true;
        this.tabStrip.classList.add('dragging');
        this.tabStrip.setPointerCapture(event.pointerId);
      }
      if (dragging) this.tabStrip.scrollLeft = startLeft - delta;
    });
    const stop = (event) => {
      if (!dragging && downTab && event.type === 'pointerup') {
        const name = downTab.dataset.sheet;
        if (name) this.showSheet(name, { emit: true });
      }
      if (this.tabStrip.hasPointerCapture && this.tabStrip.hasPointerCapture(event.pointerId)) {
        this.tabStrip.releasePointerCapture(event.pointerId);
      }
      dragging = false;
      downTab = null;
      this.tabStrip.classList.remove('dragging');
    };
    this.tabStrip.addEventListener('pointerup', stop);
    this.tabStrip.addEventListener('pointercancel', stop);
  }
}

function bisect(offsets, value) {
  let low = 0;
  let high = offsets.length - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (offsets[mid] <= value) low = mid;
    else high = mid - 1;
  }
  return low;
}

function columnLetter(index) {
  let n = index + 1;
  let out = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

function fitText(ctx, text, maxWidth) {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let low = 0;
  let high = text.length;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (ctx.measureText(`${text.slice(0, mid)}…`).width <= maxWidth) low = mid;
    else high = mid - 1;
  }
  return low > 0 ? `${text.slice(0, low)}…` : '…';
}
