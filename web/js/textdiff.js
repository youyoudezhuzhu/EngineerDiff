/*
 * 文本差异引擎 —— 桌面版 src/engineerdiff/textdiff/engine.py 的 JS 移植。
 *
 * 行为对齐点：
 *   - 编码识别：BOM → UTF-8(严格) → GB18030(要求出现 CJK 且可打印率 > 85%) → 回退；
 *   - 行级对齐使用 SequenceMatcher（autojunk 阈值与 Python 一致）；
 *   - 替换块内使用动态规划对齐相似行，避免无关行硬配对；
 *   - 字符级 span 使用「归一化文本 → 原文本索引」映射；
 *   - 换行符差异单独生成一条差异，不计入每行。
 */

import { SequenceMatcher } from './seqmatcher.js';

const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f]/;
const NON_PRINTABLE_RE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/u;

export const DEFAULT_TEXT_OPTIONS = {
  ignoreLineEndings: false,
  ignoreOuterSpace: false,
  ignoreAllSpace: false,
  ignoreCase: false,
};

/* ------------------------------------------------------------------ 编码 */

function decodeUtf32(bytes, littleEndian) {
  if (bytes.length % 4 !== 0) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let out = '';
  for (let i = 0; i + 3 < bytes.length; i += 4) {
    const code = view.getUint32(i, littleEndian);
    if (code > 0x10ffff) return null;
    out += String.fromCodePoint(code);
  }
  return out;
}

function decodeWith(bytes, encoding, fatal = false) {
  return new TextDecoder(encoding, { fatal }).decode(bytes);
}

/** 识别常见文本编码，返回 { text, encoding, warnings }。 */
export function decodeText(bytes) {
  const warnings = [];
  if (!bytes || bytes.length === 0) {
    return { text: '', encoding: 'UTF-8', warnings };
  }
  const b = bytes;
  if (b.length >= 3 && b[0] === 0xef && b[1] === 0xbb && b[2] === 0xbf) {
    return {
      text: decodeWith(b.subarray(3), 'utf-8'),
      encoding: 'UTF-8 BOM',
      warnings,
    };
  }
  if (
    b.length >= 4 &&
    ((b[0] === 0xff && b[1] === 0xfe && b[2] === 0 && b[3] === 0) ||
      (b[0] === 0 && b[1] === 0 && b[2] === 0xfe && b[3] === 0xff))
  ) {
    const le = b[0] === 0xff;
    const text = decodeUtf32(b.subarray(4), le);
    if (text !== null) return { text, encoding: 'UTF-32', warnings };
  }
  if (b.length >= 2 && b[0] === 0xff && b[1] === 0xfe) {
    try {
      return { text: decodeWith(b.subarray(2), 'utf-16le'), encoding: 'UTF-16', warnings };
    } catch {
      /* 继续尝试其他编码 */
    }
  }
  if (b.length >= 2 && b[0] === 0xfe && b[1] === 0xff) {
    try {
      return { text: decodeWith(b.subarray(2), 'utf-16be'), encoding: 'UTF-16', warnings };
    } catch {
      /* 继续尝试其他编码 */
    }
  }
  try {
    return { text: decodeWith(b, 'utf-8', true), encoding: 'UTF-8', warnings };
  } catch {
    /* 不是合法 UTF-8 */
  }
  try {
    const gb = decodeWith(b, 'gb18030');
    const cjk = (gb.match(CJK_RE) || []).length;
    let printable = 0;
    for (const ch of gb) {
      if (ch === '\r' || ch === '\n' || ch === '\t' || !NON_PRINTABLE_RE.test(ch)) printable += 1;
    }
    if (cjk > 0 && printable / Math.max(1, [...gb].length) > 0.85) {
      warnings.push('未检测到 BOM，按 GB18030/GBK 解码。');
      return { text: gb, encoding: 'GB18030/GBK', warnings };
    }
  } catch {
    /* 继续回退 */
  }
  try {
    const gb = decodeWith(b, 'gb18030');
    warnings.push('编码无法可靠识别，已按 GB18030 解码，可能存在乱码。');
    return { text: gb, encoding: 'GB18030（回退）', warnings };
  } catch {
    const text = decodeWith(b, 'latin1');
    warnings.push('编码无法识别，已按 Latin-1 回退显示，请人工核对。');
    return { text, encoding: 'Latin-1（回退）', warnings };
  }
}

/* ------------------------------------------------------------------ 行结构 */

/** 保留每行内容与换行符；返回 { lines:[{text, ending}], trailing }。 */
export function splitPhysicalLines(text) {
  if (text === '') return { lines: [], trailing: false };
  const parts = text.split(/(\r\n|\n|\r)/);
  const lines = [];
  let i = 0;
  while (i + 1 < parts.length) {
    lines.push({ text: parts[i], ending: parts[i + 1] });
    i += 2;
  }
  if (i < parts.length && parts[i] !== '') lines.push({ text: parts[i], ending: '' });
  const trailing = /[\r\n]$/.test(text);
  return { lines, trailing };
}

export /** 近似 Python repr(str)，用于生成与桌面版一致的差异说明。 */
function pyRepr(text) {
  let out = "'";
  for (const ch of text) {
    if (ch === "'") out += "\\'";
    else if (ch === '\\') out += '\\\\';
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else {
      const code = ch.codePointAt(0);
      if (code < 0x20 || code === 0x7f) out += `\\x${code.toString(16).padStart(2, '0')}`;
      else out += ch;
    }
  }
  return `${out}'`;
}

function lineEndingSignature(lines) {
  const set = new Set();
  for (const line of lines) if (line.ending) set.add(line.ending);
  return [...set].sort().join('');
}

/** 返回 [归一化文本, 归一化字符 → 原字符索引]。 */
function normalizeLine(text, opts) {
  const n = text.length;
  let start = 0;
  let end = n;
  if (opts.ignoreOuterSpace) {
    while (start < end && /\s/.test(text[start])) start += 1;
    while (end > start && /\s/.test(text[end - 1])) end -= 1;
  }
  const indices = [];
  const chars = [];
  for (let i = start; i < end; i += 1) {
    const ch = text[i];
    if (opts.ignoreAllSpace && /\s/.test(ch)) continue;
    if (opts.ignoreCase) {
      const low = ch.toLowerCase();
      for (const c of low) {
        chars.push(c);
        indices.push(i);
      }
    } else {
      chars.push(ch);
      indices.push(i);
    }
  }
  return [chars.join(''), indices];
}

function mergeRanges(ranges) {
  if (!ranges.length) return [];
  const sorted = [...ranges].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [sorted[0]];
  for (const [s, e] of sorted.slice(1)) {
    const last = merged[merged.length - 1];
    if (s <= last[1]) last[1] = Math.max(last[1], e);
    else merged.push([s, e]);
  }
  return merged;
}

function charSpans(leftText, rightText, opts) {
  const [na, ma] = normalizeLine(leftText, opts);
  const [nb, mb] = normalizeLine(rightText, opts);
  if (na === nb) return [[], []];
  const spansL = [];
  const spansR = [];
  const sm = new SequenceMatcher(null, na, nb, false);
  for (const [tag, i1, i2, j1, j2] of sm.getOpcodes()) {
    if (tag === 'equal') continue;
    if (i1 < i2 && ma.length) spansL.push([ma[i1], ma[i2 - 1] + 1]);
    if (j1 < j2 && mb.length) spansR.push([mb[j1], mb[j2 - 1] + 1]);
  }
  return [mergeRanges(spansL), mergeRanges(spansR)];
}

function ratio(a, b) {
  if (!a && !b) return 1.0;
  if (!a || !b) return 0.0;
  if (Math.min(a.length, b.length) * 4 < Math.max(a.length, b.length)) return 0.0;
  return new SequenceMatcher(null, a, b, true).quickRatio();
}

const ALIGN_BLOCK_LIMIT = 900;

/**
 * 替换块内用 DP 对齐相似行；返回 [["pair",i,j] | ["del",i,null] | ["ins",null,j]]。
 * 超大块使用线性兜底，避免浏览器长时间无响应（桌面版无此限制，差异会记录在警告里）。
 */
function alignReplaceBlock(left, right, opts, warnings) {
  const n = left.length;
  const m = right.length;
  if (n === 1 && m === 1) return [['pair', 0, 0]];
  if (n * m > ALIGN_BLOCK_LIMIT * ALIGN_BLOCK_LIMIT) {
    warnings.push('存在超大的整块替换，已使用快速行对齐；字符级差异仍然准确。');
    const ops = [];
    const common = Math.min(n, m);
    for (let i = 0; i < common; i += 1) ops.push(['pair', i, i]);
    for (let i = common; i < n; i += 1) ops.push(['del', i, null]);
    for (let j = common; j < m; j += 1) ops.push(['ins', null, j]);
    return ops;
  }
  const ln = left.map((x) => normalizeLine(x.text, opts)[0]);
  const rn = right.map((x) => normalizeLine(x.text, opts)[0]);
  const sim = [];
  for (let i = 0; i < n; i += 1) {
    const row = new Array(m);
    for (let j = 0; j < m; j += 1) row[j] = ratio(ln[i], rn[j]);
    sim.push(row);
  }
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0.0));
  const bt = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(''));
  for (let i = 1; i <= n; i += 1) bt[i][0] = 'del';
  for (let j = 1; j <= m; j += 1) bt[0][j] = 'ins';
  for (let i = 1; i <= n; i += 1) {
    for (let j = 1; j <= m; j += 1) {
      const pairScore = dp[i - 1][j - 1] + Math.max(0.0, sim[i - 1][j - 1] - 0.3);
      const delScore = dp[i - 1][j];
      const insScore = dp[i][j - 1];
      const best = Math.max(pairScore, delScore, insScore);
      dp[i][j] = best;
      if (best === pairScore && (sim[i - 1][j - 1] > 0.0 || n === m)) bt[i][j] = 'pair';
      else if (best === delScore) bt[i][j] = 'del';
      else bt[i][j] = 'ins';
    }
  }
  const ops = [];
  let i = n;
  let j = m;
  while (i > 0 || j > 0) {
    const op = bt[i][j];
    if (op === 'pair') {
      ops.push(['pair', i - 1, j - 1]);
      i -= 1;
      j -= 1;
    } else if (op === 'del' || j === 0) {
      ops.push(['del', i - 1, null]);
      i -= 1;
    } else {
      ops.push(['ins', null, j - 1]);
      j -= 1;
    }
  }
  ops.reverse();
  return ops;
}

/* ------------------------------------------------------------------ 比较 */

/**
 * 比较两段文本。
 * @returns {{left,right,diffs,warnings,settings}}
 */
export function compareTexts(leftText, rightText, options = {}) {
  const opts = { ...DEFAULT_TEXT_OPTIONS, ...options };
  const leftOpts = opts;
  const { lines: llines, trailing: ltrail } = splitPhysicalLines(leftText);
  const { lines: rlines, trailing: rtrail } = splitPhysicalLines(rightText);

  const leftLines = [];
  const rightLines = [];
  const diffs = [];
  const warnings = [];
  let counter = 0;

  const newDiff = ({
    kind,
    title,
    leftLine,
    rightLine,
    leftNo,
    rightNo,
    leftSpans,
    rightSpans,
    displayRow,
    severity = 'confirmed',
    detail = '',
  }) => {
    counter += 1;
    return {
      id: `text:${counter}`,
      kind,
      severity,
      title,
      group: 'text',
      detail,
      left: {
        kind: 'text',
        line: leftNo,
        text: leftLine ? leftLine.text : '',
        spans: leftSpans,
        extra: { displayRow, placeholder: leftLine === null },
      },
      right: {
        kind: 'text',
        line: rightNo,
        text: rightLine ? rightLine.text : '',
        spans: rightSpans,
        extra: { displayRow, placeholder: rightLine === null },
      },
    };
  };

  const addRow = ({
    leftLine,
    rightLine,
    leftNo,
    rightNo,
    kind,
    leftSpans,
    rightSpans,
    title,
  }) => {
    const rowIdx = leftLines.length;
    let diffId = null;
    if (kind !== 'modified') {
      const item = newDiff({
        kind, title, leftLine, rightLine, leftNo, rightNo, leftSpans, rightSpans, displayRow: rowIdx,
      });
      diffs.push(item);
      diffId = item.id;
    } else if (leftSpans.length || rightSpans.length) {
      const item = newDiff({
        kind, title, leftLine, rightLine, leftNo, rightNo, leftSpans, rightSpans, displayRow: rowIdx,
      });
      diffs.push(item);
      diffId = item.id;
    }

    if (leftLine === null) {
      leftLines.push({ text: '', lineNo: null, status: 'placeholder', spans: [], diffId });
    } else {
      let status;
      if (!(leftSpans.length || rightSpans.length) && kind === 'modified') status = 'equal';
      else status = kind === 'modified' ? 'modified' : 'removed';
      if (kind === 'added') status = 'equal';
      leftLines.push({ text: leftLine.text, lineNo: leftNo, status, spans: leftSpans, diffId });
    }
    if (rightLine === null) {
      rightLines.push({ text: '', lineNo: null, status: 'placeholder', spans: [], diffId });
    } else {
      let status;
      if (!(leftSpans.length || rightSpans.length) && kind === 'modified') status = 'equal';
      else status = kind === 'modified' ? 'modified' : 'added';
      if (kind === 'removed') status = 'equal';
      rightLines.push({ text: rightLine.text, lineNo: rightNo, status, spans: rightSpans, diffId });
    }
  };

  const lkeys = llines.map((x) => normalizeLine(x.text, leftOpts)[0]);
  const rkeys = rlines.map((x) => normalizeLine(x.text, leftOpts)[0]);
  const autoJunk = lkeys.length + rkeys.length > 60000;
  if (autoJunk) warnings.push('文件行数较多，已启用快速行匹配；差异定位仍以字符比较为准。');
  const sm = new SequenceMatcher(null, lkeys, rkeys, autoJunk);

  for (const [tag, i1, i2, j1, j2] of sm.getOpcodes()) {
    if (tag === 'equal') {
      for (let k = 0; k < i2 - i1; k += 1) {
        const li = i1 + k;
        const rj = j1 + k;
        addRow({
          leftLine: llines[li], rightLine: rlines[rj], leftNo: li + 1, rightNo: rj + 1,
          kind: 'modified', leftSpans: [], rightSpans: [], title: '相同',
        });
      }
    } else if (tag === 'delete') {
      for (let li = i1; li < i2; li += 1) {
        addRow({
          leftLine: llines[li], rightLine: null, leftNo: li + 1, rightNo: null,
          kind: 'removed', leftSpans: [[0, llines[li].text.length]], rightSpans: [],
          title: `第 ${li + 1} 行仅左侧存在`,
        });
      }
    } else if (tag === 'insert') {
      for (let rj = j1; rj < j2; rj += 1) {
        addRow({
          leftLine: null, rightLine: rlines[rj], leftNo: null, rightNo: rj + 1,
          kind: 'added', leftSpans: [], rightSpans: [[0, rlines[rj].text.length]],
          title: `第 ${rj + 1} 行仅右侧存在`,
        });
      }
    } else {
      const ops = alignReplaceBlock(llines.slice(i1, i2), rlines.slice(j1, j2), leftOpts, warnings);
      for (const [op, ii, jj] of ops) {
        const li = ii === null ? null : i1 + ii;
        const rj = jj === null ? null : j1 + jj;
        if (op === 'pair') {
          const lp = llines[li];
          const rp = rlines[rj];
          const [leftSpans, rightSpans] = charSpans(lp.text, rp.text, leftOpts);
          addRow({
            leftLine: lp, rightLine: rp, leftNo: li + 1, rightNo: rj + 1,
            kind: 'modified', leftSpans, rightSpans,
            title: `第 ${li + 1} 行 / 第 ${rj + 1} 行内容不同`,
          });
        } else if (op === 'del') {
          addRow({
            leftLine: llines[li], rightLine: null, leftNo: li + 1, rightNo: null,
            kind: 'removed', leftSpans: [[0, llines[li].text.length]], rightSpans: [],
            title: `第 ${li + 1} 行仅左侧存在`,
          });
        } else {
          addRow({
            leftLine: null, rightLine: rlines[rj], leftNo: null, rightNo: rj + 1,
            kind: 'added', leftSpans: [], rightSpans: [[0, rlines[rj].text.length]],
            title: `第 ${rj + 1} 行仅右侧存在`,
          });
        }
      }
    }
  }

  if (!opts.ignoreLineEndings) {
    const lsig = lineEndingSignature(llines);
    const rsig = lineEndingSignature(rlines);
    if (lsig !== rsig || ltrail !== rtrail) {
      counter += 1;
      const details = [];
      if (lsig !== rsig) details.push(`换行符类型不同：左侧 ${pyRepr(lsig)}，右侧 ${pyRepr(rsig)}`);
      if (ltrail !== rtrail) {
        details.push(`文件末尾换行不同：左侧${ltrail ? '有' : '无'}，右侧${rtrail ? '有' : '无'}`);
      }
      const firstLeft = llines.find((x) => x.text) || null;
      const firstRight = rlines.find((x) => x.text) || null;
      const eolId = `text:${counter}`;
      diffs.push({
        id: eolId,
        kind: 'modified',
        severity: 'confirmed',
        title: '换行符差异',
        group: 'text',
        detail: details.join('；'),
        left: {
          kind: 'text', line: firstLeft ? 1 : null, text: firstLeft ? firstLeft.text : '',
          spans: [], extra: { displayRow: 0 },
        },
        right: {
          kind: 'text', line: firstRight ? 1 : null, text: firstRight ? firstRight.text : '',
          spans: [], extra: { displayRow: 0 },
        },
      });
      for (const lines of [leftLines, rightLines]) {
        const target = lines.find((line) => line.lineNo !== null);
        if (target) target.diffId = eolId;
      }
    }
  }

  return {
    kind: 'text',
    left: {
      path: '',
      encoding: 'UTF-8',
      lines: leftLines,
      lineEnding: lineEndingSignature(llines),
      trailingNewline: ltrail,
      originalLineCount: llines.length,
    },
    right: {
      path: '',
      encoding: 'UTF-8',
      lines: rightLines,
      lineEnding: lineEndingSignature(rlines),
      trailingNewline: rtrail,
      originalLineCount: rlines.length,
    },
    diffs,
    warnings,
    settings: opts,
  };
}

export function diffTextFiles(leftBytes, rightBytes, options = {}) {
  const left = decodeText(leftBytes);
  const right = decodeText(rightBytes);
  const result = compareTexts(left.text, right.text, options);
  result.left.encoding = left.encoding;
  result.right.encoding = right.encoding;
  result.warnings = [
    ...left.warnings.map((w) => `左侧：${w}`),
    ...right.warnings.map((w) => `右侧：${w}`),
    ...result.warnings,
  ];
  return result;
}
