/*
 * 表格比较引擎 —— 桌面版 spreadsheet/engine.py 的 JS 移植。
 *
 * 对齐点：
 *   - 按 Sheet 名称精确配对，与工作簿内顺序无关；
 *   - 严格坐标比较；公式模式 / 缓存值模式；数值容差；
 *   - 合并单元格区域不一致在锚点生成橙色差异；
 *   - 差异顺序稳定（Sheet 顺序 → 行 → 列），差异 id 与桌面版相同；
 *   - 可选智能行对齐（实验性，默认关闭）。
 */

import { SequenceMatcher } from './seqmatcher.js';
import { columnLetter, formatDateValue, formatNumberValue } from './workbook.js';

const TYPE_LABEL = {
  blank: '空白',
  number: '数值',
  text: '文本',
  bool: '布尔',
  date: '日期',
  error: '错误',
  formula: '公式',
};

const MERGE_DETAIL = '合并单元格区域不一致';
const SMART_ROW_LIMIT = 3000;

export const DEFAULT_SPREADSHEET_OPTIONS = {
  formulaMode: 'formula',
  numericTolerance: 0,
  smartRows: false,
};

/* ------------------------------------------------------------------ 值比较 */

function isNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function effectiveValue(cell) {
  if (cell.type === 'formula') {
    if (cell.cached === null || cell.cached === undefined) {
      return ['formula', null, cell.formula || '', true];
    }
    const cachedType = classifyValue(cell.cached);
    const format = cell.style ? cell.style.numberFormat : null;
    let display;
    if (cachedType === 'number') display = formatNumberValue(cell.cached, format);
    else if (cachedType === 'date') display = formatDateValue(cell.cached, format);
    else display = displayValue(cell.cached);
    return [cachedType, cell.cached, display, false];
  }
  return [cell.type, cell.value, cell.display, false];
}

function classifyValue(raw) {
  if (raw === null || raw === undefined) return 'blank';
  if (typeof raw === 'boolean') return 'bool';
  if (raw instanceof Date) return 'date';
  if (typeof raw === 'number') return 'number';
  if (typeof raw === 'string' && /^#[A-Z/]+[!?]$/.test(raw)) return 'error';
  return 'text';
}

function displayValue(raw) {
  if (raw === null || raw === undefined) return '';
  if (raw instanceof Date) return raw.toISOString();
  return String(raw);
}

function compareLiterals(left, right, options) {
  const leftType = left.type;
  const rightType = right.type;
  if (leftType === 'blank' && rightType === 'blank') return { equal: true };
  if (leftType !== rightType) {
    return {
      equal: false,
      detail: `类型不同：${TYPE_LABEL[leftType] || leftType} ↔ ${TYPE_LABEL[rightType] || rightType}（${left.display || '空白'} ↔ ${right.display || '空白'}）`,
    };
  }
  if (leftType === 'blank') return { equal: true };
  if (leftType === 'number') return compareNumbers(left, right, options);
  if (leftType === 'text') {
    const lv = left.value === null || left.value === undefined ? left.display : left.value;
    const rv = right.value === null || right.value === undefined ? right.display : right.value;
    if (String(lv) === String(rv)) return { equal: true };
    return { equal: false, detail: `文本不同：“${left.display}” ≠ “${right.display}”` };
  }
  if (leftType === 'bool') {
    if (Boolean(left.value) === Boolean(right.value)) return { equal: true };
    return { equal: false, detail: `布尔值不同：${left.display} ≠ ${right.display}` };
  }
  if (leftType === 'date') {
    const lv = left.value instanceof Date ? left.value.getTime() : String(left.value);
    const rv = right.value instanceof Date ? right.value.getTime() : String(right.value);
    if (lv === rv) return { equal: true };
    return { equal: false, detail: `日期不同：${left.display} ≠ ${right.display}` };
  }
  if (leftType === 'error') {
    if (String(left.value) === String(right.value) && left.display === right.display) return { equal: true };
    return { equal: false, detail: `错误值不同：${left.display} ≠ ${right.display}` };
  }
  if (left.display === right.display) return { equal: true };
  return { equal: false, detail: `内容不同：${left.display} ≠ ${right.display}` };
}

function compareNumbers(left, right, options) {
  const lv = left.value;
  const rv = right.value;
  if (!isNumber(lv) || !isNumber(rv)) {
    if (String(lv) === String(rv)) return { equal: true };
    return { equal: false, detail: `数值无法比较：${left.display} ≠ ${right.display}` };
  }
  if (lv === rv) return { equal: true };
  const tolerance = Number(options.numericTolerance || 0);
  if (tolerance > 0) {
    const difference = Math.abs(lv - rv);
    const scale = Math.max(Math.abs(lv), Math.abs(rv));
    if (difference <= tolerance || difference <= tolerance * scale) return { equal: true };
  }
  if (left.display === right.display) {
    return {
      equal: false,
      detail: `数值不同：${left.display} ≠ ${right.display}（原始值 ${lv} ≠ ${rv}）`,
    };
  }
  if (tolerance > 0) {
    return { equal: false, detail: `数值不同（超出容差 ${tolerance}）：${left.display} ≠ ${right.display}` };
  }
  return { equal: false, detail: `数值不同：${left.display} ≠ ${right.display}` };
}

function compareValues(leftCell, rightCell, options) {
  const mode = options.formulaMode === 'value' ? 'value' : 'formula';
  if (mode === 'formula' && (leftCell.formula !== null || rightCell.formula !== null)) {
    const lf = (leftCell.formula || '').trim();
    const rf = (rightCell.formula || '').trim();
    if (leftCell.formula !== null && rightCell.formula !== null && lf === rf) return { equal: true };
    if (leftCell.formula !== null && rightCell.formula !== null) {
      return { equal: false, detail: `公式不同：${lf} ≠ ${rf}` };
    }
    return { equal: false, detail: `一侧为公式、另一侧不是：${lf || '无公式'} ≠ ${rf || '无公式'}` };
  }
  let left;
  let right;
  if (mode === 'value') {
    const [lt, lv, ld, lm] = effectiveValue(leftCell);
    const [rt, rv, rd, rm] = effectiveValue(rightCell);
    if (lm || rm) {
      return {
        equal: false,
        uncertain: true,
        detail: `公式缓存值缺失，按值比较无法确认是否相同（左侧公式：${leftCell.formula || '无'}；右侧公式：${rightCell.formula || '无'}）`,
      };
    }
    left = { type: lt, value: lv, display: ld };
    right = { type: rt, value: rv, display: rd };
  } else {
    left = leftCell;
    right = rightCell;
  }
  return compareLiterals(left, right, options);
}

/* ------------------------------------------------------------------ 差异构造 */

function cellLocation(sheetName, row, col, cell) {
  return {
    kind: 'cell',
    sheet: sheetName,
    row,
    col,
    text: cell ? cell.display : '',
    extra: {},
  };
}

function buildCellDiff({
  sheetName,
  leftRow,
  leftCol,
  rightRow,
  rightCol,
  leftCell,
  rightCell,
  leftPresent,
  rightPresent,
  leftDisplay,
  rightDisplay,
  kind,
  severity = 'confirmed',
  detail = '',
  uncertain = false,
  id = null,
}) {
  const left = leftPresent ? cellLocation(sheetName, leftRow, leftCol, leftCell) : null;
  const right = rightPresent ? cellLocation(sheetName, rightRow, rightCol, rightCell) : null;
  if (left) left.text = leftDisplay;
  if (right) right.text = rightDisplay;
  return {
    id: id || `cell:${sheetName}:${leftPresent ? leftRow : rightRow}:${leftPresent ? leftCol : rightCol}`,
    kind,
    severity,
    title: `单元格 ${columnLetter(leftPresent ? leftCol : rightCol)}${(leftPresent ? leftRow : rightRow) + 1}`,
    left,
    right,
    needsReview: uncertain,
    detail,
    group: 'cell',
  };
}

function mergeSpanDesc(span) {
  return span ? `${span[0]}行x${span[1]}列` : '未合并';
}

function makeMergeDiff(sheetName, row, col, leftCell, rightCell, leftSpan, rightSpan, leftPresent, rightPresent) {
  const diff = buildCellDiff({
    sheetName,
    leftRow: row,
    leftCol: col,
    rightRow: row,
    rightCol: col,
    leftCell: leftPresent ? leftCell : null,
    rightCell: rightPresent ? rightCell : null,
    leftPresent,
    rightPresent,
    leftDisplay: leftPresent ? leftCell.display : '',
    rightDisplay: rightPresent ? rightCell.display : '',
    kind: 'modified',
    severity: 'uncertain',
    detail: MERGE_DETAIL,
    uncertain: true,
  });
  for (const [location, span] of [
    [diff.left, leftSpan],
    [diff.right, rightSpan],
  ]) {
    if (location) {
      location.extra.merged = span !== null && span !== undefined;
      location.extra.span = span ? [...span] : null;
      location.extra.spanDesc = mergeSpanDesc(span);
    }
  }
  return diff;
}

function makeSheetOnlyDiff(sheet, side) {
  const name = sheet.name;
  const location = { kind: 'sheet', sheet: name, text: name, row: null, col: null };
  return {
    id: `sheet:${name}`,
    kind: side === 'left' ? 'only_left' : 'only_right',
    severity: 'confirmed',
    title: `工作表“${name}”仅${side === 'left' ? '左' : '右'}侧存在`,
    left: side === 'left' ? location : null,
    right: side === 'right' ? location : null,
    group: 'sheet',
    detail: `该工作表仅存在于${side === 'left' ? '左' : '右'}侧工作簿`,
  };
}

/* ------------------------------------------------------------------ 严格比较 */

function mergeMaps(sheet) {
  const anchors = new Map();
  const covered = new Set();
  for (const [row, col, rowSpan, colSpan] of sheet.merged) {
    if (rowSpan <= 0 || colSpan <= 0 || row >= sheet.nrows || col >= sheet.ncols) continue;
    const rs = Math.min(rowSpan, sheet.nrows - row);
    const cs = Math.min(colSpan, sheet.ncols - col);
    anchors.set(`${row},${col}`, [rs, cs]);
    for (let r = row; r < row + rs; r += 1) {
      for (let c = col; c < col + cs; c += 1) {
        if (r !== row || c !== col) covered.add(`${r},${c}`);
      }
    }
  }
  return { anchors, covered };
}

function sameSpan(a, b) {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a[0] === b[0] && a[1] === b[1];
}

function compareSheetStrict(name, leftSheet, rightSheet, options) {
  const leftMaps = mergeMaps(leftSheet);
  const rightMaps = mergeMaps(rightSheet);
  const nrows = Math.max(leftSheet.nrows, rightSheet.nrows);
  const ncols = Math.max(leftSheet.ncols, rightSheet.ncols);
  const diffs = [];
  for (let row = 0; row < nrows; row += 1) {
    for (let col = 0; col < ncols; col += 1) {
      const leftPresent = row < leftSheet.nrows && col < leftSheet.ncols;
      const rightPresent = row < rightSheet.nrows && col < rightSheet.ncols;
      const leftCell = leftPresent ? leftSheet.get(row, col) : null;
      const rightCell = rightPresent ? rightSheet.get(row, col) : null;
      const leftSpan = leftMaps.anchors.get(`${row},${col}`) || null;
      const rightSpan = rightMaps.anchors.get(`${row},${col}`) || null;
      if (!sameSpan(leftSpan, rightSpan)) {
        diffs.push(
          makeMergeDiff(name, row, col, leftCell || { display: '' }, rightCell || { display: '' }, leftSpan, rightSpan, leftPresent, rightPresent),
        );
        continue;
      }
      if (leftMaps.covered.has(`${row},${col}`) || rightMaps.covered.has(`${row},${col}`)) continue;
      const result = compareValues(
        leftCell || { type: 'blank', value: null, display: '', formula: null, cached: null },
        rightCell || { type: 'blank', value: null, display: '', formula: null, cached: null },
        options,
      );
      if (result.equal) continue;
      let kind;
      if (leftPresent && rightPresent) kind = 'modified';
      else if (leftPresent) kind = 'removed';
      else kind = 'added';
      diffs.push(
        buildCellDiff({
          sheetName: name,
          leftRow: row,
          leftCol: col,
          rightRow: row,
          rightCol: col,
          leftCell: leftPresent ? leftCell : null,
          rightCell: rightPresent ? rightCell : null,
          leftPresent,
          rightPresent,
          leftDisplay: leftCell ? leftCell.display : '',
          rightDisplay: rightCell ? rightCell.display : '',
          kind,
          severity: result.uncertain ? 'uncertain' : 'confirmed',
          detail: result.detail || '',
          uncertain: Boolean(result.uncertain),
        }),
      );
    }
  }
  return diffs;
}

/* ------------------------------------------------------------------ 智能行对齐 */

function literalSignature(cell) {
  if (cell.type === 'number') {
    const value = Number(cell.value);
    return ['n', Number.isFinite(value) ? String(value) : cell.display];
  }
  if (cell.type === 'date') {
    return ['d', cell.value instanceof Date ? cell.value.toISOString() : String(cell.value)];
  }
  if (cell.type === 'bool') return ['b', Boolean(cell.value)];
  if (cell.type === 'error') return ['e', cell.display || String(cell.value)];
  if (cell.type === 'text') return ['t', String(cell.value === null || cell.value === undefined ? cell.display : cell.value)];
  return ['blank'];
}

function cellSignature(cell) {
  if (cell.type === 'formula') {
    const cachedType = cell.cached === null || cell.cached === undefined ? 'blank' : classifyValue(cell.cached);
    return ['f', (cell.formula || '').trim(), JSON.stringify(literalSignature({ type: cachedType, value: cell.cached, display: '' }))];
  }
  return literalSignature(cell);
}

function rowSignature(sheet, row, ncols) {
  const signature = [];
  for (let col = 0; col < ncols; col += 1) {
    signature.push(row < sheet.nrows && col < sheet.ncols ? cellSignature(sheet.get(row, col)) : ['blank']);
  }
  return JSON.stringify(signature);
}

function smartUnpairedRowDiffs(sheetName, side, row, sheet, ncols) {
  const diffs = [];
  const present = side === 'left';
  for (let col = 0; col < ncols; col += 1) {
    if (col >= sheet.ncols || row >= sheet.nrows) break;
    const cell = sheet.get(row, col);
    if (cell.type === 'blank') continue;
    diffs.push(
      buildCellDiff({
        sheetName,
        leftRow: row,
        leftCol: col,
        rightRow: row,
        rightCol: col,
        leftCell: present ? cell : null,
        rightCell: present ? null : cell,
        leftPresent: present,
        rightPresent: !present,
        leftDisplay: present ? cell.display : '',
        rightDisplay: present ? '' : cell.display,
        kind: present ? 'removed' : 'added',
        detail: '整行没有对应行（智能行对齐）',
        id: `cell:${sheetName}:${present ? 'l' : 'r'}:${row}:${col}`,
      }),
    );
  }
  return diffs;
}

function compareAlignedCells(sheetName, leftRow, leftCol, rightRow, rightCol, leftSheet, rightSheet, options) {
  const leftPresent = leftRow < leftSheet.nrows && leftCol < leftSheet.ncols;
  const rightPresent = rightRow < rightSheet.nrows && rightCol < rightSheet.ncols;
  const leftCell = leftPresent ? leftSheet.get(leftRow, leftCol) : { type: 'blank', value: null, display: '', formula: null, cached: null };
  const rightCell = rightPresent ? rightSheet.get(rightRow, rightCol) : { type: 'blank', value: null, display: '', formula: null, cached: null };
  const result = compareValues(leftCell, rightCell, options);
  if (result.equal) return null;
  let kind;
  if (leftPresent && rightPresent) kind = 'modified';
  else if (leftPresent) kind = 'removed';
  else kind = 'added';
  return buildCellDiff({
    sheetName,
    leftRow,
    leftCol,
    rightRow,
    rightCol,
    leftCell: leftPresent ? leftCell : null,
    rightCell: rightPresent ? rightCell : null,
    leftPresent,
    rightPresent,
    leftDisplay: leftCell.display,
    rightDisplay: rightCell.display,
    kind,
    severity: result.uncertain ? 'uncertain' : 'confirmed',
    detail: result.detail || '',
    uncertain: Boolean(result.uncertain),
  });
}

function compareSheetSmart(name, leftSheet, rightSheet, options) {
  const warnings = [];
  if (leftSheet.merged.length || rightSheet.merged.length) {
    warnings.push(`工作表“${name}”包含合并单元格，智能行对齐已回退为严格坐标比较。`);
    return { diffs: compareSheetStrict(name, leftSheet, rightSheet, options), warnings };
  }
  if (Math.max(leftSheet.nrows, rightSheet.nrows) > SMART_ROW_LIMIT) {
    warnings.push(`工作表“${name}”行数超过 ${SMART_ROW_LIMIT}，智能行对齐已回退为严格坐标比较。`);
    return { diffs: compareSheetStrict(name, leftSheet, rightSheet, options), warnings };
  }
  const ncols = Math.max(leftSheet.ncols, rightSheet.ncols);
  const leftSigs = Array.from({ length: leftSheet.nrows }, (_, row) => rowSignature(leftSheet, row, ncols));
  const rightSigs = Array.from({ length: rightSheet.nrows }, (_, row) => rowSignature(rightSheet, row, ncols));
  const matcher = new SequenceMatcher(null, leftSigs, rightSigs, false);
  const diffs = [];
  for (const [tag, ls, le, rs, re] of matcher.getOpcodes()) {
    const leftRows = Array.from({ length: le - ls }, (_, i) => ls + i);
    const rightRows = Array.from({ length: re - rs }, (_, i) => rs + i);
    if (tag === 'equal') {
      for (let i = 0; i < leftRows.length; i += 1) {
        for (let col = 0; col < ncols; col += 1) {
          const diff = compareAlignedCells(name, leftRows[i], col, rightRows[i], col, leftSheet, rightSheet, options);
          if (diff) diffs.push(diff);
        }
      }
      continue;
    }
    const paired = Math.min(leftRows.length, rightRows.length);
    for (let i = 0; i < paired; i += 1) {
      for (let col = 0; col < ncols; col += 1) {
        const diff = compareAlignedCells(name, leftRows[i], col, rightRows[i], col, leftSheet, rightSheet, options);
        if (diff) diffs.push(diff);
      }
    }
    for (const row of leftRows.slice(paired)) diffs.push(...smartUnpairedRowDiffs(name, 'left', row, leftSheet, ncols));
    for (const row of rightRows.slice(paired)) diffs.push(...smartUnpairedRowDiffs(name, 'right', row, rightSheet, ncols));
  }
  diffs.sort(diffSortKey);
  warnings.push('智能行对齐为实验性功能：按行签名对齐后再比较，结果可能与严格坐标模式不同。');
  return { diffs, warnings };
}

function diffSortKey(a, b) {
  const key = (diff) => {
    if (diff.left && diff.left.row !== null && diff.left.row !== undefined) return [diff.left.row, diff.left.col || 0, diff.id];
    if (diff.right && diff.right.row !== null && diff.right.row !== undefined) return [diff.right.row, diff.right.col || 0, diff.id];
    return [0, 0, diff.id];
  };
  const ka = key(a);
  const kb = key(b);
  for (let i = 0; i < 2; i += 1) if (ka[i] !== kb[i]) return ka[i] - kb[i];
  return String(ka[2]).localeCompare(String(kb[2]));
}

/* ------------------------------------------------------------------ 工作簿 */

function pairSheetNames(left, right) {
  const names = left.sheets.map((sheet) => sheet.name);
  for (const sheet of right.sheets) if (!names.includes(sheet.name)) names.push(sheet.name);
  return names;
}

function dedupeWarnings(warnings) {
  const seen = new Set();
  const out = [];
  for (const warning of warnings) {
    if (warning && !seen.has(warning)) {
      seen.add(warning);
      out.push(warning);
    }
  }
  return out;
}

/** 比较两个工作簿。返回 { left,right,sheets,diffs,warnings,settings }。 */
export function compareWorkbooks(left, right, options = {}) {
  const opts = { ...DEFAULT_SPREADSHEET_OPTIONS, ...options };
  const comparison = {
    kind: left.fmt === 'csv' ? 'csv' : 'excel',
    left,
    right,
    sheets: [],
    diffs: [],
    warnings: [...left.warnings, ...right.warnings],
    settings: opts,
  };
  for (const name of pairSheetNames(left, right)) {
    const leftSheet = left.sheet(name);
    const rightSheet = right.sheet(name);
    const sheetComparison = {
      name,
      left: leftSheet,
      right: rightSheet,
      leftIndex: left.sheets.findIndex((sheet) => sheet.name === name),
      rightIndex: right.sheets.findIndex((sheet) => sheet.name === name),
      status: 'equal',
      diffs: [],
      warnings: [],
    };
    if (!leftSheet) {
      sheetComparison.status = 'only_right';
      sheetComparison.diffs = [makeSheetOnlyDiff(rightSheet, 'right')];
    } else if (!rightSheet) {
      sheetComparison.status = 'only_left';
      sheetComparison.diffs = [makeSheetOnlyDiff(leftSheet, 'left')];
    } else {
      let diffs;
      if (opts.smartRows) {
        const smart = compareSheetSmart(name, leftSheet, rightSheet, opts);
        diffs = smart.diffs;
        sheetComparison.warnings.push(...smart.warnings);
        comparison.warnings.push(...smart.warnings);
      } else {
        diffs = compareSheetStrict(name, leftSheet, rightSheet, opts);
      }
      sheetComparison.diffs = diffs;
      sheetComparison.status = diffs.length ? 'different' : 'equal';
    }
    comparison.sheets.push(sheetComparison);
    comparison.diffs.push(...sheetComparison.diffs);
  }
  const missingCache = comparison.diffs.filter(
    (diff) => diff.group === 'cell' && diff.severity === 'uncertain' && diff.detail.includes('缓存值缺失'),
  ).length;
  if (missingCache) {
    comparison.warnings.push(`${missingCache} 个公式单元格缺少缓存值，已标记为橙色需要人工确认。`);
  }
  const uncertain = comparison.diffs.filter((diff) => diff.severity === 'uncertain').length;
  if (uncertain && !missingCache) {
    comparison.warnings.push(`${uncertain} 处差异无法完全确认，已标记为橙色需要人工复核。`);
  }
  comparison.warnings = dedupeWarnings(comparison.warnings);
  return comparison;
}

export function sheetColor(sheetComparison) {
  if (!sheetComparison) return 'normal';
  if (sheetComparison.diffs.some((diff) => diff.severity === 'uncertain')) return 'uncertain';
  if (sheetComparison.status !== 'equal') return 'diff';
  return 'normal';
}

/** 统一入口：two loaded files → comparison result（text / csv / excel）。 */
export function compareLoaded(leftFile, rightFile, options = {}) {
  const textOptions = options.text || {};
  const sheetOptions = options.sheet || {};
  if (leftFile.group !== rightFile.group && !(leftFile.group === 'csv' && rightFile.group === 'csv')) {
    throw new Error('两侧文件类型必须一致：TXT ↔ TXT、CSV ↔ CSV、Excel ↔ Excel。');
  }
  if (leftFile.group === 'text') {
    const result = (options.compareTexts || compareTextsFallback)(leftFile.bytes, rightFile.bytes, textOptions);
    result.left.path = leftFile.fileName;
    result.right.path = rightFile.fileName;
    return result;
  }
  const table = compareWorkbooks(leftFile.workbook, rightFile.workbook, sheetOptions);
  if (leftFile.group === 'csv') {
    const text = (options.compareTexts || compareTextsFallback)(leftFile.bytes, rightFile.bytes, textOptions);
    text.left.path = leftFile.fileName;
    text.right.path = rightFile.fileName;
    const extraWarnings = [];
    if (leftFile.delimiter !== rightFile.delimiter) {
      extraWarnings.push(
        `左右 CSV 分隔符不同（左侧 ${JSON.stringify(leftFile.delimiter)}，右侧 ${JSON.stringify(rightFile.delimiter)}），表格比较按各自解析后的单元格进行。`,
      );
    }
    return {
      kind: 'csv',
      text,
      table,
      diffs: table.diffs,
      warnings: [...text.warnings, ...table.warnings, ...extraWarnings],
      settings: { ...text.settings, ...table.settings },
      leftDelimiter: leftFile.delimiter,
      rightDelimiter: rightFile.delimiter,
    };
  }
  return {
    kind: 'excel',
    table,
    diffs: table.diffs,
    warnings: table.warnings,
    settings: table.settings,
  };
}

let _compareTexts = null;

export function setTextComparator(fn) {
  _compareTexts = fn;
}

function compareTextsFallback(leftBytes, rightBytes, options) {
  if (!_compareTexts) throw new Error('文本比较器未注册。');
  return _compareTexts(leftBytes, rightBytes, options);
}
