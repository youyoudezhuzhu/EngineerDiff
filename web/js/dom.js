/* 通用 DOM / 格式化 / 图标助手。 */

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

export function formatSeconds(value) {
  if (!Number.isFinite(value)) return '';
  return value < 10 ? `${value.toFixed(2)} s` : `${value.toFixed(1)} s`;
}

export function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function debounce(fn, wait = 120) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function toast(message, kind = 'info', timeout = 2600) {
  const host = document.getElementById('toast-host');
  if (!host) return;
  const node = el('div', { class: `toast toast-${kind}` }, message);
  host.append(node);
  requestAnimationFrame(() => node.classList.add('show'));
  setTimeout(() => {
    node.classList.remove('show');
    setTimeout(() => node.remove(), 220);
  }, timeout);
}

export function downloadText(fileName, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = el('a', { href: url, download: fileName });
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = el('textarea', { class: 'clipboard-fallback' });
    area.value = text;
    document.body.append(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}

/* 线性图标（Fluent 风格，24x24，currentColor） */
export const ICONS = {
  folder:
    '<path d="M3.5 6.5A2.5 2.5 0 0 1 6 4h3.2c.5 0 1 .2 1.4.6l1.1 1.1c.2.2.5.3.8.3H18a2.5 2.5 0 0 1 2.5 2.5v7A2.5 2.5 0 0 1 18 18.5H6A2.5 2.5 0 0 1 3.5 16V6.5Zm1.5 0V16c0 .6.4 1 1 1h12c.6 0 1-.4 1-1V8.5c0-.6-.4-1-1-1h-5.5c-.5 0-1-.2-1.4-.6L9.9 5.8a.5.5 0 0 0-.4-.2H6c-.6 0-1 .4-1 1Z"/>',
  swap: '<path d="M7.3 3.3 3.3 7.3a1 1 0 0 0 0 1.4l4 4 1.4-1.4L6.4 9H20V7H6.4l2.3-2.3-1.4-1.4Zm9.4 8-1.4 1.4 2.3 2.3H4v2h13.6l-2.3 2.3 1.4 1.4 4-4a1 1 0 0 0 0-1.4l-4-4Z"/>',
  play: '<path d="M8 5.1v13.8c0 .8.9 1.3 1.6.9l10.8-6.9a1 1 0 0 0 0-1.7L9.6 4.2A1 1 0 0 0 8 5.1Z"/>',
  chevronLeft: '<path d="M14.7 5.3 8 12l6.7 6.7 1.4-1.4L10.8 12l5.3-5.3-1.4-1.4Z"/>',
  chevronRight: '<path d="m9.3 5.3 6.7 6.7-6.7 6.7-1.4-1.4 5.3-5.3-5.3-5.3 1.4-1.4Z"/>',
  chevronUp: '<path d="m5.3 14.7 6.7-6.7 6.7 6.7-1.4 1.4L12 10.8l-5.3 5.3-1.4-1.4Z"/>',
  chevronDown: '<path d="m5.3 9.3 1.4-1.4L12 13.2l5.3-5.3 1.4 1.4-6.7 6.7-6.7-6.7Z"/>',
  settings:
    '<path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm0 1.8a1.7 1.7 0 1 1 0 3.4 1.7 1.7 0 0 1 0-3.4Z"/><path d="M9.8 2.5a1.5 1.5 0 0 1 1.5-1.3h1.4a1.5 1.5 0 0 1 1.5 1.3l.2 1.3c.4.1.8.3 1.1.5l1.1-.7a1.5 1.5 0 0 1 1.9.2l1 1a1.5 1.5 0 0 1 .2 1.9l-.7 1.1c.2.3.4.7.5 1.1l1.3.2a1.5 1.5 0 0 1 1.3 1.5v1.4a1.5 1.5 0 0 1-1.3 1.5l-1.3.2c-.1.4-.3.8-.5 1.1l.7 1.1a1.5 1.5 0 0 1-.2 1.9l-1 1a1.5 1.5 0 0 1-1.9.2l-1.1-.7c-.3.2-.7.4-1.1.5l-.2 1.3a1.5 1.5 0 0 1-1.5 1.3h-1.4a1.5 1.5 0 0 1-1.5-1.3l-.2-1.3c-.4-.1-.8-.3-1.1-.5l-1.1.7a1.5 1.5 0 0 1-1.9-.2l-1-1a1.5 1.5 0 0 1-.2-1.9l.7-1.1c-.2-.3-.4-.7-.5-1.1l-1.3-.2a1.5 1.5 0 0 1-1.3-1.5v-1.4a1.5 1.5 0 0 1 1.3-1.5l1.3-.2c.1-.4.3-.8.5-1.1l-.7-1.1a1.5 1.5 0 0 1 .2-1.9l1-1a1.5 1.5 0 0 1 1.9-.2l1.1.7c.3-.2.7-.4 1.1-.5l.2-1.3ZM11.2 3h1.6l.3 1.7c.1.5.5.9 1 1 .5.1 1 .3 1.4.6.4.3 1 .3 1.4.1l1.5-1 1.1 1.1-1 1.5c-.3.4-.2 1 .1 1.4.3.4.5.9.6 1.4.1.5.5.9 1 1l1.7.3v1.6l-1.7.3c-.5.1-.9.5-1 1-.1.5-.3 1-.6 1.4-.3.4-.3 1-.1 1.4l1 1.5-1.1 1.1-1.5-1c-.4-.3-1-.2-1.4.1-.4.3-.9.5-1.4.6-.5.1-.9.5-1 1l-.3 1.7h-1.6l-.3-1.7c-.1-.5-.5-.9-1-1-.5-.1-1-.3-1.4-.6-.4-.3-1-.3-1.4-.1l-1.5 1-1.1-1.1 1-1.5c.3-.4.2-1-.1-1.4-.3-.4-.5-.9-.6-1.4-.1-.5-.5-.9-1-1L3 12.8v-1.6l1.7-.3c.5-.1.9-.5 1-1 .1-.5.3-1 .6-1.4.3-.4.3-1 .1-1.4l-1-1.5 1.1-1.1 1.5 1c.4.3 1 .2 1.4-.1.4-.3.9-.5 1.4-.6.5-.1.9-.5 1-1l.3-1.7Z"/>',
  copy: '<path d="M8 3.5A2.5 2.5 0 0 0 5.5 6v10A2.5 2.5 0 0 0 8 18.5h7a2.5 2.5 0 0 0 2.5-2.5V6A2.5 2.5 0 0 0 15 3.5H8Zm-1 2.5c0-.6.4-1 1-1h7c.6 0 1 .4 1 1v10c0 .6-.4 1-1 1H8c-.6 0-1-.4-1-1V6Z"/><path d="M3.5 7.5A1 1 0 0 0 2.5 8v9A3.5 3.5 0 0 0 6 20.5h8a1 1 0 1 0 0-2H6A1.5 1.5 0 0 1 4.5 17V8a1 1 0 0 0-1-.5Z"/>',
  close: '<path d="M5.3 5.3 12 12l6.7-6.7 1.4 1.4L13.4 13.4 20.1 20l-1.4 1.4L12 14.8l-6.7 6.6L3.9 20l6.7-6.7-6.7-6.7 1.4-1.3Z"/>',
  sun: '<path d="M12 2.5a1 1 0 0 1 1 1V5a1 1 0 1 1-2 0V3.5a1 1 0 0 1 1-1Zm0 15a1 1 0 0 1 1 1v1.5a1 1 0 1 1-2 0V18.5a1 1 0 0 1 1-1ZM2.5 12a1 1 0 0 1 1-1H5a1 1 0 1 1 0 2H3.5a1 1 0 0 1-1-1Zm15 0a1 1 0 0 1 1-1h1.5a1 1 0 1 1 0 2H18.5a1 1 0 0 1-1-1ZM12 6.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11Zm0 2a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Z"/><path d="M5.3 5.3a1 1 0 0 1 1.4 0l1 1a1 1 0 0 1-1.4 1.4l-1-1a1 1 0 0 1 0-1.4Zm11 11a1 1 0 0 1 1.4 0l1 1a1 1 0 0 1-1.4 1.4l-1-1a1 1 0 0 1 0-1.4Zm1.4-11a1 1 0 0 1 0 1.4l-1 1a1 1 0 1 1-1.4-1.4l1-1a1 1 0 0 1 1.4 0Zm-11 11a1 1 0 0 1 0 1.4l-1 1a1 1 0 1 1-1.4-1.4l1-1a1 1 0 0 1 1.4 0Z"/>',
  moon: '<path d="M10.8 3.2a1 1 0 0 1 .5 1.1 7 7 0 0 0 8.4 8.4 1 1 0 0 1 1.2 1.3A9 9 0 1 1 9.5 2.7a1 1 0 0 1 1.3.5Z"/>',
  monitor: '<path d="M3.5 5.5A2.5 2.5 0 0 1 6 3h12a2.5 2.5 0 0 1 2.5 2.5v8A2.5 2.5 0 0 1 18 16H6a2.5 2.5 0 0 1-2.5-2.5v-8ZM6 5c-.3 0-.5.2-.5.5v8c0 .3.2.5.5.5h12c.3 0 .5-.2.5-.5v-8c0-.3-.2-.5-.5-.5H6Z"/><path d="M8 18a1 1 0 0 1 1-1h6a1 1 0 1 1 0 2H9a1 1 0 0 1-1-1Z"/>',
  diff: '<path d="M12 3a1 1 0 0 1 1 1v7h7a1 1 0 1 1 0 2h-7v7a1 1 0 1 1-2 0v-7H4a1 1 0 1 1 0-2h7V4a1 1 0 0 1 1-1Z"/>',
  table: '<path d="M4 4.5h16a1.5 1.5 0 0 1 1.5 1.5v12A1.5 1.5 0 0 1 20 19.5H4A1.5 1.5 0 0 1 2.5 18V6A1.5 1.5 0 0 1 4 4.5Zm.5 4V10h5V8.5h-5Zm7 0V10h5V8.5h-5Zm7 0V10h3V8.5h-3Zm-14 3.5v3h5v-3h-5Zm7 0v3h5v-3h-5Zm7 0v3h3v-3h-3Z"/>',
  file: '<path d="M6 2.5h7.2L19.5 8.8V20a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 20V4A1.5 1.5 0 0 1 6 2.5Zm6.5 2H6.5v14h11V9.5h-3a1 1 0 0 1-1-1v-4Z"/>',
  check: '<path d="M20.3 6.7 9.5 17.5 3.7 11.7l1.4-1.4 4.4 4.4 9.4-9.4 1.4 1.4Z"/>',
  refresh: '<path d="M12 4.5a7.5 7.5 0 0 1 6.7 4.1l1.4-1.4v5h-5l1.9-1.9A5.5 5.5 0 0 0 6.9 12H4.5A7.5 7.5 0 0 1 12 4.5Zm6.6 7.5H21a7.5 7.5 0 0 1-13.3 4.6l-1.4 1.4v-5h5l-1.9 1.9A5.5 5.5 0 0 0 18.6 12Z"/>',
};

export function icon(name, size = 16) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'currentColor');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon');
  svg.innerHTML = ICONS[name] || '';
  return svg;
}
