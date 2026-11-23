/*
 * 双栏文本视图（窗口化渲染）：行号 + 字符级标红 + 差异导航定位。
 *
 * 只渲染视口内行，10 万行文本也不会创建 10 万个 DOM 节点；
 * 行号列使用 sticky 定位，横向滚动时保持可见。
 */

import { el } from './dom.js';

const LINE_HEIGHT = 20;
const OVERSCAN = 8;

export class TextView {
  constructor(container) {
    this.container = container;
    this.container.classList.add('tv');
    this.scroll = el('div', { class: 'tv-scroll', tabindex: '0' });
    this.canvas = el('div', { class: 'tv-canvas' });
    this.rows = el('div', { class: 'tv-rows' });
    this.canvas.append(this.rows);
    this.scroll.append(this.canvas);
    this.empty = el('div', { class: 'tv-empty' }, '请选择文件');
    this.container.append(this.scroll, this.empty);
    this.side = null;
    this.onlyDiffs = false;
    this.visible = [];
    this._renderedFrom = -1;
    this._renderedTo = -1;
    this._currentId = null;
    this._maxWidth = 600;
    this._syncHandler = null;
    this._raf = 0;
    this._measure = document.createElement('canvas').getContext('2d');
    this.scroll.addEventListener('scroll', () => {
      if (this._syncHandler) this._syncHandler(this.scrollRatio());
      this._scheduleRender();
    });
  }

  setSyncHandler(handler) {
    this._syncHandler = handler;
  }

  setSide(side, emptyText = '请选择文件') {
    this.side = side;
    this.empty.textContent = emptyText;
    this._currentId = null;
    this.scroll.scrollTop = 0;
    this.scroll.scrollLeft = 0;
    this._rebuild();
  }

  setEmptyText(text) {
    this.empty.textContent = text;
  }

  setOnlyDiffs(enabled) {
    if (enabled === this.onlyDiffs) return;
    this.onlyDiffs = enabled;
    this._rebuild();
  }

  setCurrentDiff(diffId) {
    this._currentId = diffId;
    this._paintCurrent();
  }

  scrollRatio() {
    const max = this.scroll.scrollHeight - this.scroll.clientHeight;
    return max > 0 ? this.scroll.scrollTop / max : 0;
  }

  horizontalRatio() {
    const max = this.scroll.scrollWidth - this.scroll.clientWidth;
    return max > 0 ? this.scroll.scrollLeft / max : 0;
  }

  setScrollRatio(ratio) {
    const max = this.scroll.scrollHeight - this.scroll.clientHeight;
    if (max > 0) this.scroll.scrollTop = Math.max(0, Math.min(1, ratio)) * max;
  }

  setHorizontalRatio(ratio) {
    const max = this.scroll.scrollWidth - this.scroll.clientWidth;
    if (max > 0) this.scroll.scrollLeft = Math.max(0, Math.min(1, ratio)) * max;
  }

  scrollByLines(lines) {
    this.scroll.scrollTop += lines * LINE_HEIGHT;
  }

  scrollToDiffRow(row) {
    const target = Math.max(0, row * LINE_HEIGHT - LINE_HEIGHT * 3);
    this.scroll.scrollTop = target;
  }

  _rebuild() {
    this._renderedFrom = -1;
    this._renderedTo = -1;
    const side = this.side;
    if (!side) {
      this.visible = [];
      this.scroll.style.display = 'none';
      this.empty.style.display = 'flex';
      return;
    }
    this.scroll.style.display = '';
    this.empty.style.display = 'none';
    if (this.onlyDiffs) {
      const rows = new Set();
      side.lines.forEach((line, index) => {
        if (line.diffId || line.status !== 'equal') {
          rows.add(index - 1);
          rows.add(index);
          rows.add(index + 1);
        }
      });
      this.visible = [...rows].filter((index) => index >= 0 && index < side.lines.length).sort((a, b) => a - b);
    } else {
      this.visible = side.lines.map((_, index) => index);
    }
    this.canvas.style.height = `${Math.max(1, this.visible.length * LINE_HEIGHT)}px`;
    this.rows.style.height = this.canvas.style.height;
    this._maxWidth = this._measureWidth();
    this.canvas.style.width = `${Math.max(this._maxWidth, 320)}px`;
    this._render();
  }

  _measureWidth() {
    if (!this.side) return 320;
    this._measure.font = getComputedStyle(this.container).getPropertyValue('--tv-font') ||
      '13px ui-monospace, SFMono-Regular, Consolas, monospace';
    let width = 320;
    for (const line of this.side.lines) {
      if (!line.text) continue;
      const measured = this._measure.measureText(line.text).width;
      if (measured + 90 > width) width = measured + 90;
    }
    return Math.ceil(width);
  }

  _scheduleRender() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      this._render();
    });
  }

  _render() {
    if (!this.side) return;
    const scrollTop = this.scroll.scrollTop;
    const height = this.scroll.clientHeight || 400;
    const first = Math.max(0, Math.floor(scrollTop / LINE_HEIGHT) - OVERSCAN);
    const last = Math.min(
      this.visible.length - 1,
      Math.ceil((scrollTop + height) / LINE_HEIGHT) + OVERSCAN,
    );
    if (first === this._renderedFrom && last === this._renderedTo) return;
    this._renderedFrom = first;
    this._renderedTo = last;
    const fragment = document.createDocumentFragment();
    for (let pos = first; pos <= last; pos += 1) {
      const lineIndex = this.visible[pos];
      const line = this.side.lines[lineIndex];
      if (!line) continue;
      fragment.append(this._renderRow(line, pos));
    }
    this.rows.replaceChildren(fragment);
    this._paintCurrent();
  }

  _renderRow(line, position) {
    const classes = ['tv-row'];
    if (line.status === 'modified') classes.push('row-mod');
    else if (line.status === 'added') classes.push('row-add');
    else if (line.status === 'removed') classes.push('row-del');
    else if (line.status === 'placeholder') classes.push('row-placeholder');
    if (line.diffId) classes.push('has-diff');
    const row = el('div', {
      class: classes.join(' '),
      style: `top:${position * LINE_HEIGHT}px`,
      dataset: { diffId: line.diffId || '', row: String(line.lineNo || '') },
    });
    row.append(el('span', { class: 'tv-no' }, line.lineNo ? String(line.lineNo) : ''));
    const text = el('span', { class: 'tv-text' });
    if (line.status === 'placeholder') {
      text.append(el('span', { class: 'ph' }, '·'));
    } else if (line.spans && line.spans.length) {
      let cursor = 0;
      for (const [start, end] of line.spans) {
        if (start > cursor) text.append(document.createTextNode(line.text.slice(cursor, start)));
        text.append(el('mark', { class: 'chg' }, line.text.slice(start, end)));
        cursor = end;
      }
      if (cursor < line.text.length) text.append(document.createTextNode(line.text.slice(cursor)));
    } else {
      text.append(document.createTextNode(line.text));
    }
    row.append(text);
    return row;
  }

  _paintCurrent() {
    for (const node of this.rows.querySelectorAll('.tv-row.current')) node.classList.remove('current');
    if (!this._currentId) return;
    const node = this.rows.querySelector(`.tv-row[data-diff-id="${cssEscape(this._currentId)}"]`);
    if (node) node.classList.add('current');
  }
}

function cssEscape(value) {
  if (globalThis.CSS && CSS.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, '\\$&');
}

export { LINE_HEIGHT };
