/*
 * EngineerDiff 网页版：单文件比较 + 批量比较 + 主题/设置。
 *
 * 与桌面版对齐：拖入即比较、双栏/表格/CSV 文本三视图、差异导航、
 * 工作表联动、仅显示差异、批量“或语义”矩阵、浅色/深色主题。
 */

import { el, clear, formatBytes, formatSeconds, toast, copyText, icon, downloadText } from './dom.js';
import { loadAnyFile, fileGroup, SUPPORTED_EXTS, extensionOf } from './workbook.js';
import { compareLoaded, setTextComparator } from './sheetcompare.js';
import { diffTextFiles } from './textdiff.js';
import { TextView } from './textview.js';
import { GridView } from './gridview.js';
import { BatchView } from './batchview.js';

setTextComparator(diffTextFiles);

const WEB_VERSION = '1.0.0';
const SETTINGS_KEY = 'engineerdiff.web.settings';
const THEME_KEY = 'engineerdiff.web.theme';

const DEFAULT_SETTINGS = {
  theme: 'system',
  text: {
    ignoreLineEndings: false,
    ignoreOuterSpace: false,
    ignoreAllSpace: false,
    ignoreCase: false,
  },
  sheet: {
    formulaMode: 'formula',
    numericTolerance: 0,
    smartRows: false,
  },
  syncScroll: true,
  syncSheets: true,
};

const state = {
  page: 'single',
  left: null,
  right: null,
  result: null,
  diffs: [],
  index: -1,
  view: 'table',
  textViews: { left: null, right: null },
  grids: { left: null, right: null },
  mobileSide: 'left',
  tableZoom: 1,
  busy: false,
  comparing: false,
  batch: null,
  settings: loadSettings(),
};

/* ------------------------------------------------------------------ 设置 */

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return structuredClone(DEFAULT_SETTINGS);
    const parsed = JSON.parse(raw);
    return {
      ...structuredClone(DEFAULT_SETTINGS),
      ...parsed,
      text: { ...DEFAULT_SETTINGS.text, ...(parsed.text || {}) },
      sheet: { ...DEFAULT_SETTINGS.sheet, ...(parsed.sheet || {}) },
    };
  } catch {
    return structuredClone(DEFAULT_SETTINGS);
  }
}

function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
  } catch {
    /* 无痕模式下静默 */
  }
}

/* ------------------------------------------------------------------ 主题 */

function preferredDark() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function effectiveTheme() {
  const mode = state.settings.theme;
  if (mode === 'dark') return 'dark';
  if (mode === 'light') return 'light';
  return preferredDark() ? 'dark' : 'light';
}

function applyTheme() {
  document.documentElement.dataset.theme = effectiveTheme();
  const button = document.getElementById('btn-theme');
  if (button) {
    button.replaceChildren(icon(effectiveTheme() === 'dark' ? 'sun' : 'moon', 16));
    button.setAttribute('aria-label', effectiveTheme() === 'dark' ? '切换到浅色主题' : '切换到深色主题');
    button.title = button.getAttribute('aria-label');
  }
  for (const grid of Object.values(state.grids)) if (grid) grid.draw();
  const batch = state.batch;
  if (batch) batch.refreshTheme();
}

function cycleTheme() {
  const order = ['system', 'light', 'dark'];
  const current = order.indexOf(state.settings.theme);
  state.settings.theme = order[(current + 1) % order.length];
  saveSettings();
  applyTheme();
  toast(`主题：${{ system: '跟随系统', light: '浅色', dark: '深色' }[state.settings.theme]}`);
}

/* ------------------------------------------------------------------ 单文件 */

function slotFor(side) {
  return document.getElementById(`slot-${side}`);
}

function renderSlot(side) {
  const slot = slotFor(side);
  const entry = side === 'left' ? state.left : state.right;
  const input = slot.querySelector('input[type=file]');
  clear(slot);
  slot.append(
    el('div', { class: 'slot-icon' }, icon('file', 20)),
    el(
      'div',
      { class: 'slot-main' },
      el('div', { class: 'slot-name' }, entry ? entry.fileName : `选择或拖入${side === 'left' ? '基准' : '待比较'}文件`),
      el(
        'div',
        { class: 'slot-meta' },
        entry ? `${entryLabel(entry)} · ${formatBytes(entry.file.size)}` : `支持 ${SUPPORTED_EXTS.join(' / ')}`,
      ),
    ),
  );
  if (entry) {
    slot.append(
      el(
        'button',
        { class: 'btn ghost small', type: 'button', title: '重新选择文件', onclick: () => input.click() },
        '更换',
      ),
    );
  }
  slot.append(input);
  slot.classList.toggle('has-file', Boolean(entry));
}

function entryLabel(entry) {
  if (!entry) return '';
  if (entry.group === 'text') return 'TXT 文本';
  if (entry.group === 'csv') return `CSV · 分隔符 ${entry.delimiter === '\t' ? 'Tab' : entry.delimiter}`;
  return `${entry.workbook.fmt === 'xls' ? 'XLS' : 'XLSX'} · ${entry.workbook.sheets.length} 个工作表`;
}

function setupSlots() {
  for (const side of ['left', 'right']) {
    const slot = slotFor(side);
    const input = el('input', {
      type: 'file',
      accept: SUPPORTED_EXTS.join(','),
      class: 'file-input',
      'aria-label': `选择${side === 'left' ? '基准' : '待比较'}文件`,
    });
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      input.value = '';
      if (file) await setFile(side, file);
    });
    slot.append(input);
    slot.addEventListener('click', (event) => {
      if (event.target.closest('button')) return;
      input.click();
    });
    slot.addEventListener('dragover', (event) => {
      event.preventDefault();
      slot.classList.add('drag');
    });
    slot.addEventListener('dragleave', () => slot.classList.remove('drag'));
    slot.addEventListener('drop', async (event) => {
      event.preventDefault();
      slot.classList.remove('drag');
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) await setFile(side, file);
    });
  }
  document.getElementById('btn-swap').addEventListener('click', () => {
    [state.left, state.right] = [state.right, state.left];
    renderSlot('left');
    renderSlot('right');
    if (state.left && state.right) void startCompare();
  });
}

async function setFile(side, file) {
  const group = fileGroup(file.name);
  if (!group) {
    showError(`不支持的文件类型：${file.name}。网页版支持 ${SUPPORTED_EXTS.join(' / ')}；图片 OCR 请使用桌面版。`);
    return;
  }
  const other = side === 'left' ? state.right : state.left;
  try {
    showBusy(true, `正在读取 ${file.name}…`);
    await nextFrame();
    const entry = await loadAnyFile(file);
    if (side === 'left') state.left = entry;
    else state.right = entry;
    renderSlot(side);
    updatePanesHead();
    hideBanner();
    const counterpart = side === 'left' ? state.right : state.left;
    if (counterpart && counterpart.group !== group) {
      showError('两侧文件类型必须一致：TXT ↔ TXT、CSV ↔ CSV、Excel ↔ Excel。请重新选择另一侧文件。');
      setStatus({ state: `已选择 ${file.name}；另一侧类型不一致，无法比较。` });
      return;
    }
    if (state.left && state.right) await startCompare();
    else setStatus({ state: `已选择 ${file.name}；拖入另一侧文件后自动比较。` });
  } catch (err) {
    showError(err && err.message ? err.message : String(err));
  } finally {
    if (!state.comparing) showBusy(false);
  }
}

async function startCompare() {
  if (!state.left || !state.right || state.comparing) return;
  state.comparing = true;
  try {
    showBusy(true, '正在比较…');
    await nextFrame();
    const started = performance.now();
    const result = compareLoaded(state.left, state.right, {
      text: state.settings.text,
      sheet: state.settings.sheet,
    });
    state.result = result;
    state.view = result.kind === 'csv' ? (state.view === 'text' ? 'text' : 'table') : result.kind === 'text' ? 'text' : 'table';
    state.tableZoom = 1;
    renderResult(result, (performance.now() - started) / 1000);
    hideBanner();
    if (result.warnings && result.warnings.length) showWarnings(result.warnings);
  } catch (err) {
    showError(err && err.message ? err.message : String(err));
  } finally {
    state.comparing = false;
    showBusy(false);
  }
}

function renderResult(result, elapsed) {
  const paneLeft = document.getElementById('pane-left');
  const paneRight = document.getElementById('pane-right');
  clear(paneLeft);
  clear(paneRight);
  state.textViews = { left: null, right: null };
  state.grids = { left: null, right: null };
  const isText = result.kind === 'text' || (result.kind === 'csv' && state.view === 'text');
  if (isText) {
    const textResult = result.kind === 'text' ? result : result.text;
    renderTextPanes(textResult);
    state.diffs = textResult.diffs;
  } else {
    const comparison = result.kind === 'csv' ? result.table : result.table;
    renderGridPanes(comparison);
    state.diffs = comparison.diffs;
  }
  state.index = state.diffs.length ? 0 : -1;
  updatePanesHead();
  updateToolbarForResult();
  updateIndexLabel(elapsed);
  setStatus({
    typeLabel: kindLabel(result.kind),
    state: state.diffs.length ? '比较完成' : '比较完成：未发现差异',
    count: state.diffs.length,
    elapsed,
  });
  if (state.index >= 0) revealCurrent();
}

function renderTextPanes(textResult) {
  for (const side of ['left', 'right']) {
    const pane = document.getElementById(`pane-${side}`);
    const body = el('div', { class: 'pane-body' });
    pane.append(body);
    const view = new TextView(body);
    view.setSide(textResult[side], side === 'left' ? '请选择基准文件' : '请选择待比较文件');
    state.textViews[side] = view;
    view.setSyncHandler((ratio) => {
      if (!state.settings.syncScroll) return;
      const other = state.textViews[side === 'left' ? 'right' : 'left'];
      if (other) other.setScrollRatio(ratio);
    });
  }
}

function renderGridPanes(comparison) {
  for (const side of ['left', 'right']) {
    const pane = document.getElementById(`pane-${side}`);
    const body = el('div', { class: 'pane-body' });
    pane.append(body);
    const grid = new GridView(body, {
      onCellClick: () => {},
      onSheetSelected: (name) => {
        if (!state.settings.syncSheets) return;
        const other = state.grids[side === 'left' ? 'right' : 'left'];
        if (other && other.sheetNames().includes(name) && other.currentSheet() !== name) {
          other.showSheet(name);
        }
      },
    });
    grid.load(comparison[side], comparison, side, { showTabs: true });
    state.grids[side] = grid;
  }
  applyGridZoom();
}

function applyGridZoom() {
  for (const grid of Object.values(state.grids)) if (grid) grid.setZoom(state.tableZoom);
  const label = document.getElementById('zoom-label');
  if (label) label.textContent = `${Math.round(state.tableZoom * 100)}%`;
}

function updatePanesHead() {
  for (const side of ['left', 'right']) {
    const head = document.getElementById(`head-${side}`);
    const entry = side === 'left' ? state.left : state.right;
    if (!head) continue;
    clear(head);
    head.append(
      el('span', { class: 'pane-badge', dataset: { side } }, side === 'left' ? 'A' : 'B'),
      el('span', { class: 'pane-name', title: entry ? entry.fileName : '' }, entry ? entry.fileName : '未选择文件'),
      el('span', { class: 'pane-meta' }, entry ? `${entryLabel(entry)} · ${formatBytes(entry.file.size)}` : ''),
    );
  }
}

function updateToolbarForResult() {
  const kind = state.result ? state.result.kind : null;
  document.getElementById('view-toggle').hidden = kind !== 'csv';
  document.getElementById('btn-copy-summary').hidden = !state.result;
  document.getElementById('text-tools').hidden = kind === 'excel' || (kind === 'csv' && state.view === 'table');
  document.getElementById('table-tools').hidden = !(kind === 'excel' || (kind === 'csv' && state.view === 'table'));
  if (kind === 'csv') {
    for (const button of document.querySelectorAll('#view-toggle button')) {
      button.classList.toggle('active', button.dataset.view === state.view);
    }
  }
}

function updateIndexLabel(elapsed) {
  const total = state.diffs.length;
  const indexLabel = document.getElementById('index-label');
  indexLabel.textContent = total ? `${state.index + 1} / ${total}` : '0 / 0';
  document.getElementById('btn-prev').disabled = !total;
  document.getElementById('btn-next').disabled = !total;
  const count = document.getElementById('status-count');
  count.textContent = total ? `差异 ${total} 处` : '无差异';
  count.dataset.tone = total ? 'diff' : 'ok';
  if (elapsed !== undefined) {
    document.getElementById('status-elapsed').textContent = formatSeconds(elapsed);
  }
}

function revealCurrent({ autoSide = false } = {}) {
  const diff = state.diffs[state.index];
  if (!diff) return;
  if (state.textViews.left || state.textViews.right) {
    for (const side of ['left', 'right']) {
      const view = state.textViews[side];
      if (!view) continue;
      const location = side === 'left' ? diff.left : diff.right;
      const row = location && location.extra ? location.extra.displayRow : 0;
      view.setCurrentDiff(diff.id);
      view.scrollToDiffRow(row === undefined || row === null ? 0 : row);
    }
  }
  for (const side of ['left', 'right']) {
    const grid = state.grids[side];
    if (grid) grid.reveal(diff);
  }
  if (autoSide && isMobile()) {
    const preferred = diff.right ? 'right' : diff.left ? 'left' : state.mobileSide;
    if (preferred !== state.mobileSide) setMobileSide(preferred);
  }
}

function stepDiff(direction) {
  if (!state.diffs.length) return;
  const total = state.diffs.length;
  state.index = (state.index + direction + total) % total;
  updateIndexLabel();
  revealCurrent({ autoSide: true });
  const diff = state.diffs[state.index];
  setStatus({ state: `${diff.title}${diff.detail ? ` — ${diff.detail}` : ''}` });
}

/* ------------------------------------------------------------------ 视图切换 */

function setupToolbar() {
  document.getElementById('btn-prev').addEventListener('click', () => stepDiff(-1));
  document.getElementById('btn-next').addEventListener('click', () => stepDiff(1));
  document.getElementById('btn-compare').addEventListener('click', () => {
    if (state.left && state.right) void startCompare();
    else toast('请先选择左右两侧文件', 'warn');
  });
  document.getElementById('cb-only-diffs').addEventListener('change', (event) => {
    const enabled = event.target.checked;
    for (const view of Object.values(state.textViews)) if (view) view.setOnlyDiffs(enabled);
    for (const grid of Object.values(state.grids)) if (grid) grid.setOnlyDiffs(enabled);
  });
  document.getElementById('cb-sync').addEventListener('change', (event) => {
    state.settings.syncScroll = event.target.checked;
    saveSettings();
  });
  document.getElementById('cb-sheet-sync').addEventListener('change', (event) => {
    state.settings.syncSheets = event.target.checked;
    saveSettings();
  });
  document.getElementById('zoom-out').addEventListener('click', () => {
    state.tableZoom = Math.max(0.5, state.tableZoom - 0.1);
    applyGridZoom();
  });
  document.getElementById('zoom-in').addEventListener('click', () => {
    state.tableZoom = Math.min(2.5, state.tableZoom + 0.1);
    applyGridZoom();
  });
  document.getElementById('zoom-label').addEventListener('click', () => {
    state.tableZoom = 1;
    applyGridZoom();
  });
  for (const button of document.querySelectorAll('#view-toggle button')) {
    button.addEventListener('click', () => {
      if (!state.result || state.result.kind !== 'csv') return;
      const nextView = button.dataset.view;
      if (nextView === state.view) return;
      state.view = nextView;
      renderResult(state.result, undefined);
    });
  }
  document.getElementById('mobile-a').addEventListener('click', () => setMobileSide('left'));
  document.getElementById('mobile-b').addEventListener('click', () => setMobileSide('right'));
}

function setMobileSide(side) {
  state.mobileSide = side;
  document.getElementById('compare-area').dataset.mobileSide = side;
  document.getElementById('mobile-a').classList.toggle('active', side === 'left');
  document.getElementById('mobile-b').classList.toggle('active', side === 'right');
  requestAnimationFrame(() => {
    const grid = state.grids[side];
    if (grid) grid._resize();
  });
}

function isMobile() {
  return window.matchMedia('(max-width: 900px)').matches;
}

/* ------------------------------------------------------------------ 批量 */

function setupBatch() {
  const root = document.getElementById('page-batch');
  state.batch = new BatchView(root, {
    getSettings: () => state.settings,
    toast,
    onError: showError,
    setStatus: (info) => setStatus(info),
  });
  state.batch.init();
}

/* ------------------------------------------------------------------ 状态与横幅 */

function setStatus({ typeLabel, state: text, count, elapsed } = {}) {
  if (typeLabel !== undefined) document.getElementById('status-type').textContent = typeLabel;
  if (text !== undefined) document.getElementById('status-text').textContent = text;
  if (count !== undefined) {
    const node = document.getElementById('status-count');
    node.textContent = count ? `差异 ${count} 处` : '无差异';
    node.dataset.tone = count ? 'diff' : 'ok';
  }
  if (elapsed !== undefined) document.getElementById('status-elapsed').textContent = formatSeconds(elapsed);
}

function showBusy(busy, message) {
  state.busy = busy;
  const bar = document.getElementById('busy');
  bar.hidden = !busy;
  if (busy && message) bar.querySelector('.busy-text').textContent = message;
  document.body.classList.toggle('is-busy', busy);
}

function showError(message) {
  const banner = document.getElementById('banner');
  banner.hidden = false;
  banner.dataset.tone = 'error';
  clear(banner).append(
    el('span', { class: 'banner-text' }, message),
    el('button', { class: 'btn ghost small', type: 'button', onclick: hideBanner }, '关闭'),
  );
}

function showWarnings(warnings) {
  const banner = document.getElementById('banner');
  banner.hidden = false;
  banner.dataset.tone = 'warn';
  clear(banner).append(
    el('div', { class: 'banner-text' }, warnings.slice(0, 3).join('；'), warnings.length > 3 ? `（共 ${warnings.length} 条）` : ''),
    el(
      'button',
      {
        class: 'btn ghost small',
        type: 'button',
        onclick: () => {
          hideBanner();
          toast(warnings.join('\n'), 'info', 8000);
        },
      },
      '全部',
    ),
    el('button', { class: 'btn ghost small', type: 'button', onclick: hideBanner }, '关闭'),
  );
}

function hideBanner() {
  document.getElementById('banner').hidden = true;
}

function nextFrame() {
  return new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0)));
}

/* ------------------------------------------------------------------ 设置对话框 */

function setupSettings() {
  const dialog = document.getElementById('settings-dialog');
  const form = {
    theme: document.getElementById('set-theme'),
    ignoreLineEndings: document.getElementById('set-eol'),
    ignoreOuterSpace: document.getElementById('set-outer'),
    ignoreAllSpace: document.getElementById('set-all'),
    ignoreCase: document.getElementById('set-case'),
    formulaMode: document.getElementById('set-formula'),
    tolerance: document.getElementById('set-tolerance'),
    smartRows: document.getElementById('set-smart'),
    syncScroll: document.getElementById('set-sync'),
    syncSheets: document.getElementById('set-sheet-sync'),
  };
  document.getElementById('btn-settings').addEventListener('click', () => {
    form.theme.value = state.settings.theme;
    form.ignoreLineEndings.checked = state.settings.text.ignoreLineEndings;
    form.ignoreOuterSpace.checked = state.settings.text.ignoreOuterSpace;
    form.ignoreAllSpace.checked = state.settings.text.ignoreAllSpace;
    form.ignoreCase.checked = state.settings.text.ignoreCase;
    form.formulaMode.value = state.settings.sheet.formulaMode;
    form.tolerance.value = String(state.settings.sheet.numericTolerance ?? 0);
    form.smartRows.checked = state.settings.sheet.smartRows;
    form.syncScroll.checked = state.settings.syncScroll;
    form.syncSheets.checked = state.settings.syncSheets;
    dialog.hidden = false;
    dialog.querySelector('button').focus();
  });
  document.getElementById('set-cancel').addEventListener('click', () => {
    dialog.hidden = true;
  });
  document.getElementById('set-reset').addEventListener('click', () => {
    state.settings = structuredClone(DEFAULT_SETTINGS);
    form.theme.value = 'system';
    for (const key of ['ignoreLineEndings', 'ignoreOuterSpace', 'ignoreAllSpace', 'ignoreCase', 'smartRows']) {
      if (key === 'smartRows') form.smartRows.checked = false;
      else form[key].checked = false;
    }
    form.formulaMode.value = 'formula';
    form.tolerance.value = '0';
    form.syncScroll.checked = true;
    form.syncSheets.checked = true;
    applyTheme();
  });
  document.getElementById('set-apply').addEventListener('click', () => {
    state.settings.theme = form.theme.value;
    state.settings.text = {
      ignoreLineEndings: form.ignoreLineEndings.checked,
      ignoreOuterSpace: form.ignoreOuterSpace.checked,
      ignoreAllSpace: form.ignoreAllSpace.checked,
      ignoreCase: form.ignoreCase.checked,
    };
    state.settings.sheet = {
      formulaMode: form.formulaMode.value,
      numericTolerance: Number(form.tolerance.value) || 0,
      smartRows: form.smartRows.checked,
    };
    state.settings.syncScroll = form.syncScroll.checked;
    state.settings.syncSheets = form.syncSheets.checked;
    document.getElementById('cb-sync').checked = state.settings.syncScroll;
    document.getElementById('cb-sheet-sync').checked = state.settings.syncSheets;
    saveSettings();
    applyTheme();
    dialog.hidden = true;
    if (state.left && state.right) void startCompare();
    if (state.batch) state.batch.refreshSettings();
  });
  document.getElementById('settings-dialog').addEventListener('click', (event) => {
    if (event.target.id === 'settings-dialog') dialog.hidden = true;
  });
}

/* ------------------------------------------------------------------ 页面切换 */

function switchPage(page) {
  state.page = page;
  hideBanner();
  document.getElementById('page-single').hidden = page !== 'single';
  document.getElementById('page-batch').hidden = page !== 'batch';
  for (const button of document.querySelectorAll('.nav-tabs button')) {
    button.classList.toggle('active', button.dataset.page === page);
  }
  if (page === 'single') {
    const count = state.diffs.length;
    setStatus({
      typeLabel: state.result ? kindLabel(state.result.kind) : '未选择文件',
      state: state.result ? (count ? '比较完成' : '比较完成：未发现差异') : '就绪：选择或拖入两侧文件后自动比较',
      count,
    });
    requestAnimationFrame(() => {
      for (const grid of Object.values(state.grids)) if (grid) grid._resize();
    });
  } else if (state.batch) {
    setStatus({
      typeLabel: '批量比较',
      state: state.batch.result
        ? `完成：${state.batch.result.fileCount} 个文件，${state.batch.result.differentCount} 处差异`
        : '就绪：导入同类型文件后开始比较',
      count: state.batch.result ? state.batch.result.differentCount : 0,
    });
    state.batch.onShow();
  }
}

function kindLabel(kind) {
  if (kind === 'text') return 'TXT ↔ TXT';
  if (kind === 'csv') return 'CSV ↔ CSV';
  if (kind === 'excel') return 'Excel ↔ Excel';
  return '未选择文件';
}

/* ------------------------------------------------------------------ 启动 */

function setupNav() {
  for (const button of document.querySelectorAll('.nav-tabs button')) {
    button.addEventListener('click', () => switchPage(button.dataset.page));
  }
  document.getElementById('btn-theme').addEventListener('click', cycleTheme);
}

function setupKeyboard() {
  window.addEventListener('keydown', (event) => {
    if (state.page !== 'single') return;
    if (event.key === 'F7') {
      event.preventDefault();
      stepDiff(-1);
    } else if (event.key === 'F8') {
      event.preventDefault();
      stepDiff(1);
    } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (state.left && state.right) void startCompare();
    } else if (event.key === 'Escape') {
      document.getElementById('settings-dialog').hidden = true;
    }
  });
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (state.settings.theme === 'system') applyTheme();
    });
  }
}

function setupFooter() {
  document.getElementById('app-version').textContent = `网页版 ${WEB_VERSION}`;
  document.getElementById('btn-copy-summary').addEventListener('click', async () => {
    if (!state.result) return;
    const lines = ['类型\t位置\t基准\t差异'];
    for (const diff of state.diffs) {
      lines.push(
        [
          state.result.kind,
          diff.title,
          diff.left ? diff.left.text : '（不存在）',
          diff.right ? diff.right.text : '（不存在）',
          diff.detail || '',
        ].join('\t'),
      );
    }
    const ok = await copyText(lines.join('\n'));
    toast(ok ? '差异列表已复制' : '复制失败，请手动选择', ok ? 'info' : 'error');
  });
}

async function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  if (!/^https?:$/.test(location.protocol)) return;
  try {
    await navigator.serviceWorker.register('./sw.js');
  } catch {
    /* 离线缓存失败不影响使用 */
  }
}

async function main() {
  applyTheme();
  setupNav();
  setupSlots();
  setupToolbar();
  setupSettings();
  setupKeyboard();
  setupFooter();
  setupBatch();
  renderSlot('left');
  renderSlot('right');
  updatePanesHead();
  for (const side of ['left', 'right']) {
    const host = document.getElementById(`pane-${side}`);
    host.append(el('div', { class: 'pane-placeholder' }, '比较结果将在这里显示'));
  }
  setMobileSide('left');
  document.getElementById('cb-sync').checked = state.settings.syncScroll;
  document.getElementById('cb-sheet-sync').checked = state.settings.syncSheets;
  updateToolbarForResult();
  setStatus({ typeLabel: '未选择文件', state: '就绪：选择或拖入两侧文件后自动比较', count: 0 });
  await registerServiceWorker();
}

document.addEventListener('DOMContentLoaded', () => {
  void main();
});
