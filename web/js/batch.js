/*
 * 批量比较：一个基准 + 多个候选，任何候选不同即标红（或语义）。
 * 桌面版 src/engineerdiff/batch.py 的 JS 移植（不含图片 OCR）。
 */

import { compareLoaded } from './sheetcompare.js';
import { diffTextFiles } from './textdiff.js';
import { loadAnyFile, fileGroup } from './workbook.js';

export class BatchError extends Error {}

const SHEET_LABEL = (name) => `工作表「${name}」`;

function cellLocationLabel(sheet, row, col) {
  const letters = (() => {
    let n = col + 1;
    let out = '';
    while (n > 0) {
      const rem = (n - 1) % 26;
      out = String.fromCharCode(65 + rem) + out;
      n = Math.floor((n - 1) / 26);
    }
    return out;
  })();
  return sheet ? `${sheet}!${letters}${row + 1}` : `${letters}${row + 1}`;
}

function rightValue(diff) {
  if (!diff.right) return '（不存在）';
  return diff.right.text === '' ? '（空白）' : diff.right.text;
}

function leftValue(diff) {
  if (!diff.left) return '—';
  return diff.left.text === '' ? '（空白）' : diff.left.text;
}

function diffKeyAndLocation(diff, candidateIndex) {
  if (diff.group === 'text') {
    if (diff.title === '换行符差异') return { key: ['eol'], location: '换行符', hasBase: true, sortKey: [1e9, 0] };
    const leftLine = diff.left ? diff.left.line : null;
    const rightLine = diff.right ? diff.right.line : null;
    if (leftLine !== null && leftLine !== undefined) {
      return { key: ['text', leftLine], location: `第 ${leftLine} 行`, hasBase: true, sortKey: [leftLine, 0] };
    }
    if (rightLine !== null && rightLine !== undefined) {
      return {
        key: ['text-add', candidateIndex, rightLine],
        location: `第 ${rightLine} 行（仅候选新增）`,
        hasBase: false,
        sortKey: [rightLine, 1],
      };
    }
  }
  if (diff.group === 'sheet' || diff.kind === 'only_left' || diff.kind === 'only_right') {
    const loc = diff.left || diff.right;
    const sheet = loc ? loc.sheet : '';
    if (sheet) {
      return {
        key: ['sheet', sheet],
        location: SHEET_LABEL(sheet),
        hasBase: Boolean(diff.left),
        sortKey: [0, 0, 0, sheet],
      };
    }
  }
  if (diff.group === 'cell') {
    const loc = diff.left || diff.right;
    if (loc && loc.row !== null && loc.row !== undefined && loc.col !== null && loc.col !== undefined) {
      if (diff.left) {
        return {
          key: ['cell', loc.sheet, loc.row, loc.col],
          location: cellLocationLabel(loc.sheet, loc.row, loc.col),
          hasBase: true,
          sortKey: [1, loc.sheet || '', loc.row, loc.col],
        };
      }
      return {
        key: ['cell-add', candidateIndex, loc.sheet, loc.row, loc.col],
        location: `${cellLocationLabel(loc.sheet, loc.row, loc.col)}（仅候选）`,
        hasBase: false,
        sortKey: [1, loc.sheet || '', loc.row, loc.col],
      };
    }
  }
  return { key: ['other', candidateIndex, diff.id], location: diff.title || '差异', hasBase: false, sortKey: [9, 0] };
}

function diffItems(result) {
  return result.diffs || [];
}

/** 合并多次单对比较为批量行。 */
export function buildBatchResult(mode, baselineName, candidates) {
  const n = candidates.length;
  const rows = new Map();
  const warnings = [];
  candidates.forEach((candidate, index) => {
    if (candidate.error) {
      warnings.push(`${candidate.name}：${candidate.error}`);
      return;
    }
    for (const diff of diffItems(candidate.result)) {
      const { key, location, sortKey } = diffKeyAndLocation(diff, index);
      const keyStr = JSON.stringify(key);
      const loc = diff.left || diff.right;
      const sheetName = loc && loc.sheet ? loc.sheet : '';
      let row = rows.get(keyStr);
      if (!row) {
        row = {
          key,
          location,
          baseline: leftValue(diff),
          values: new Array(n).fill('—'),
          differs: new Array(n).fill(false),
          severity: diff.severity === 'uncertain' ? 'uncertain' : 'confirmed',
          note: diff.detail || '',
          sortKey,
          sheet: sheetName,
        };
        rows.set(keyStr, row);
      } else if (!row.baseline || row.baseline === '—') {
        if (diff.left) row.baseline = leftValue(diff);
      }
      if (!row.sheet && sheetName) row.sheet = sheetName;
      row.values[index] = rightValue(diff);
      row.differs[index] = true;
      if (diff.severity === 'uncertain') {
        if (row.severity !== 'confirmed') row.severity = 'uncertain';
      } else {
        row.severity = 'confirmed';
      }
      if (!row.note && diff.detail) row.note = diff.detail;
    }
  });

  const list = [...rows.values()];
  for (const row of list) {
    const hasBase = row.baseline !== '—';
    for (let i = 0; i < n; i += 1) {
      if (row.differs[i]) continue;
      row.values[i] = hasBase ? row.baseline : '—';
    }
  }
  list.sort((a, b) => compareSortKey(a.sortKey, b.sortKey) || String(a.location).localeCompare(String(b.location)));
  return {
    mode,
    baseline: baselineName,
    candidates,
    rows: list,
    warnings: [...new Set(warnings)],
    elapsed: 0,
    differentCount: list.length,
    fileCount: n + 1,
  };
}

function compareSortKey(a, b) {
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const av = a[i];
    const bv = b[i];
    if (av === bv) continue;
    if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv));
    return (av || 0) - (bv || 0);
  }
  return 0;
}

/**
 * 执行批量比较。
 * @param {File} baselineFile
 * @param {File[]} candidateFiles
 * @param {object} settings {text, sheet}
 * @param {{progress?:Function,onCandidate?:Function,cancel?:Function}} hooks
 */
export async function runBatch(baselineFile, candidateFiles, settings = {}, hooks = {}) {
  const { progress = () => {}, onCandidate = null, cancel = () => false } = hooks;
  if (!candidateFiles.length) throw new BatchError('请至少添加一个待比较文件。');
  const group = fileGroup(baselineFile.name);
  if (!group) throw new BatchError(`不支持的文件类型：${baselineFile.name}`);
  for (const file of candidateFiles) {
    const candidateGroup = fileGroup(file.name);
    if (!candidateGroup) throw new BatchError(`不支持的文件类型：${file.name}`);
    if (candidateGroup !== group) {
      throw new BatchError('批量比较要求所有文件类型一致（TXT / CSV / XLSX / XLS 不能混合）。');
    }
  }
  const started = performance.now();
  progress(2, '读取基准文件…');
  const baseline = await loadAnyFile(baselineFile);
  const candidates = [];
  const total = candidateFiles.length;
  for (let index = 0; index < total; index += 1) {
    if (cancel()) throw new Error('用户取消了批量比较');
    const file = candidateFiles[index];
    progress(4 + Math.floor((index * 90) / total), `正在比较 ${index + 1}/${total}：${file.name}`);
    await nextTick();
    try {
      const loaded = await loadAnyFile(file);
      const result = compareLoaded(baseline, loaded, settings);
      const candidate = {
        name: file.name,
        file,
        result,
        error: '',
        diffCount: diffItems(result).length,
      };
      candidates.push(candidate);
      if (onCandidate) onCandidate(index, candidate);
    } catch (err) {
      const candidate = {
        name: file.name,
        file,
        result: null,
        error: err && err.message ? err.message : String(err),
        diffCount: 0,
      };
      candidates.push(candidate);
      if (onCandidate) onCandidate(index, candidate);
    }
    await nextTick();
  }
  const mode = group === 'text' ? 'text' : group === 'csv' ? 'csv' : 'excel';
  const batch = buildBatchResult(mode, baselineFile.name, candidates);
  batch.baselineFile = baselineFile;
  batch.candidateFiles = candidateFiles;
  const extraWarnings = [];
  for (const candidate of candidates) {
    if (candidate.result && candidate.result.warnings) {
      for (const warning of candidate.result.warnings) extraWarnings.push(`${candidate.name}：${warning}`);
    }
  }
  batch.warnings = [...new Set([...batch.warnings, ...extraWarnings])];
  batch.baselineWarnings = baseline.warnings || [];
  batch.elapsed = (performance.now() - started) / 1000;
  progress(100, `批量比较完成：${batch.fileCount} 个文件，${batch.differentCount} 处差异`);
  return batch;
}

function nextTick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** 重新以新的基准执行（切换基准时复用已读取的文件对象）。 */
export async function rerunBatchWithBaseline(batch, baselineFile, settings, hooks) {
  const files = [batch.baselineFile, ...batch.candidateFiles].filter(Boolean);
  const candidates = files.filter((file) => file !== baselineFile);
  return runBatch(baselineFile, candidates, settings, hooks || {});
}

export { diffTextFiles };
