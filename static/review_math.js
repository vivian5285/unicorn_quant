/* 2026-10-23: display-only helpers. Never publish an executable target. */
(function (root) {
  const day = 86400000;
  function marker(ms, rows, tf, stock) {
    if (stock) {
      const r = rows.find(r => Math.floor(r[0] / day) === Math.floor(ms / day));
      return r ? Math.floor(r[0] / 1000) : null;
    }
    const span = tf === '8h' ? day / 3 : day;
    const t = Math.floor(ms / span) * span;
    return rows.some(r => r[0] === t) ? t / 1000 : null;
  }
  function virtualAt(trades, ms) {
    const byLeg = new Map();
    for (const t of trades) {
      if (t.entry_t > ms || (!t.open && t.exit_t <= ms)) continue;
      const leg = t.sub || t.leg;
      if (!byLeg.has(leg) || byLeg.get(leg).entry_t < t.entry_t) byLeg.set(leg, t);
    }
    return [...byLeg.values()].reduce((v, t) => v + (t.side === 'LONG' ? 1 : -1) * (t.w ?? 1) * (t.alloc ?? 1), 0);
  }
  const api = { marker, virtualAt };
  if (typeof module !== 'undefined') module.exports = api;
  else root.ReviewMath = api;
})(typeof window === 'undefined' ? globalThis : window);
