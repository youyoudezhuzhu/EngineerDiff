/*
 * SequenceMatcher —— Python difflib.SequenceMatcher 的 JS 移植。
 *
 * 只移植 EngineerDiff 需要的部分：
 *   - setSeqs / findLongestMatch / getMatchingBlocks / getOpcodes
 *   - quickRatio（与 Python 3.11 实现一致的多重集合交集上界）
 *   - autojunk（len(b) >= 200 时，出现次数 > n//100+1 的元素视为 junk）
 *
 * 保持与桌面版相同的 opcode 序列，避免网页版与桌面版行对齐结果不同。
 */

export class SequenceMatcher {
  constructor(isJunk = null, a = [], b = [], autoJunk = true) {
    this.isJunk = isJunk;
    this.autoJunk = autoJunk;
    this.a = null;
    this.b = null;
    this.matchingBlocks = null;
    this.opcodes = null;
    this.b2j = null;
    this.bJunk = new Set();
    this.bPopular = new Set();
    this.fullbCount = null;
    this.setSeqs(a, b);
  }

  setSeqs(a, b) {
    this.setSeq1(a);
    this.setSeq2(b);
  }

  setSeq1(a) {
    if (this.a === a) return;
    this.a = a;
    this.matchingBlocks = null;
    this.opcodes = null;
  }

  setSeq2(b) {
    if (this.b === b) return;
    this.b = b;
    this.matchingBlocks = null;
    this.opcodes = null;
    this.fullbCount = null;
    this._chainB();
  }

  _chainB() {
    const b = this.b;
    const b2j = new Map();
    for (let i = 0; i < b.length; i += 1) {
      const elt = b[i];
      const list = b2j.get(elt);
      if (list) list.push(i);
      else b2j.set(elt, [i]);
    }
    this.b2j = b2j;

    this.bJunk = new Set();
    if (this.isJunk) {
      for (const elt of [...b2j.keys()]) {
        if (this.isJunk(elt)) {
          this.bJunk.add(elt);
          b2j.delete(elt);
        }
      }
    }

    this.bPopular = new Set();
    const n = b.length;
    if (this.autoJunk && n >= 200) {
      const ntest = Math.floor(n / 100) + 1;
      for (const [elt, idxs] of [...b2j.entries()]) {
        if (idxs.length > ntest) {
          this.bPopular.add(elt);
          b2j.delete(elt);
        }
      }
    }
  }

  findLongestMatch(alo = 0, ahi = null, blo = 0, bhi = null) {
    const a = this.a;
    const b = this.b;
    const b2j = this.b2j;
    const isBJunk = (elt) => this.bJunk.has(elt);
    if (ahi === null) ahi = a.length;
    if (bhi === null) bhi = b.length;
    let besti = alo;
    let bestj = blo;
    let bestsize = 0;
    let j2len = new Map();
    for (let i = alo; i < ahi; i += 1) {
      const newj2len = new Map();
      const indices = b2j.get(a[i]);
      if (indices) {
        for (const j of indices) {
          if (j < blo) continue;
          if (j >= bhi) break;
          const k = (j2len.get(j - 1) || 0) + 1;
          newj2len.set(j, k);
          if (k > bestsize) {
            besti = i - k + 1;
            bestj = j - k + 1;
            bestsize = k;
          }
        }
      }
      j2len = newj2len;
    }
    while (
      besti > alo &&
      bestj > blo &&
      !isBJunk(b[bestj - 1]) &&
      a[besti - 1] === b[bestj - 1]
    ) {
      besti -= 1;
      bestj -= 1;
      bestsize += 1;
    }
    while (
      besti + bestsize < ahi &&
      bestj + bestsize < bhi &&
      !isBJunk(b[bestj + bestsize]) &&
      a[besti + bestsize] === b[bestj + bestsize]
    ) {
      bestsize += 1;
    }
    while (
      besti > alo &&
      bestj > blo &&
      isBJunk(b[bestj - 1]) &&
      a[besti - 1] === b[bestj - 1]
    ) {
      besti -= 1;
      bestj -= 1;
      bestsize += 1;
    }
    while (
      besti + bestsize < ahi &&
      bestj + bestsize < bhi &&
      isBJunk(b[bestj + bestsize]) &&
      a[besti + bestsize] === b[bestj + bestsize]
    ) {
      bestsize += 1;
    }
    return [besti, bestj, bestsize];
  }

  getMatchingBlocks() {
    if (this.matchingBlocks) return this.matchingBlocks;
    const la = this.a.length;
    const lb = this.b.length;
    const queue = [[0, la, 0, lb]];
    const matching = [];
    while (queue.length) {
      const [alo, ahi, blo, bhi] = queue.pop();
      const [i, j, k] = this.findLongestMatch(alo, ahi, blo, bhi);
      if (k) {
        matching.push([i, j, k]);
        if (alo < i && blo < j) queue.push([alo, i, blo, j]);
        if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
      }
    }
    matching.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[2] - y[2]);
    let i1 = 0;
    let j1 = 0;
    let k1 = 0;
    const nonAdjacent = [];
    for (const [i2, j2, k2] of matching) {
      if (i1 + k1 === i2 && j1 + k1 === j2) {
        k1 += k2;
      } else {
        if (k1) nonAdjacent.push([i1, j1, k1]);
        i1 = i2;
        j1 = j2;
        k1 = k2;
      }
    }
    if (k1) nonAdjacent.push([i1, j1, k1]);
    nonAdjacent.push([la, lb, 0]);
    this.matchingBlocks = nonAdjacent;
    return nonAdjacent;
  }

  getOpcodes() {
    if (this.opcodes) return this.opcodes;
    let i = 0;
    let j = 0;
    const opcodes = [];
    for (const [ai, bj, size] of this.getMatchingBlocks()) {
      let tag = '';
      if (i < ai && j < bj) tag = 'replace';
      else if (i < ai) tag = 'delete';
      else if (j < bj) tag = 'insert';
      if (tag) opcodes.push([tag, i, ai, j, bj]);
      i = ai + size;
      j = bj + size;
      if (size) opcodes.push(['equal', ai, i, bj, j]);
    }
    this.opcodes = opcodes;
    return opcodes;
  }

  quickRatio() {
    if (this.matchingBlocks) return this.ratio();
    const la = this.a.length;
    const lb = this.b.length;
    if (!la || !lb) return la === lb ? 1.0 : 0.0;
    if (!this.fullbCount) {
      this.fullbCount = new Map();
      for (const elt of this.b) {
        this.fullbCount.set(elt, (this.fullbCount.get(elt) || 0) + 1);
      }
    }
    const avail = new Map();
    let matches = 0;
    for (const elt of this.a) {
      const numb = avail.has(elt) ? avail.get(elt) : this.fullbCount.get(elt) || 0;
      avail.set(elt, numb - 1);
      if (numb > 0) matches += 1;
    }
    return (2.0 * matches) / (la + lb);
  }

  ratio() {
    const blocks = this.getMatchingBlocks();
    let matches = 0;
    for (const [, , size] of blocks) matches += size;
    return (2.0 * matches) / (this.a.length + this.b.length);
  }
}

export default SequenceMatcher;
