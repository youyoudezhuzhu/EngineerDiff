/*
 * 文本差异一致性测试：JS 移植版 vs 桌面版 Python 引擎。
 *
 * 运行：node --test web/tests/
 * 依赖：系统 python3（仅标准库，不需要安装项目依赖）。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { compareTexts } from '../js/textdiff.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REFERENCE = path.join(HERE, 'reference', 'text_reference.py');

function normalizeSide(side) {
  return {
    encoding: 'UTF-8',
    lineEnding: side.lineEnding,
    trailingNewline: side.trailingNewline,
    originalLineCount: side.originalLineCount,
    lines: side.lines.map((line) => ({
      text: line.text,
      lineNo: line.lineNo,
      status: line.status,
      spans: line.spans.map((span) => [span[0], span[1]]),
      diffId: line.diffId,
    })),
  };
}

function normalizeDiff(diff) {
  const loc = (location) => {
    if (location === null) return null;
    return {
      line: location.line,
      text: location.text,
      spans: location.spans.map((span) => [span[0], span[1]]),
      placeholder: Boolean(location.extra && location.extra.placeholder),
      displayRow: location.extra ? location.extra.displayRow : null,
    };
  };
  return {
    id: diff.id,
    kind: diff.kind,
    title: diff.title,
    detail: diff.detail,
    left: loc(diff.left),
    right: loc(diff.right),
  };
}

function jsResult(c) {
  const result = compareTexts(c.left, c.right, c.options || {});
  return {
    name: c.name,
    left: normalizeSide(result.left),
    right: normalizeSide(result.right),
    diffs: result.diffs.map(normalizeDiff),
  };
}

function pythonResults(cases) {
  const stdout = execFileSync('python3', [REFERENCE], {
    input: JSON.stringify({ cases }),
    encoding: 'utf-8',
    maxBuffer: 64 * 1024 * 1024,
  });
  return JSON.parse(stdout).cases;
}

const CASES = [
  { name: 'single-line-change', left: 'a\nb\n', right: 'a\nc\n' },
  { name: 'cjk-version', left: '软件版本：V1.2.3\n保护定值：1.50 A\n', right: '软件版本：V1.2.4\n保护定值：1.80 A\n' },
  { name: 'added-line', left: 'a\nb\n', right: 'a\nnew\nb\n' },
  { name: 'removed-line', left: 'a\nb\nc\n', right: 'a\nc\n' },
  {
    name: 'replace-block-dissimilar',
    left: '标题\n第一段\n第二段\n第三段\n尾部\n',
    right: '标题\n完全不同的内容\n尾部\n',
  },
  {
    name: 'duplicate-lines',
    left: '0.00\n0.00\n1.00\n0.00\n',
    right: '0.00\n1.00\n0.00\n0.00\n',
  },
  { name: 'empty-left', left: '', right: 'a\nb\n' },
  { name: 'empty-both', left: '', right: '' },
  { name: 'trailing-newline', left: 'a\n', right: 'a' },
  { name: 'crlf-vs-lf', left: 'a\r\nb\r\n', right: 'a\nb\n' },
  {
    name: 'ignore-line-endings',
    left: 'a\r\nb\r\n',
    right: 'a\nb\n',
    options: { ignoreLineEndings: true },
  },
  { name: 'ignore-outer-space', left: '  a  \n', right: 'a\n', options: { ignoreOuterSpace: true } },
  { name: 'ignore-all-space', left: 'a b c\n', right: 'abc\n', options: { ignoreAllSpace: true } },
  { name: 'ignore-case', left: 'Version A\n', right: 'version a\n', options: { ignoreCase: true } },
  {
    name: 'mixed-eol-and-change',
    left: '装置版本 1.2.3\r\n投入 1.50\r\n',
    right: '装置版本 1.2.4\n投入 1.50 A\n',
  },
  {
    name: 'many-lines',
    left: Array.from({ length: 120 }, (_, i) => `参数${i}=${i}.00`).join('\n') + '\n',
    right:
      Array.from({ length: 120 }, (_, i) => (i % 17 === 0 ? `参数${i}=${i}.05` : `参数${i}=${i}.00`)).join('\n') +
      '\n',
  },
];

test('text diff matches desktop engine exactly', () => {
  const expected = pythonResults(CASES);
  for (let i = 0; i < CASES.length; i += 1) {
    const actual = jsResult(CASES[i]);
    assert.deepEqual(actual, expected[i], `case ${CASES[i].name} differs from Python engine`);
  }
});
