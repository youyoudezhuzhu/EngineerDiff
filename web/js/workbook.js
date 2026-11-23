/*
 * 工作簿读取：XLS/XLSX（SheetJS）+ CSV（内置解析）。
 *
 * 与桌面版 loader.py / csv_parser.py 对齐：
 *   - 行列上限 50000 / 2000 / 500000 单元格，超限截断并给出中文警告；
 *   - 合并单元格、列宽行高、公式表达式与缓存值分别保存；
 *   - CSV 支持引号/逗号/引号内换行、分隔符识别、CRLF 统一为 LF。
 *
 * 网页版使用稀疏表格（get(row,col)），避免 5 万行 × 2000 列的矩形数组占用内存。
 */

export const MAX_ROWS = 50000;
export const MAX_COLS = 2000;
export const MAX_CELLS = 500000;
export const CSV_SHEET_NAME = 'CSV';

export class SpreadsheetLoadError extends Error {}

/* ------------------------------------------------------------------ 稀疏表 */

const BLANK_CELL = Object.freeze({ display: '', type: 'blank', value: null, formula: null, cached: null, style: {} });

export function blankCell() {
  return BLANK_CELL;
}

export class SheetModel {
  constructor(name) {
    this.name = name;
    this.nrows = 0;
    this.ncols = 0;
    this._cells = new Map(); // key `${r},${c}` -> cell
    this.merged = []; // [row, col, rowSpan, colSpan]
    this.mergedAnchor = new Map(); // "r,c" -> [rowSpan, colSpan]
    this.covered = new Set(); // "r,c" 非锚点
    this.colWidths = new Map();
    this.rowHeights = new Map();
    this.defaultColWidth = 64;
    this.defaultRowHeight = 20;
    this.hiddenRows = new Set();
    this.hiddenCols = new Set();
    this.warnings = [];
  }

  set(row, col, cell) {
    this._cells.set(`${row},${col}`, cell);
    if (row + 1 > this.nrows) this.nrows = row + 1;
    if (col + 1 > this.ncols) this.ncols = col + 1;
  }

  get(row, col) {
    return this._cells.get(`${row},${col}`) || BLANK_CELL;
  }

  has(row, col) {
    return this._cells.has(`${row},${col}`);
  }

  finalizeMerges() {
    this.mergedAnchor.clear();
    this.covered.clear();
    const anchors = new Map();
    for (const [row, col, rowSpan, colSpan] of this.merged) {
      if (rowSpan <= 0 || colSpan <= 0 || row >= this.nrows || col >= this.ncols) continue;
      const rs = Math.min(rowSpan, this.nrows - row);
      const cs = Math.min(colSpan, this.ncols - col);
      anchors.set(`${row},${col}`, [rs, cs]);
      for (let r = row; r < row + rs; r += 1) {
        for (let c = col; c < col + cs; c += 1) {
          if (r !== row || c !== col) this.covered.add(`${r},${c}`);
        }
      }
    }
    this.mergedAnchor = anchors;
    return this;
  }
}

export class WorkbookModel {
  constructor(name, fmt) {
    this.name = name;
    this.fmt = fmt;
    this.sheets = [];
    this.warnings = [];
    this._byName = new Map();
  }

  addSheet(sheet) {
    this.sheets.push(sheet);
    this._byName.set(sheet.name, sheet);
    return sheet;
  }

  sheet(name) {
    return this._byName.get(name) || null;
  }
}

/* ------------------------------------------------------------------ 工具 */

export function columnLetter(index) {
  let n = index + 1;
  let out = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

export function estimateTextWidth(text) {
  let width = 0;
  for (const ch of text) width += ch.codePointAt(0) > 0x2e80 ? 2 : 1;
  return width;
}

/* ------------------------------------------------------------------ CSV */

const DELIMITERS = [',', ';', '\t', '|'];

function decodeCsvBytes(bytes) {
  const warnings = [];
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return { text: new TextDecoder('utf-8').decode(bytes.subarray(3)), encoding: 'utf-8-sig', warnings };
  }
  if (bytes.length >= 2 && ((bytes[0] === 0xff && bytes[1] === 0xfe) || (bytes[0] === 0xfe && bytes[1] === 0xff))) {
    const enc = bytes[0] === 0xff ? 'utf-16le' : 'utf-16be';
    try {
      return { text: new TextDecoder(enc).decode(bytes.subarray(2)), encoding: 'utf-16', warnings };
    } catch {
      /* 回退 */
    }
  }
  for (const encoding of ['utf-8', 'gb18030']) {
    try {
      const fatal = encoding === 'utf-8';
      const text = new TextDecoder(encoding, { fatal }).decode(bytes);
      if (encoding === 'gb18030') warnings.push('CSV 不是 UTF-8 编码，已按 GB18030 解码，请确认中文显示正常。');
      return { text, encoding, warnings };
    } catch {
      /* 尝试下一种 */
    }
  }
  warnings.push('CSV 编码无法识别，已按 Latin-1 解码，非 ASCII 字符可能显示异常。');
  return { text: new TextDecoder('latin1').decode(bytes), encoding: 'latin-1', warnings };
}

/** 识别分隔符：优先每行列数一致且引用外的出现次数最多者。 */
export function sniffDelimiter(text) {
  const warnings = [];
  const sample = text.slice(0, 16384);
  const lines = sample.split(/\r\n|\n|\r/).filter((line) => line.trim() !== '');
  if (!lines.length) return { delimiter: ',', warnings };
  const countsFor = (delimiter) => {
    const counts = [];
    for (const line of lines.slice(0, 50)) {
      let count = 0;
      let inQuotes = false;
      for (let i = 0; i < line.length; i += 1) {
        const ch = line[i];
        if (ch === '"') {
          if (inQuotes && line[i + 1] === '"') i += 1;
          else inQuotes = !inQuotes;
        } else if (!inQuotes && ch === delimiter) count += 1;
      }
      counts.push(count);
    }
    return counts;
  };
  let best = null;
  for (const delimiter of DELIMITERS) {
    const counts = countsFor(delimiter);
    const total = counts.reduce((sum, value) => sum + value, 0);
    if (!total) continue;
    const consistent = counts.every((value) => value === counts[0]);
    const score = (consistent ? 1000 : 0) + total;
    if (!best || score > best.score) best = { delimiter, score };
  }
  if (!best) {
    warnings.push('未能识别 CSV 分隔符，已按逗号解析。');
    return { delimiter: ',', warnings };
  }
  return { delimiter: best.delimiter, warnings };
}

/** RFC4180 解析：支持引号、双引号转义、引号内换行与分隔符。 */
export function parseCsvText(text, delimiter = ',') {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  let i = 0;
  const pushField = () => {
    row.push(field);
    field = '';
  };
  const pushRow = () => {
    rows.push(row);
    row = [];
  };
  while (i < text.length) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += ch;
      i += 1;
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
      i += 1;
      continue;
    }
    if (ch === delimiter) {
      pushField();
      i += 1;
      continue;
    }
    if (ch === '\r') {
      if (text[i + 1] === '\n') i += 1;
      pushField();
      pushRow();
      i += 1;
      continue;
    }
    if (ch === '\n') {
      pushField();
      pushRow();
      i += 1;
      continue;
    }
    field += ch;
    i += 1;
  }
  if (field !== '' || row.length || text.endsWith(delimiter)) {
    pushField();
    pushRow();
  }
  // csv.reader 不产生结尾空行；去掉文末全空行
  while (rows.length && rows[rows.length - 1].every((value) => value === '')) rows.pop();
  return rows;
}

function normalizeFieldText(field) {
  return field.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

/** 解析 CSV 字节，返回 { workbook, delimiter, text, warnings }。 */
export function parseCsv(bytes, fileName) {
  const { text, warnings } = decodeCsvBytes(bytes);
  if (!text) {
    const wb = new WorkbookModel(fileName, 'csv');
    const sheet = wb.addSheet(new SheetModel(CSV_SHEET_NAME));
    sheet.defaultColWidth = 64;
    wb.warnings.push('CSV 文件为空。');
    return { workbook: wb, delimiter: ',', text, warnings: wb.warnings };
  }
  const { delimiter, warnings: delimWarnings } = sniffDelimiter(text);
  warnings.push(...delimWarnings);
  const rawRows = parseCsvText(text, delimiter);
  const ncols = rawRows.reduce((max, row) => Math.max(max, row.length), 0);
  const wb = new WorkbookModel(fileName, 'csv');
  const sheet = wb.addSheet(new SheetModel(CSV_SHEET_NAME));
  sheet.defaultColWidth = 64;
  sheet.defaultRowHeight = 20;
  if (!rawRows.length || ncols === 0) {
    wb.warnings.push('CSV 文件没有可显示的数据行。');
    wb.warnings.push(...warnings);
    return { workbook: wb, delimiter, text, warnings: wb.warnings };
  }
  let ragged = 0;
  const multilineCols = new Set();
  const maxWidth = new Array(ncols).fill(0);
  rawRows.forEach((rawRow, rowIndex) => {
    if (rawRow.length !== ncols && rawRow.some((value) => value !== '')) ragged += 1;
    let lines = 1;
    for (let col = 0; col < ncols; col += 1) {
      const raw = col < rawRow.length ? rawRow[col] : '';
      const field = normalizeFieldText(raw);
      for (const line of field.split('\n')) {
        maxWidth[col] = Math.max(maxWidth[col], estimateTextWidth(line));
      }
      if (!field) continue;
      const multiline = field.includes('\n');
      if (multiline) {
        multilineCols.add(col);
        lines = Math.max(lines, field.split('\n').length);
      }
      sheet.set(rowIndex, col, {
        display: field,
        type: 'text',
        value: field,
        formula: null,
        cached: null,
        style: { wrap: multiline },
      });
    }
    if (lines > 1) {
      sheet.rowHeights.set(rowIndex, Math.max(20, 20 * lines + 6));
    }
  });
  sheet.nrows = rawRows.length;
  sheet.ncols = ncols;
  for (let col = 0; col < ncols; col += 1) {
    let width = Math.max(60, Math.min(360, Math.round(maxWidth[col] * 7 + 24)));
    if (multilineCols.has(col)) width = Math.max(width, 180);
    sheet.colWidths.set(col, width);
  }
  if (ragged) wb.warnings.push(`${ragged} 行字段数与最大列数不一致，缺失字段已按空白单元格处理。`);
  wb.warnings.push(...warnings);
  return { workbook: wb, delimiter, text, warnings: wb.warnings };
}

/* ------------------------------------------------------------------ XLSX / XLS */

/** 从 bookFiles 原始 XML 中恢复「有公式但没有缓存值」的单元格。
 *
 * SheetJS 会直接丢弃这类单元格（没有 v 值），导致公式模式下漏报差异；
 * 桌面版 openpyxl 能读到公式表达式，因此这里补回来以保持行为一致。
 * 兼容 `s:` 等命名空间前缀与空 `<v/>`。
 */
const XML_ENTITIES = { '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&amp;': '&' };

function decodeXmlText(text) {
  return text.replace(/&(lt|gt|quot|apos|amp);/g, (match) => XML_ENTITIES[match]);
}

function recoverFormulaOnlyCells(wb, XLSX) {
  const files = wb.files;
  if (!files) return new Map();
  const decode = (entry) => {
    if (!entry || !entry.content) return '';
    const content = entry.content;
    if (typeof content === 'string') return content;
    try {
      return new TextDecoder('utf-8').decode(content);
    } catch {
      return '';
    }
  };
  const workbookXml = decode(files['xl/workbook.xml']);
  const relsXml = decode(files['xl/_rels/workbook.xml.rels']);
  if (!workbookXml || !relsXml) return new Map();

  const relTarget = new Map();
  for (const match of relsXml.matchAll(/<(?:\w+:)?Relationship\b([^>]*?)\/?>/g)) {
    const attrs = match[1];
    const id = /\bId="([^"]+)"/.exec(attrs);
    const target = /\bTarget="([^"]+)"/.exec(attrs);
    if (id && target) relTarget.set(id[1], target[1].replace(/^\.?\//, ''));
  }
  const partBySheet = new Map();
  for (const match of workbookXml.matchAll(/<(?:\w+:)?sheet\b([^>]*?)\/?>/g)) {
    const attrs = match[1];
    const name = /\bname="([^"]*)"/.exec(attrs);
    const rid = /\br:id="([^"]+)"/.exec(attrs) || /\brelationshipId="([^"]+)"/.exec(attrs);
    if (!name || !rid) continue;
    let target = relTarget.get(rid[1]);
    if (!target) continue;
    if (!target.startsWith('xl/')) target = `xl/${target}`;
    partBySheet.set(decodeXmlText(name[1]), target);
  }

  const result = new Map();
  for (const [sheetName, part] of partBySheet) {
    const xml = decode(files[part]);
    if (!xml) continue;
    // 先去掉自闭合空单元格 <c r="A3" s="2"/>，否则成对匹配会跨越到下一个 </c>。
    const pairedXml = xml.replace(/<(?:\w+:)?c\b[^>]*\/>/g, '');
    const recovered = [];
    for (const cellMatch of pairedXml.matchAll(/<(?:\w+:)?c\b([^>]*)>([\s\S]*?)<\/(?:\w+:)?c>/g)) {
      const attrs = cellMatch[1];
      const inner = cellMatch[2];
      const formulaMatch = /<(?:\w+:)?f\b[^>]*>([\s\S]*?)<\/(?:\w+:)?f>/.exec(inner);
      if (!formulaMatch) continue;
      // 有非空缓存值时不处理；空 <v/> / <v></v> 视为没有缓存值。
      const valueMatch = /<(?:\w+:)?v\b[^>]*?(?:\/>|>([\s\S]*?)<\/(?:\w+:)?v>)/.exec(inner);
      if (valueMatch && valueMatch[1] && valueMatch[1].trim() !== '') continue;
      const ref = /\br="([A-Z]+[0-9]+)"/.exec(attrs);
      if (!ref) continue;
      let formula = decodeXmlText(formulaMatch[1]).trim();
      if (!formula) continue;
      if (!formula.startsWith('=')) formula = `=${formula}`;
      let pos;
      try {
        pos = XLSX.utils.decode_cell(ref[1]);
      } catch {
        continue;
      }
      recovered.push([pos.r, pos.c, formula]);
    }
    if (recovered.length) result.set(sheetName, recovered);
  }
  return result;
}

function cellTypeFromSheetJs(cell) {
  if (cell.f) return 'formula';
  switch (cell.t) {
    case 'n':
      return 'number';
    case 'b':
      return 'bool';
    case 'd':
      return 'date';
    case 'e':
      return 'error';
    case 's':
    case 'str':
      return 'text';
    default:
      return cell.v == null ? 'blank' : 'text';
  }
}

/* ---- 数字 / 日期显示（与桌面版 loader.format_number / format_date 对齐） ---- */

function generalNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (number === 0) return '0';
  if (Number.isInteger(number) && Math.abs(number) < 1e15) return String(number);
  let text = number.toPrecision(15);
  if (text.includes('e') || text.includes('E')) {
    let [mantissa, exponent] = text.split(/[eE]/);
    const sign = exponent.startsWith('-') ? '-' : '+';
    exponent = exponent.replace(/^[+-]/, '').replace(/^0+/, '') || '0';
    text = `${mantissa}E${sign}${exponent}`;
  } else if (text.includes('.')) {
    text = text.replace(/0+$/, '').replace(/\.$/, '');
  }
  return text;
}

function numberFormatDecimals(section) {
  const match = /\.([0#]+)/.exec(section);
  if (match) return match[1].length;
  if (section.includes('0') || section.includes('#')) return 0;
  return null;
}

export function formatNumberValue(value, numberFormat) {
  const fmt = (numberFormat || '').trim();
  if (!fmt || fmt.toLowerCase() === 'general') return generalNumber(value);
  let section = fmt.split(';')[0].replace(/\[[^\]]*\]/g, '').trim();
  if (!section) return generalNumber(value);
  let number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  let negative = false;
  if (number < 0) {
    negative = true;
    number = Math.abs(number);
  }
  const percent = section.includes('%');
  if (percent) number *= 100;
  const decimals = numberFormatDecimals(section);
  const thousands = section.split('.')[0].includes(',');
  let text;
  if (/e/i.test(section) && (section.includes('0') || section.includes('#'))) {
    text = number.toExponential(6).replace('e', 'E');
  } else if (decimals === null) {
    text = generalNumber(number);
  } else if (thousands) {
    text = number.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  } else {
    text = number.toFixed(decimals);
  }
  if (percent) text += '%';
  if (negative) text = `-${text}`;
  return text;
}

export function formatDateValue(value, numberFormat) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (input) => String(input).padStart(2, '0');
  const y = date.getFullYear();
  const m = pad(date.getMonth() + 1);
  const d = pad(date.getDate());
  const hh = pad(date.getHours());
  const mm = pad(date.getMinutes());
  const ss = pad(date.getSeconds());
  const fmt = (numberFormat || '').toLowerCase();
  if (fmt) {
    const hasTime = /[hs]/.test(fmt);
    const hasDate = /[yd]/.test(fmt);
    if (hasTime && !hasDate) return `${hh}:${mm}:${ss}`;
    if (hasDate && hasTime) return `${y}-${m}-${d} ${hh}:${mm}:${ss}`;
    if (hasDate) return `${y}-${m}-${d}`;
  }
  if (date.getHours() || date.getMinutes() || date.getSeconds()) return `${y}-${m}-${d} ${hh}:${mm}:${ss}`;
  return `${y}-${m}-${d}`;
}

function classifyRawValue(value) {
  if (value === null || value === undefined) return 'blank';
  if (typeof value === 'boolean') return 'bool';
  if (value instanceof Date) return 'date';
  if (typeof value === 'number') return 'number';
  return 'text';
}

/** 按值类型与数字格式渲染显示文本（公式单元格按其缓存值类型渲染）。 */
function displayFor(cell, type) {
  if (cell.v === null || cell.v === undefined) return '';
  if (type === 'formula') {
    const cachedType = classifyRawValue(cell.v);
    return displayFor({ ...cell, z: cell.z }, cachedType);
  }
  if (type === 'bool') return cell.v ? 'TRUE' : 'FALSE';
  if (type === 'date') return formatDateValue(cell.v, cell.z);
  if (type === 'number') return formatNumberValue(cell.v, cell.z);
  if (type === 'error') return String(cell.v) || '#ERROR!';
  return String(cell.v);
}

function colWidthPx(sheetJsCol) {
  if (!sheetJsCol || !sheetJsCol.wch) return null;
  return Math.round(sheetJsCol.wch * 7 + 5);
}

function rowHeightPx(sheetJsRow) {
  if (!sheetJsRow || !sheetJsRow.hpt) return null;
  return Math.round((sheetJsRow.hpt * 4) / 3);
}

/** 解析 XLS/XLSX 字节（SheetJS），返回 { workbook, warnings }。 */
export function parseSpreadsheet(bytes, fileName, ext) {
  const XLSX = globalThis.XLSX;
  if (!XLSX) throw new SpreadsheetLoadError('表格解析组件未加载（xlsx.full.min.js）。');
  let book;
  try {
    book = XLSX.read(bytes, {
      type: 'array',
      cellDates: true,
      cellNF: true,
      cellText: true,
      sheetStubs: false,
      bookFiles: true,
    });
  } catch (err) {
    throw new SpreadsheetLoadError(`无法读取表格文件：${err && err.message ? err.message : err}`);
  }
  const fmt = ext === '.xls' ? 'xls' : 'xlsx';
  const wb = new WorkbookModel(fileName, fmt);
  if (fmt === 'xls') {
    wb.warnings.push(
      'XLS 格式无法读取公式表达式，公式单元格只能按缓存值比较；如需严格比较公式请两侧都使用 XLSX。',
    );
  }
  let orphanFormulas = new Map();
  if (fmt !== 'xls') {
    try {
      orphanFormulas = recoverFormulaOnlyCells(book, XLSX);
    } catch {
      orphanFormulas = new Map();
    }
  }
  for (const sheetName of book.SheetNames) {
    const ws = book.Sheets[sheetName];
    const sheet = new SheetModel(sheetName);
    sheet.defaultColWidth = 64;
    sheet.defaultRowHeight = 20;
    const ref = ws['!ref'];
    let range = null;
    if (ref) {
      try {
        range = XLSX.utils.decode_range(ref);
      } catch {
        range = null;
      }
    }
    if (!range) {
      wb.addSheet(sheet);
      continue;
    }
    let rows = range.e.r + 1;
    let cols = range.e.c + 1;
    if (rows > MAX_ROWS) {
      wb.warnings.push(`工作表“${sheetName}”行数 ${rows} 超过 ${MAX_ROWS}，已截断读取。`);
      rows = MAX_ROWS;
    }
    if (cols > MAX_COLS) {
      wb.warnings.push(`工作表“${sheetName}”列数 ${cols} 超过 ${MAX_COLS}，已截断读取。`);
      cols = MAX_COLS;
    }
    if (rows * cols > MAX_CELLS) {
      const allowedRows = Math.max(1, Math.floor(MAX_CELLS / cols));
      wb.warnings.push(`工作表“${sheetName}”单元格数超过 ${MAX_CELLS}，已截断到 ${allowedRows} 行。`);
      rows = Math.min(rows, allowedRows);
    }
    for (let r = 0; r < rows; r += 1) {
      for (let c = 0; c < cols; c += 1) {
        const addr = XLSX.utils.encode_cell({ r, c });
        const raw = ws[addr];
        if (!raw || (raw.v == null && !raw.f)) continue;
        const type = cellTypeFromSheetJs(raw);
        if (type === 'blank') continue;
        const formula = raw.f ? (raw.f.startsWith('=') ? raw.f : `=${raw.f}`) : null;
        sheet.set(r, c, {
          display: displayFor(raw, type),
          type,
          value: raw.v == null ? null : raw.v,
          formula,
          cached: raw.f ? (raw.v == null ? null : raw.v) : null,
          style: raw.z ? { numberFormat: String(raw.z) } : {},
        });
      }
    }
    sheet.nrows = rows;
    sheet.ncols = cols;
    const recovered = orphanFormulas.get(sheetName);
    if (recovered) {
      for (const [r, c, formula] of recovered) {
        if (r >= rows || c >= cols) continue;
        sheet.set(r, c, {
          display: formula,
          type: 'formula',
          value: null,
          formula,
          cached: null,
          style: {},
        });
      }
    }
    if (Array.isArray(ws['!merges'])) {
      for (const merge of ws['!merges']) {
        const row = merge.s.r;
        const col = merge.s.c;
        const rowSpan = merge.e.r - merge.s.r + 1;
        const colSpan = merge.e.c - merge.s.c + 1;
        if (row < rows && col < cols) sheet.merged.push([row, col, rowSpan, colSpan]);
      }
    }
    if (Array.isArray(ws['!cols'])) {
      ws['!cols'].forEach((item, col) => {
        const px = colWidthPx(item);
        if (px) sheet.colWidths.set(col, px);
        if (item && item.hidden) sheet.hiddenCols.add(col);
      });
    }
    if (Array.isArray(ws['!rows'])) {
      ws['!rows'].forEach((item, row) => {
        const px = rowHeightPx(item);
        if (px) sheet.rowHeights.set(row, px);
        if (item && item.hidden) sheet.hiddenRows.add(row);
      });
    }
    sheet.finalizeMerges();
    wb.addSheet(sheet);
  }
  return { workbook: wb, warnings: wb.warnings };
}

/* ------------------------------------------------------------------ 入口 */

export function extensionOf(fileName) {
  const match = /\.([^.]+)$/.exec(fileName || '');
  return match ? `.${match[1].toLowerCase()}` : '';
}

export function fileGroup(fileName) {
  const ext = extensionOf(fileName);
  if (ext === '.txt') return 'text';
  if (ext === '.csv') return 'csv';
  if (['.xlsx', '.xlsm', '.xls'].includes(ext)) return 'excel';
  return null;
}

export const SUPPORTED_EXTS = ['.txt', '.csv', '.xlsx', '.xlsm', '.xls'];

/** 读取一个文件（ArrayBuffer）为 { group, workbook?, csvText? }。 */
export async function loadAnyFile(file) {
  const group = fileGroup(file.name);
  if (!group) {
    throw new SpreadsheetLoadError(
      `不支持的文件类型：${file.name}。网页版支持 TXT / CSV / XLS / XLSX（图片 OCR 请使用桌面版）。`,
    );
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (group === 'text') return { group, bytes, fileName: file.name, file };
  if (group === 'csv') {
    const parsed = parseCsv(bytes, file.name);
    return { group, bytes, fileName: file.name, file, ...parsed };
  }
  const parsed = parseSpreadsheet(bytes, file.name, extensionOf(file.name));
  return { group, bytes, fileName: file.name, file, ...parsed };
}
