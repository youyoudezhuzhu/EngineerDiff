/*
 * 批量比较一致性：JS 移植版 vs 桌面版 Python 引擎。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const REFERENCE = path.join(HERE, 'reference', 'batch_reference.py');

const require = createRequire(import.meta.url);
globalThis.XLSX = require(path.join(HERE, '..', 'vendor', 'xlsx.full.min.js'));

const { runBatch } = await import('../js/batch.js');
const { setTextComparator } = await import('../js/sheetcompare.js');
const { diffTextFiles } = await import('../js/textdiff.js');
setTextComparator(diffTextFiles);

function fakeFile(buffer, name) {
  return {
    name,
    arrayBuffer: async () => buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength),
  };
}

async function fileAt(absPath) {
  const buffer = await fs.readFile(absPath);
  return fakeFile(new Uint8Array(buffer), path.basename(absPath));
}

const CASES = [
  {
    name: 'text-three',
    baseline: 'samples/text/装置参数_A.txt',
    candidates: ['samples/text/装置参数_B.txt', 'samples/text/装置参数_C.txt'],
  },
  {
    name: 'csv-pair',
    baseline: 'tests/fixtures/sample_left.csv',
    candidates: ['tests/fixtures/sample_right.csv'],
  },
  {
    name: 'excel-pair',
    baseline: 'samples/excel/装置参数_A.xlsx',
    candidates: ['samples/excel/装置参数_B.xlsx'],
  },
];

test('batch compare matches desktop engine exactly', async () => {
  const payload = {
    cases: CASES.map((item) => ({
      name: item.name,
      baseline: path.join(ROOT, item.baseline),
      candidates: item.candidates.map((candidate) => path.join(ROOT, candidate)),
    })),
  };
  const expected = JSON.parse(
    execFileSync('python3', [REFERENCE], {
      input: JSON.stringify(payload),
      encoding: 'utf-8',
      maxBuffer: 64 * 1024 * 1024,
    }),
  ).cases;

  for (let i = 0; i < CASES.length; i += 1) {
    const item = CASES[i];
    const baseline = await fileAt(path.join(ROOT, item.baseline));
    const candidates = [];
    for (const relative of item.candidates) candidates.push(await fileAt(path.join(ROOT, relative)));
    const batch = await runBatch(baseline, candidates, {});
    const actual = {
      name: item.name,
      mode: batch.mode,
      rows: batch.rows.map((row) => ({
        location: row.location,
        baseline: row.baseline,
        values: row.values,
        differs: row.differs,
        severity: row.severity,
        sheet: row.sheet,
        note: row.note,
      })),
      differentCount: batch.differentCount,
      fileCount: batch.fileCount,
      candidates: batch.candidates.map((candidate) => ({
        name: candidate.name,
        diffCount: candidate.diffCount,
        error: candidate.error,
      })),
    };
    assert.deepEqual(actual, expected[i], `case ${item.name} differs from Python engine`);
  }
});
