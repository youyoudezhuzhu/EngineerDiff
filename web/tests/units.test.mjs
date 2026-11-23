/* 纯逻辑单元测试：编码识别、CSV 解析、数字/日期格式、批量键构造。 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { decodeText } from '../js/textdiff.js';
import {
  parseCsvText,
  sniffDelimiter,
  parseCsv,
  formatNumberValue,
  formatDateValue,
  columnLetter,
  extensionOf,
  fileGroup,
} from '../js/workbook.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
globalThis.XLSX = require(path.join(HERE, '..', 'vendor', 'xlsx.full.min.js'));

test('decodeText handles BOM and common encodings', () => {
  const utf8 = new TextEncoder().encode('版本：1.2.3\n');
  assert.equal(decodeText(utf8).text, '版本：1.2.3\n');

  const withBom = new Uint8Array([0xef, 0xbb, 0xbf, ...utf8]);
  const bom = decodeText(withBom);
  assert.equal(bom.encoding, 'UTF-8 BOM');
  assert.equal(bom.text, '版本：1.2.3\n');

  const text = 'TEXT 测试\n';
  const utf16 = [0xff, 0xfe];
  for (const ch of text) {
    const code = ch.codePointAt(0);
    utf16.push(code & 0xff, code >> 8);
  }
  const decoded = decodeText(new Uint8Array(utf16));
  assert.equal(decoded.encoding, 'UTF-16');
  assert.equal(decoded.text, text);

  // GBK 编码的“中文”：B8 F6 B2 E2
  const gbk = new Uint8Array([0xb0, 0xb2, 0xc8, 0xab, 0x0d, 0x0a]);
  const gbkResult = decodeText(gbk);
  assert.equal(gbkResult.text, '安全\r\n');
  assert.ok(gbkResult.encoding.includes('GB18030'));
});

test('CSV parser supports quotes, newlines and delimiter sniffing', () => {
  const rows = parseCsvText('a,b\n"x,1","y\nz"\n"he said ""hi""",2\n');
  assert.deepEqual(rows, [
    ['a', 'b'],
    ['x,1', 'y\nz'],
    ['he said "hi"', '2'],
  ]);
  assert.equal(sniffDelimiter('a;b;c\n1;2;3\n').delimiter, ';');
  assert.equal(sniffDelimiter('a\tb\n1\t2\n').delimiter, '\t');
  assert.equal(sniffDelimiter('a,b\n1,2\n').delimiter, ',');
});

test('CSV workbook preserves multiline cells and column widths', () => {
  const bytes = new TextEncoder().encode('名称,说明,值\nA,"第一行\n第二行",1.5\n');
  const { workbook, delimiter } = parseCsv(bytes, 'demo.csv');
  assert.equal(delimiter, ',');
  const sheet = workbook.sheets[0];
  assert.equal(sheet.name, 'CSV');
  assert.equal(sheet.nrows, 2);
  assert.equal(sheet.ncols, 3);
  assert.equal(sheet.get(1, 1).display, '第一行\n第二行');
  assert.ok(sheet.rowHeights.get(1) > 20);
  assert.ok(sheet.colWidths.get(1) >= 180);
});

test('number and date formatting mirrors desktop loader', () => {
  assert.equal(formatNumberValue(0.6666666666666666, '0.00'), '0.67');
  assert.equal(formatNumberValue(220, '0.00'), '220.00');
  assert.equal(formatNumberValue(1234.5, '#,##0.00'), '1,234.50');
  assert.equal(formatNumberValue(0.125, '0%'), '13%');
  assert.equal(formatNumberValue(2, 'General'), '2');
  assert.equal(formatNumberValue(1.5, null), '1.5');
  const date = new Date(2024, 0, 2, 3, 4, 5);
  assert.equal(formatDateValue(date, 'yyyy-mm-dd'), '2024-01-02');
  assert.equal(formatDateValue(date, 'yyyy-mm-dd hh:mm:ss'), '2024-01-02 03:04:05');
  assert.equal(formatDateValue(date, 'hh:mm:ss'), '03:04:05');
});

test('column letters, extensions and file groups', () => {
  assert.equal(columnLetter(0), 'A');
  assert.equal(columnLetter(25), 'Z');
  assert.equal(columnLetter(26), 'AA');
  assert.equal(columnLetter(701), 'ZZ');
  assert.equal(extensionOf('A.XLSX'), '.xlsx');
  assert.equal(fileGroup('a.txt'), 'text');
  assert.equal(fileGroup('a.csv'), 'csv');
  assert.equal(fileGroup('a.xls'), 'excel');
  assert.equal(fileGroup('a.png'), null);
});
