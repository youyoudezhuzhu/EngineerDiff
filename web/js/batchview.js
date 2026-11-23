/*
 * 批量比较页：一个基准 + 多个候选，任何候选不同即标红。
 * 布局与桌面版 1.2.7 对齐：同样式的“基准 / 全部”文件框，操作按钮在“全部”标题行。
 */

import { el, clear, formatBytes, icon, copyText, downloadText } from './dom.js';
import { fileGroup, SUPPORTED_EXTS } from './workbook.js';
import { runBatch } from './batch.js';

export class BatchView {
  constructor(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    this.files = [];
    this.baseline = null;
    this.result = null;
    this.running = false;
    this.cancelled = false;
    this.showEqual = true;
    this.collapsedGroups = new Set();
    this._build();
  }

  init() {}

  refreshSettings() {
    if (this.files.length && this.baseline) void this.start();
  }

  refreshTheme() {
    /* 颜色全部走 CSS 变量，无需重绘 */
  }

  onShow() {}

  /* ---------------------------------------------------------------- 构建 */

  _build() {
    const root = this.root;
    clear(root);
    const baselineBox = el('section', { class: 'import-box baseline-box' });
    const baselineHead = el('div', { class: 'import-head' },
      el('span', { class: 'import-title' }, '基准：'),
      el('span', { class: 'import-spacer' }),
      el('button', { class: 'btn ghost small', type: 'button', dataset: { action: 'pick-baseline' } }, '选择'),
    );
    const baselineList = el('div', { class: 'import-list', role: 'list' });
    baselineBox.append(baselineHead, baselineList);

    const allBox = el('section', { class: 'import-box all-box' });
    const allHead = el('div', { class: 'import-head' },
      el('span', { class: 'import-title' }, '全部：'),
      el('span', { class: 'import-count', id: 'batch-count' }, '共 0 个文件'),
      el('span', { class: 'import-spacer' }),
      el('button', { class: 'btn ghost small', type: 'button', dataset: { action: 'remove' } }, '移除', el('span', { class: 'long' }, '选中')),
      el('button', { class: 'btn ghost small', type: 'button', dataset: { action: 'clear' } }, '清空'),
      el('button', { class: 'btn ghost small', type: 'button', dataset: { action: 'set-baseline' } }, '设为基准'),
      el('button', { class: 'btn ghost small', type: 'button', dataset: { action: 'pick-all' } }, '选择'),
    );
    const allList = el('div', { class: 'import-list', role: 'list' });
    allBox.append(allHead, allList);

    const actions = el('div', { class: 'batch-actions' },
      el('span', { class: 'batch-hint', id: 'batch-status' }, '导入 2 个及以上同类型文件后开始比较。'),
      el('span', { class: 'batch-spacer' }),
      el('button', { class: 'btn ghost small', type: 'button', dataset: { action: 'cancel' }, hidden: true }, '取消'),
      el('button', { class: 'btn primary', type: 'button', dataset: { action: 'start' }, disabled: true },
        icon('play', 14), '开始批量比较'),
    );
    const progress = el('div', { class: 'progress', hidden: true }, el('div', { class: 'progress-bar' }));
    const resultHost = el('section', { class: 'batch-result', hidden: true });

    this.baselineList = baselineList;
    this.allList = allList;
    this.allBox = allBox;
    this.baselineBox = baselineBox;
    this.countLabel = allHead.querySelector('#batch-count');
    this.statusLabel = actions.querySelector('#batch-status');
    this.startButton = actions.querySelector('[data-action=start]');
    this.cancelButton = actions.querySelector('[data-action=cancel]');
    this.progress = progress;
    this.resultHost = resultHost;

    root.append(
      el('p', { class: 'page-hint' }, '默认第一个文件为基准；可把文件拖到“基准”框或点击选择，其余文件依次与基准比较。任意候选不同即标红（或关系）。'),
      baselineBox,
      allBox,
      actions,
      progress,
      resultHost,
    );

    const pickBaseline = this._input(false, (files) => this.addFiles(files, { setBaseline: true }));
    const pickAll = this._input(true, (files) => this.addFiles(files));
    baselineHead.querySelector('[data-action=pick-baseline]').addEventListener('click', () => pickBaseline.click());
    allHead.querySelector('[data-action=pick-all]').addEventListener('click', () => pickAll.click());
    allHead.querySelector('[data-action=remove]').addEventListener('click', () => this.removeSelected());
    allHead.querySelector('[data-action=clear]').addEventListener('click', () => this.clearAll());
    allHead.querySelector('[data-action=set-baseline]').addEventListener('click', () => this.setBaselineFromSelection());
    this.startButton.addEventListener('click', () => void this.start());
    this.cancelButton.addEventListener('click', () => {
      this.cancelled = true;
      this.statusLabel.textContent = '正在取消…';
    });
    baselineBox.append(pickBaseline);
    allBox.append(pickAll);

    for (const box of [baselineBox, allBox]) this._bindDrop(box);
    allBox.addEventListener('click', (event) => {
      const item = event.target.closest('.import-item');
      if (!item) return;
      const index = Number(item.dataset.index);
      if (event.detail === 2) {
        this.setBaseline(index);
        return;
      }
      if (event.target.closest('.import-item-remove')) {
        this.removeIndex(index);
        return;
      }
      this._selectIndex(index, event.shiftKey || event.ctrlKey || event.metaKey);
    });
    this._refresh();
  }

  _input(multiple, handler) {
    const input = el('input', {
      type: 'file',
      class: 'file-input',
      multiple: multiple ? true : null,
      accept: SUPPORTED_EXTS.join(','),
      'aria-label': multiple ? '选择批量比较文件' : '选择基准文件',
    });
    input.addEventListener('change', () => {
      const files = [...(input.files || [])];
      input.value = '';
      if (files.length) handler(files);
    });
    return input;
  }

  _bindDrop(box) {
    box.addEventListener('dragover', (event) => {
      if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      box.classList.add('drag');
    });
    box.addEventListener('dragleave', () => box.classList.remove('drag'));
    box.addEventListener('drop', (event) => {
      event.preventDefault();
      box.classList.remove('drag');
      const files = [...((event.dataTransfer && event.dataTransfer.files) || [])];
      if (!files.length) return;
      this.addFiles(files, { setBaseline: box.classList.contains('baseline-box') });
    });
  }

  /* ---------------------------------------------------------------- 文件 */

  addFiles(files, { setBaseline = false } = {}) {
    const group = this.files.length ? fileGroup(this.files[0].file.name) : null;
    const added = [];
    for (const file of files) {
      const candidateGroup = fileGroup(file.name);
      if (!candidateGroup) {
        this.ctx.onError(`不支持的文件类型：${file.name}。网页版支持 ${SUPPORTED_EXTS.join(' / ')}（图片 OCR 请使用桌面版）。`);
        continue;
      }
      if (group && candidateGroup !== group) {
        this.ctx.onError('批量比较要求所有文件类型一致（TXT / CSV / XLSX / XLS 不能混合）。');
        return;
      }
      if (this.files.some((item) => sameFile(item.file, file))) continue;
      this.files.push({ file, status: '' });
      added.push(file);
    }
    if (setBaseline) {
      const target = files[files.length - 1];
      const index = this.files.findIndex((item) => sameFile(item.file, target));
      if (index >= 0) this.baseline = index;
    } else if (this.baseline === null && this.files.length) {
      this.baseline = 0;
    }
    this._refresh();
    if (added.length) this.statusLabel.textContent = `已添加 ${added.length} 个文件。`;
  }

  removeIndex(index) {
    if (this.running) return;
    this.files.splice(index, 1);
    if (this.baseline === index) this.baseline = this.files.length ? 0 : null;
    else if (this.baseline !== null && this.baseline > index) this.baseline -= 1;
    this._refresh();
  }

  removeSelected() {
    if (this.running) return;
    const selected = this.files
      .map((item, index) => (item.selected ? index : -1))
      .filter((index) => index >= 0);
    if (!selected.length) {
      this.statusLabel.textContent = '请先在“全部”列表中选择文件。';
      return;
    }
    for (const index of selected.sort((a, b) => b - a)) this.removeIndex(index);
  }

  clearAll() {
    if (this.running) return;
    this.files = [];
    this.baseline = null;
    this.result = null;
    this.resultHost.hidden = true;
    this._refresh();
  }

  setBaseline(index) {
    if (this.running) return;
    if (index < 0 || index >= this.files.length) return;
    this.baseline = index;
    this._refresh();
    this.statusLabel.textContent = '基准已切换；点击“开始批量比较”生成新的差异矩阵。';
  }

  setBaselineFromSelection() {
    const index = this.files.findIndex((item) => item.selected);
    if (index < 0) {
      this.statusLabel.textContent = '请先在“全部”列表中选择一个文件。';
      return;
    }
    this.setBaseline(index);
  }

  _selectIndex(index, additive) {
    if (!additive) {
      for (const item of this.files) item.selected = false;
    }
    if (this.files[index]) this.files[index].selected = true;
    this._refreshLists();
  }

  _refresh() {
    this.countLabel.textContent = `共 ${this.files.length} 个文件`;
    this.startButton.disabled = this.running || this.files.length < 2 || this.baseline === null;
    this.cancelButton.hidden = !this.running;
    this.baselineBox.classList.toggle('has-drag-target', true);
    this._refreshLists();
  }

  _refreshLists() {
    clear(this.baselineList);
    clear(this.allList);
    const baselineItem = this.baseline !== null ? this.files[this.baseline] : null;
    if (baselineItem) {
      this.baselineList.append(
        el('div', { class: 'import-item baseline-item', role: 'listitem' },
          el('span', { class: 'import-icon' }, icon('file', 15)),
          el('span', { class: 'import-name', title: baselineItem.file.name }, baselineItem.file.name),
          el('span', { class: 'import-meta' }, `${typeLabel(baselineItem.file)} · ${formatBytes(baselineItem.file.size)}`),
        ),
      );
    } else {
      this.baselineList.append(el('div', { class: 'import-empty' }, '拖入或选择基准文件'));
    }
    for (const [index, item] of this.files.entries()) {
      const isBaseline = index === this.baseline;
      this.allList.append(
        el('div', {
          class: `import-item${item.selected ? ' selected' : ''}${isBaseline ? ' is-baseline' : ''}${item.error ? ' has-error' : ''}`,
          role: 'listitem',
          dataset: { index: String(index) },
          title: item.file.name,
        },
          el('span', { class: 'import-icon' }, icon(isBaseline ? 'check' : 'file', 15)),
          el('span', { class: 'import-name' }, item.file.name),
          el('span', { class: 'import-meta' }, item.status || `${typeLabel(item.file)} · ${formatBytes(item.file.size)}`),
          el('button', {
            class: 'import-item-remove',
            type: 'button',
            'aria-label': `移除 ${item.file.name}`,
            onclick: (event) => {
              event.stopPropagation();
              this.removeIndex(index);
            },
          }, '×'),
        ),
      );
    }
    if (!this.files.length) {
      this.allList.append(el('div', { class: 'import-empty' }, '把同类型文件拖到此处，或点击右侧“选择”'));
    }
  }

  /* ---------------------------------------------------------------- 执行 */

  async start() {
    if (this.running) return;
    if (this.files.length < 2 || this.baseline === null) {
      this.ctx.toast('请至少添加 2 个文件（第一个默认为基准）', 'warn');
      return;
    }
    const baselineItem = this.files[this.baseline];
    const candidates = this.files.filter((_, index) => index !== this.baseline);
    this.running = true;
    this.cancelled = false;
    this.resultHost.hidden = true;
    this.progress.hidden = false;
    this.progress.querySelector('.progress-bar').style.width = '0%';
    for (const item of this.files) {
      item.status = '';
      item.error = '';
    }
    this._refresh();
    this.statusLabel.textContent = '正在比较…';
    if (this.ctx.setStatus) this.ctx.setStatus({ typeLabel: '批量比较', state: '正在批量比较…', count: 0 });
    try {
      const result = await runBatch(
        baselineItem.file,
        candidates.map((item) => item.file),
        { text: this.ctx.getSettings().text, sheet: this.ctx.getSettings().sheet },
        {
          progress: (percent, message) => {
            this.progress.querySelector('.progress-bar').style.width = `${Math.max(2, percent)}%`;
            this.statusLabel.textContent = message;
          },
          cancel: () => this.cancelled,
          onCandidate: (index, candidate) => {
            const item = candidates[index];
            if (!item) return;
            item.error = candidate.error;
            item.status = candidate.error ? '✗ 失败' : `✓ ${candidate.diffCount} 处差异`;
            this._refreshLists();
          },
        },
      );
      this.result = result;
      this.collapsedGroups.clear();
      this.showEqual = true;
      this.running = false;
      this.progress.hidden = true;
      this._refresh();
      this._renderResult();
      this.statusLabel.textContent = `完成：${result.fileCount} 个文件，${result.differentCount} 处差异，用时 ${result.elapsed.toFixed(2)} s`;
      if (this.ctx.setStatus) {
        this.ctx.setStatus({
          typeLabel: '批量比较',
          state: `基准 ${basename(result.baseline)} · ${result.fileCount} 个文件`,
          count: result.differentCount,
          elapsed: result.elapsed,
        });
      }
    } catch (err) {
      this.running = false;
      this.progress.hidden = true;
      this._refresh();
      const message = err && err.message ? err.message : String(err);
      this.statusLabel.textContent = message.includes('取消') ? '已取消' : '批量比较失败';
      if (!message.includes('取消')) this.ctx.onError(message);
    }
  }

  /* ---------------------------------------------------------------- 结果 */

  _renderResult() {
    const result = this.result;
    const host = this.resultHost;
    host.hidden = false;
    clear(host);
    if (!result) return;
    const header = el('div', { class: 'batch-result-head' },
      el('span', { class: 'batch-summary' }, `${result.fileCount} 个文件 · ${result.differentCount} 处位置存在差异（任意文件不同即标红）`),
      el('span', { class: 'batch-spacer' }),
      el('label', { class: 'check' },
        el('input', {
          type: 'checkbox',
          checked: this.showEqual,
          onchange: (event) => {
            this.showEqual = event.target.checked;
            this._renderTable();
          },
        }),
        '显示一致项',
      ),
      el('span', { class: 'legend' }, '红 = 确定差异 · 橙 = 需人工复核'),
      el('button', {
        class: 'btn ghost small',
        type: 'button',
        onclick: async () => {
          const ok = await copyText(this._tsv());
          this.ctx.toast(ok ? '差异表已复制（TSV）' : '复制失败', ok ? 'info' : 'error');
        },
      }, icon('copy', 14), '复制差异表'),
      el('button', {
        class: 'btn ghost small',
        type: 'button',
        onclick: () => downloadText('EngineerDiff-batch.tsv', this._tsv()),
      }, '下载 TSV'),
    );
    this.tableHost = el('div', { class: 'batch-table-host' });
    host.append(header, this.tableHost);
    if (result.warnings && result.warnings.length) {
      host.append(el('div', { class: 'batch-warnings' }, result.warnings.slice(0, 3).join('；')));
    }
    this._renderTable();
  }

  _renderTable() {
    const result = this.result;
    const host = this.tableHost;
    if (!host) return;
    clear(host);
    const candidates = result.candidates;
    const table = el('table', { class: 'diff-table fixed' });
    const colgroup = el(
      'colgroup',
      {},
      el('col', { class: 'col-location-col' }),
      ...new Array(1 + candidates.length).fill(0).map(() => el('col', {})),
    );
    const thead = el('thead');
    const headRow = el('tr', {},
      el('th', { class: 'col-location' }, '位置'),
      el('th', { class: 'col-value' }, `基准：${basename(result.baseline)}`),
      ...candidates.map((candidate) =>
        el('th', { class: 'col-value', title: candidate.name },
          candidate.name,
          el('span', { class: 'th-suffix' }, candidate.error ? '（失败）' : `（${candidate.diffCount} 处）`),
        ),
      ),
    );
    thead.append(headRow);
    const tbody = el('tbody');
    const grouped = result.mode === 'excel';
    let lastSheet = null;
    result.rows.forEach((row, rowIndex) => {
      if (grouped) {
        const sheet = row.sheet || '（无 Sheet）';
        if (sheet !== lastSheet) {
          lastSheet = sheet;
          const count = result.rows.filter((item) => (item.sheet || '（无 Sheet）') === sheet).length;
          const collapsed = this.collapsedGroups.has(sheet);
          const groupRow = el('tr', { class: 'group-row', dataset: { sheet } },
            el('td', { colspan: String(2 + candidates.length) },
              el('button', {
                class: 'group-toggle',
                type: 'button',
                onclick: () => {
                  if (this.collapsedGroups.has(sheet)) this.collapsedGroups.delete(sheet);
                  else this.collapsedGroups.add(sheet);
                  this._renderTable();
                },
                'aria-expanded': collapsed ? 'false' : 'true',
              }, `${collapsed ? '▸' : '▾'} 工作表「${sheet}」 · ${count} 处差异`),
            ),
          );
          tbody.append(groupRow);
        }
      }
      if (grouped && this.collapsedGroups.has(row.sheet || '（无 Sheet）')) {
        return;
      }
      const tone = row.severity === 'uncertain' ? 'uncertain' : 'diff';
      const tr = el('tr', { class: `diff-row tone-${tone}`, dataset: { index: String(rowIndex) } },
        el('td', { class: 'col-location', title: row.location }, row.location),
        el('td', { class: 'col-value' }, row.baseline),
      );
      candidates.forEach((_, index) => {
        const differs = row.differs[index];
        const value = row.values[index];
        const cell = el('td', {
          class: `col-value${differs ? ' differs' : ''}`,
          title: differs ? `${value}${row.note ? `\n${row.note}` : ''}` : value,
        });
        if (differs) cell.textContent = value;
        else cell.textContent = this.showEqual ? value : '';
        tr.append(cell);
      });
      tbody.append(tr);
    });
    table.append(colgroup, thead, tbody);
    host.append(table);
    this._updateStickyOffset();
    host.addEventListener('scroll', () => this._updateStickyOffset(), { passive: true });
  }

  _updateStickyOffset() {
    const host = this.tableHost;
    if (!host) return;
    host.style.setProperty('--sticky-top', '0px');
  }

  _tsv() {
    const result = this.result;
    if (!result) return '';
    const candidates = result.candidates;
    const lines = [[
      '位置',
      `基准：${basename(result.baseline)}`,
      ...candidates.map((candidate) => candidate.name),
    ].join('\t')];
    for (const row of result.rows) {
      lines.push([
        row.location,
        row.baseline,
        ...candidates.map((_, index) => (row.differs[index] ? row.values[index] : row.baseline)),
      ].join('\t'));
    }
    return lines.join('\n');
  }
}

function sameFile(a, b) {
  return a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
}

function typeLabel(file) {
  const group = fileGroup(file.name);
  if (group === 'text') return 'TXT 文本';
  if (group === 'csv') return 'CSV';
  return file.name.toLowerCase().endsWith('.xls') ? 'XLS 工作簿' : 'XLSX 工作簿';
}

function basename(path) {
  return String(path || '').split(/[\\/]/).pop();
}
