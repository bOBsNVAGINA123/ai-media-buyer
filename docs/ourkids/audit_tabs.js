/* ===================== COMMERCIAL AUDIT — four tabs ==========================
   au  Commercial     — entirely window-driven off O.fin / O.shop / O.ad daily series,
                        so it obeys the universal date bar like every other tab.
   av  Vendor Capital — GMROI, goods-received recency and the next-dollar call, from
                        audit.json (fixed windows; the UI snaps and says so).
   ag  Listing Gap    — what the shops sell that the site cannot, from audit.json.
   aq  Demand Gap     — demand we pay for and lose, from O.searchIntel + the funnel.

   The rule: anything with a daily series is computed live from the chosen range.
   Anything that is a per-SKU join (listing, vendor stock, receipts) is a fixed-window
   snapshot from the collector, and every one of those cards says which window it used
   and when it was pulled. No number is silently the wrong period.                    */

const AU_W = [7, 30, 90, 365];
let AUW = 30;                       // chosen snapshot window for av / ag
function auSetW(w) { AUW = +w; RDR(); }
/* The audit block is served as its OWN file (docs/ourkids/audit.json), written by its own
   workflow. It is deliberately not inside data.js: data.js is rewritten wholesale by the main
   collector and by other sessions, and anything merged into it gets lost. Fetch once, cache,
   re-render when it lands. */
let AUD = null, AUD_STATE = 'idle';
function auHas() { return !!(AUD && AUD.vendors); }
function auLoad() {
  if (AUD_STATE !== 'idle') return;
  AUD_STATE = 'loading';
  fetch('audit.json?t=' + Date.now(), { cache: 'no-store' })
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(j => { AUD = j; AUD_STATE = 'ready';
      if (['au', 'av', 'ag', 'aq'].indexOf(TAB) >= 0) tab(TAB); })
    .catch(e => { AUD_STATE = 'failed:' + e;
      if (['av', 'ag'].indexOf(TAB) >= 0) tab(TAB); });
}
function auSnap(days) {             // snap the live range to the nearest snapshot window
  let b = AU_W[0];
  AU_W.forEach(w => { if (Math.abs(w - days) < Math.abs(b - days)) b = w; });
  return b;
}
const auM = v => 'E£ ' + Math.round(v || 0).toLocaleString();
const auK = v => { v = v || 0; const a = Math.abs(v);
  return a >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : a >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : Math.round(v).toLocaleString(); };
const auP = (v, d) => (v == null || !isFinite(v)) ? '—' : v.toFixed(d == null ? 1 : d) + '%';
const auX = v => (v == null || !isFinite(v)) ? '—' : v.toFixed(2) + '×';
function auKpi(l, v, n) { return '<div class="kpi"><div class="l">' + l + '</div><div class="v">' + v + '</div>' +
  (n ? '<div style="font-size:11px;color:#8a93a6;margin-top:2px">' + n + '</div>' : '') + '</div>'; }
function auCard(t, s, kpis, body, tag) {
  return '<div class="card full" data-a="' + (tag || 'ins') + '"><div class="chead"><div><div class="ct">' + t +
    '</div><div class="cs">' + s + '</div></div>' + (kpis ? '<div class="kpis">' + kpis + '</div>' : '') + '</div>' +
    (body || '') + '</div>';
}
/* one inline bar row — no chart lifecycle to leak */
function auBar(label, pct, val, note, col) {
  return '<div style="display:grid;grid-template-columns:190px 1fr 92px;gap:10px;align-items:center;padding:3px 0">' +
    '<div style="font-size:12px;font-weight:700;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + label +
    (note ? '<div style="font-size:10px;color:#8a93a6;font-weight:600">' + note + '</div>' : '') + '</div>' +
    '<div style="background:#eef0f5;border-radius:4px;height:14px;overflow:hidden"><div style="height:100%;width:' +
    Math.max(0, Math.min(100, pct)) + '%;background:' + (col || 'var(--indigo)') + ';border-radius:4px"></div></div>' +
    '<div style="font-size:12px;font-weight:800;font-variant-numeric:tabular-nums">' + val + '</div></div>';
}
function auWinChips() {
  return '<div style="display:flex;gap:5px;align-items:center;padding:2px 12px 10px">' +
    '<span style="font-size:10px;color:#8a93a6;font-weight:800;letter-spacing:.3px">SNAPSHOT WINDOW</span>' +
    AU_W.map(w => '<span class="chip" style="' + (w === AUW ? 'background:#2b3242;color:#fff;border-color:#2b3242' : '') +
      '" onclick="auSetW(' + w + ')">' + (w === 365 ? '365d' : w + 'd') + '</span>').join(' ') +
    '<span style="flex:1"></span><span style="font-size:10.5px;color:#8a93a6">pulled ' +
    ((AUD && AUD.pulled) || '—') + '</span></div>';
}
function auNoData(id, what) {
  document.getElementById(id).innerHTML = auCard('Not in this sync yet',
    (AUD_STATE === 'loading' ? 'Loading audit.json…'
      : AUD_STATE.indexOf('failed') === 0 ? what + ' comes from audit.json, which has not been published yet by the Commercial Audit workflow. Nothing is broken; the file simply is not there yet.'
      : what + ' is loading.'), '', '');
}

/* ---- session basis -------------------------------------------------------
   O.ad.sessions is Shopify, ALL COUNTRIES. Roughly a sixth of it is foreign traffic that
   almost never orders, so every session-based ratio -- GPPV, conversion, cost per visit --
   reads low against it. The routing pull already ships an Egypt-only cut of the same
   Shopify sessions (O.cvr.wins["30eg"] vs ["30"]), so the honest fix is to scale by the
   MEASURED share rather than guess. Default is Egypt-only, because a bot is not a visit;
   the chip flips it and the banner always names the basis and the factor. */
let AUEG = true;
function auEgSet(v) { AUEG = !!v; RDR(); }
function auEgFactor() {
  const w = ((O.cvr || {}).wins) || {};
  for (const k of ['30', '14', '7']) {
    const a = w[k], b = w[k + 'eg'];
    if (a && b && a.allSess > 0 && b.allSess > 0) {
      const f = b.allSess / a.allSess;
      if (f > 0.3 && f <= 1) return { f: f, win: k, eg: b.allSess, all: a.allSess };
    }
  }
  return null;
}
function auBasis() {
  const e = auEgFactor();
  return { on: AUEG && !!e, f: (AUEG && e) ? e.f : 1, m: e };
}
function auBasisChips() {
  const e = auEgFactor();
  if (!e) return '';
  return '<div style="display:flex;gap:5px;align-items:center;padding:2px 12px 10px">' +
    '<span style="font-size:10px;color:#8a93a6;font-weight:800;letter-spacing:.3px">SESSION BASIS</span>' +
    [[true, 'Egypt only'], [false, 'All countries']].map(x =>
      '<span class="chip" style="' + (AUEG === x[0] ? 'background:#2b3242;color:#fff;border-color:#2b3242' : '') +
      '" onclick="auEgSet(' + x[0] + ')">' + x[1] + '</span>').join(' ') +
    '<span style="flex:1"></span><span style="font-size:10.5px;color:#8a93a6">Egypt is ' +
    (e.f * 100).toFixed(1) + '% of Shopify sessions over the last ' + e.win + ' days</span></div>';
}

/* ---------------------------------------------------------- window arithmetic */
/* O.fin is ONLINE + marketplaces only -- branches are not in it. Branch revenue and
   gross profit live in O.pos, monthly and exact (it reconciles to Odoo to the pound).
   O.bl exists as a daily branch series but runs ~12% light against Odoo, so it is not
   used here. A month is pro-rated by elapsed days, and the CURRENT month is divided by
   the days actually banked, not by 31 -- otherwise every month-to-date range would
   understate the stores by a third. */
function auMonths(s, e) {           // [{m,frac}] months overlapping the range
  const out = [], today = (O.today || e);
  let y = +s.slice(0, 4), mo = +s.slice(5, 7);
  while (true) {
    const m = y + '-' + String(mo).padStart(2, '0');
    if (m > e.slice(0, 7)) break;
    const dim = new Date(Date.UTC(y, mo, 0)).getUTCDate();
    const banked = (m === today.slice(0, 7)) ? +today.slice(8, 10) : dim;
    const a = (m + '-01') > s ? (m + '-01') : s;
    const bEnd = m + '-' + String(dim).padStart(2, '0');
    const b = bEnd < e ? bEnd : e;
    if (a <= b) {
      const days = Math.round((Date.parse(b) - Date.parse(a)) / 864e5) + 1;
      out.push({ m: m, frac: banked ? Math.min(1, days / banked) : 0 });
    }
    mo++; if (mo > 12) { mo = 1; y++; }
  }
  return out;
}
function auBranch(s, e) {
  const P = O.pos || {}, ms = auMonths(s, e);
  let rev = 0, gp = 0, ord = 0;
  Object.keys(P).forEach(b => ms.forEach(x => {
    const r = P[b][x.m]; if (!r) return;
    rev += (r[0] || 0) * x.frac; gp += (r[1] || 0) * x.frac; ord += (r[2] || 0) * x.frac;
  }));
  return { rev: rev, gp: gp, ord: ord, months: ms.length };
}
function auWin() {
  const A = O.ad || {}, F = O.fin || {}, S = O.shop || {}, chd = F.chD || {};
  /* The ad/session series starts later than the revenue series (O.ad.start). Ratios that
     divide revenue by sessions or spend MUST use the same days on both sides, or a range
     that reaches back before O.ad.start reads as a miracle -- 21x MER, 3% conversion.
     So: totals use the full range, ratios use the ad-covered slice, and the tab says when
     the two differ. */
  const adS = A.start || ST.s, adE = A.n ? aD(A.n - 1) : ST.e;
  const rs = ST.s > adS ? ST.s : adS, re = ST.e < adE ? ST.e : adE;
  const r = rng(ST.s, ST.e), a = aRange(ST.s, ST.e);
  const rr = (rs <= re) ? rng(rs, re) : null;
  const o = { days: span(ST.s, ST.e), orev: 0, ogp: 0, oref: 0, oord: 0, mkt: 0,
              sess: 0, spend: 0, ms: 0, gs: 0, ts: 0, atc: 0, chk: 0,
              rOrev: 0, rOgp: 0, rOref: 0, rOord: 0 };
  if (r) for (let i = r[0]; i <= r[1]; i++) {
    o.orev += ((S.rev || [])[i] || 0) * SC.rev; o.ogp += ((S.gp || [])[i] || 0) * SC.gp;
    o.oref += ((S.ref || [])[i] || 0) * SC.refund; o.oord += ((S.ord || [])[i] || 0);
    ['Noon', 'Amazon', 'Homzmart'].forEach(k => { if (chd[k]) o.mkt += (chd[k][i] || 0) * SC.rev; });
  }
  if (rr) for (let i = rr[0]; i <= rr[1]; i++) {
    o.rOrev += ((S.rev || [])[i] || 0) * SC.rev; o.rOgp += ((S.gp || [])[i] || 0) * SC.gp;
    o.rOref += ((S.ref || [])[i] || 0) * SC.refund; o.rOord += ((S.ord || [])[i] || 0);
  }
  if (a) for (let i = a[0]; i <= a[1]; i++) {
    const s2 = (A.sessions || [])[i] || 0;
    o.sess += s2; o.spend += (A.spend || [])[i] || 0;
    o.ms += (A.mspend || [])[i] || 0; o.gs += (A.gspend || [])[i] || 0; o.ts += (A.tspend || [])[i] || 0;
    o.atc += s2 * (((A.atcRatio || [])[i] || 0) / 100);
    o.chk += s2 * (((A.checkoutRatio || [])[i] || 0) / 100);
  }
  const bas = auBasis();
  o.basis = bas.on ? 'Egypt only' : 'All countries';
  o.basisF = bas.f;
  o.sessAll = o.sess;
  o.sess = o.sess * bas.f;          // ratios below all divide by this
  const b = auBranch(ST.s, ST.e);
  o.branch = b.rev; o.branchGp = b.gp; o.branchOrd = b.ord;
  o.rev = o.orev + o.mkt + o.branch;
  o.gp = o.ogp + o.branchGp + o.mkt * 0.22;          // marketplace GP at the blended rate
  o.onShare = o.rev ? o.orev / o.rev * 100 : 0;
  o.gpPct = o.rev ? o.gp / o.rev * 100 : null;
  o.adDays = (rs <= re) ? span(rs, re) : 0;
  o.adCover = o.days ? o.adDays / o.days : 0;
  o.adFrom = rs; o.adTo = re;
  /* ratios -- ad-covered slice on both sides */
  o.gppv = o.sess ? o.rOgp / o.sess : null;
  o.gppvNet = o.sess ? (o.rOgp * (o.rOrev ? 1 - o.rOref / o.rOrev : 1)) / o.sess : null;
  o.mer = o.spend ? o.rOrev / o.spend : null;
  o.merNet = o.spend ? (o.rOrev - o.rOref) / o.spend : null;
  o.cvr = o.sess ? o.rOord / o.sess * 100 : null;
  o.aov = o.rOord ? o.rOrev / o.rOord : null;
  o.retOn = o.orev ? o.oref / o.orev * 100 : null;
  o.mediaOfGp = o.rOgp ? o.spend / o.rOgp * 100 : null;
  /* company MER also needs matched days: scale total revenue to the ad-covered slice */
  const bAd = (rs <= re) ? auBranch(rs, re) : { rev: 0 };
  o.coMer = o.spend ? (o.rOrev + bAd.rev) / o.spend : null;
  o.mediaOfAllGp = o.gp && o.adCover ? o.spend / (o.gp * o.adCover) * 100 : null;
  return o;
}

/* ============================================================ au — COMMERCIAL */
function rAU() {
  if (TAB !== 'au') return; dCh(); auLoad();
  const g = document.getElementById('g_au'); if (!g) return;
  const c = auWin();
  const p = (function () { const s = ST.s, e = ST.e; let w = null;
    try { w = cmpW(); } catch (x) { }
    if (!w) return null; const S = ST.s, E = ST.e; ST.s = w[0]; ST.e = w[1];
    const o = auWin(); ST.s = S; ST.e = E; return o; })();
  const dl = (a, b, inv) => (a == null || b == null || !b) ? '' :
    '<span style="font-size:10.5px;font-weight:800;color:' +
    (((a >= b) !== !!inv) ? '#12a06e' : '#e23a63') + '"> ' + (a >= b ? '▲' : '▼') + ' ' +
    Math.abs((a / b - 1) * 100).toFixed(0) + '%</span>';

  document.getElementById('bn_au').innerHTML = '&#8635; Synced <b>' + LSC + ' Cairo</b>. <b>COMMERCIAL AUDIT</b> — ' +
    'the whole business on one page for <b>' + ST.s + ' → ' + ST.e + '</b> (' + c.days + ' days). Every number here is ' +
    'computed live from the daily series, so it moves with the range bar above. <b>GPPV</b> is gross profit per visit — ' +
    'online gross profit ÷ sessions — and it is the metric this audit ranks everything by, because revenue per visit ' +
    'hides that we buy expensive traffic for thin-margin categories. Each headline has a <b>net</b> reading beside it ' +
    'that takes refunds and exchanges out. Sessions are on a <b>' + c.basis + '</b> basis' +
    (c.basisF < 1 ? ' (' + (c.basisF * 100).toFixed(1) + '% of Shopify sessions — the rest is foreign traffic that almost never orders)' : '') + '.' +
    (c.adCover < 0.999 ? ' <b style="color:#b8860b">Sessions and spend only exist from ' + (O.ad && O.ad.start) +
      ', so every ratio on this page (GPPV, MER, conversion) is measured over ' + c.adFrom + ' → ' + c.adTo +
      ' — ' + c.adDays + ' of the ' + c.days + ' days in the range. Revenue totals still cover the whole range.</b>' : '');

  let h = auCard('Where the business stands', ST.s + ' → ' + ST.e + ' · ' + c.days + ' days · net readings strip refunds',
    auKpi('Revenue', 'E£ ' + auK(c.rev) + dl(c.rev, p && p.rev), 'GP ' + auP(c.gpPct) + ' · E£ ' + auK(c.gp)) +
    auKpi('Online share', auP(c.onShare) + dl(c.onShare, p && p.onShare), 'E£ ' + auK(c.orev) + ' of E£ ' + auK(c.rev)) +
    auKpi('GPPV', c.gppv ? 'E£ ' + c.gppv.toFixed(2) + dl(c.gppv, p && p.gppv) : '—',
      'net E£ ' + (c.gppvNet ? c.gppvNet.toFixed(2) : '—') + ' · ' + auK(c.sess) + ' sessions, ' + c.basis.toLowerCase()) +
    auKpi('Online MER', auX(c.mer) + dl(c.mer, p && p.mer), 'net ' + auX(c.merNet) + ' · spend E£ ' + auK(c.spend)) +
    auKpi('Media as % of online GP', auP(c.mediaOfGp, 0) + dl(c.mediaOfGp, p && p.mediaOfGp, true),
      'E£ ' + auK(c.spend) + ' spent · E£ ' + auK(c.ogp) + ' made online · ' + auP(c.mediaOfAllGp, 0) + ' of company GP') +
    auKpi('Site conversion', auP(c.cvr, 2) + dl(c.cvr, p && p.cvr), 'AOV E£ ' + auK(c.aov)) +
    auKpi('Online refunds', auP(c.retOn) + dl(c.retOn, p && p.retOn, true), 'E£ ' + auK(c.oref) + ' returned') +
    auKpi('Company MER', auX(c.coMer), 'all revenue ÷ all media'), auBasisChips(), 'blend');

  /* channel split */
  const chan = { 'Branches': c.branch, 'Online (ourkids-eg.com)': c.orev, 'Marketplaces': c.mkt };
  const ctot = c.rev || 1;
  h += auCard('Where the revenue came from',
    'Branches are exact monthly Odoo figures pro-rated across ' + c.days + ' days; online and marketplaces are daily.',
    '', '<div style="padding:4px 14px 12px">' +
    Object.keys(chan).sort((x, y) => chan[y] - chan[x]).filter(k => chan[k] > 0)
      .map(k => auBar(k, chan[k] / ctot * 100, 'E£ ' + auK(chan[k]), auP(chan[k] / ctot * 100) + ' of revenue',
        k === 'Branches' ? '#f0883e' : k === 'Marketplaces' ? '#22a7b8' : 'var(--indigo)')).join('') + '</div>');

  /* funnel */
  const fun = [['Sessions', c.sess], ['Add to cart', c.atc], ['Reached checkout', c.chk], ['Orders', c.oord]];
  h += auCard('The funnel', 'Shopify sessions through to orders over the selected range. The step that loses the most is the one to fix.',
    auKpi('Session → cart', auP(c.sess ? c.atc / c.sess * 100 : null)) +
    auKpi('Cart → checkout', auP(c.atc ? c.chk / c.atc * 100 : null)) +
    auKpi('Checkout → order', auP(c.chk ? c.oord / c.chk * 100 : null)) +
    auKpi('Net conversion', auP(c.cvr, 2)),
    '<div style="padding:4px 14px 12px">' + fun.map((f, i) => auBar(f[0], f[1] / (fun[0][1] || 1) * 100, auK(f[1]),
      i ? auP(fun[i - 1][1] ? f[1] / fun[i - 1][1] * 100 : null) + ' of previous step' : '',
      (i && fun[i - 1][1] && f[1] / fun[i - 1][1] < .35) ? '#e23a63' : 'var(--indigo)')).join('') + '</div>');

  /* spend split + GP after ads */
  const gpa = c.gp - c.spend;
  h += auCard('What the media bought', 'Spend by platform against the gross profit the whole business made in the same window.',
    auKpi('Total media', 'E£ ' + auK(c.spend)) + auKpi('Meta', 'E£ ' + auK(c.ms), auP(c.spend ? c.ms / c.spend * 100 : null) + ' of spend') +
    auKpi('Google', 'E£ ' + auK(c.gs), auP(c.spend ? c.gs / c.spend * 100 : null) + ' of spend') +
    auKpi('TikTok', 'E£ ' + auK(c.ts), auP(c.spend ? c.ts / c.spend * 100 : null) + ' of spend') +
    auKpi('GP after ads', 'E£ ' + auK(gpa), 'company-wide, incl. branches'),
    '<div style="padding:4px 14px 12px">' +
    [['Meta', c.ms], ['Google', c.gs], ['TikTok', c.ts]].map(x =>
      auBar(x[0], c.spend ? x[1] / c.spend * 100 : 0, 'E£ ' + auK(x[1]), auP(c.spend ? x[1] / c.spend * 100 : null))).join('') + '</div>');

  h += auSec('Conversion &amp; collections') + auCvrBlocks();
  h += auSec('What is working — protect it') + auGood(c);
  h += auSec('The gaps — and what each one is worth') + auGaps(c);
  h += auSec('The action plan') + auActions(c);
  h += auAsk(auDecisions(c));
  g.innerHTML = h;
}

/* ---- the argument, generated from the live window ---- */
function auGood(c) {
  const out = [], M = auRoute();
  const b = auBranch(ST.s, ST.e);
  if (c.branch > c.orev) out.push(auFind('E£ ' + auK(c.branch) + ' · ' + auP(100 - c.onShare) + ' of revenue',
    'Retail execution is the strongest thing in the company',
    'The branches did <b>E£ ' + auK(c.branch) + '</b> in this window at a <b>' + auP(b.rev ? b.gp / b.rev * 100 : null) +
    '</b> gross margin, against E£ ' + auK(c.orev) + ' online. Whatever the store teams are doing works, and nothing in this audit should be allowed to disturb it.', true));
  if (c.mer && c.mer > 3) out.push(auFind(auX(c.mer) + ' online MER',
    'Media is not yet upside-down on a revenue basis',
    'Every pound of media returned <b>' + auX(c.mer) + '</b> of online revenue, <b>' + auX(c.merNet) +
    '</b> after refunds. The problem this audit raises is margin, not top line — see the gap below.', true));
  if (M) { const T = auByType(M), home = T.find(x => x.type === 'Homepage');
    const site = T.reduce((a, x) => a + x.orders, 0) / (T.reduce((a, x) => a + x.sessions, 0) || 1);
    if (home && home.cvr > site * 1.3) out.push(auFind((home.cvr / site).toFixed(1) + '× the site average',
      'The homepage converts far better than anything we point ads at',
      'Homepage traffic converts at <b>' + (home.cvr * 100).toFixed(2) + '%</b> against a site average of ' +
      (site * 100).toFixed(2) + '%, on ' + auK(home.sessions) + ' sessions. The people who arrive at the front door already know what they want — the collections we buy traffic into are where it goes wrong.', true)); }
  if (!out.length) out.push(auFind('', 'Nothing in this window clears the bar', 'On the selected range no metric is far enough ahead of trend to call out as a strength. Widen the range.', true));
  return out.join('');
}
function auGaps(c) {
  const out = [], M = auRoute();
  const A = AUD || {};
  if (c.mediaOfGp != null && c.mediaOfGp > 55) {
    const burn = c.spend - c.rOgp;
    out.push(auFind('E£ ' + auK(c.spend) + ' spent to make E£ ' + auK(c.rOgp),
      'Media is eating ' + auP(c.mediaOfGp, 0) + ' of the gross profit it generates',
      'Online gross profit in the ad-covered days was <b>E£ ' + auK(c.rOgp) + '</b> against <b>E£ ' + auK(c.spend) +
      '</b> of media. ' + (burn > 0 ? 'That is <b>E£ ' + auK(burn) + ' more spent than made</b>, before packing, delivery, payment fees or the ' + auP(c.retOn) + ' refund rate.' :
      'It leaves E£ ' + auK(-burn) + ' before packing, delivery, payment fees and the ' + auP(c.retOn) + ' refund rate.') +
      ' On a standalone basis the site is not paying for its own traffic; it is only defensible if that media is also filling the shops, and that has never been measured.'));
  }
  if (c.atc && c.chk / c.atc < 0.5) out.push(auFind('E£ ' + auK(Math.max(0, c.atc * 0.5 - c.chk) * (c.chk ? c.oord / c.chk : 0) * (c.aov || 0)) + ' in this window',
    auP(100 - (c.chk / c.atc * 100), 0) + ' of add-to-carts never reach the checkout',
    '<b>' + auK(c.atc) + '</b> carts produced <b>' + auK(c.chk) + '</b> checkouts and <b>' + auK(c.oord) +
    '</b> orders. Add-to-cart rate is ' + auP(c.sess ? c.atc / c.sess * 100 : null) + ' — people want the product. The loss is one step, on mobile, and nobody has instrumented it.'));
  if (c.retOn != null && c.retOn > 8) out.push(auFind('E£ ' + auK(c.oref) + ' returned',
    'Online refunds are running at ' + auP(c.retOn),
    'Refunds and exchanges took <b>E£ ' + auK(c.oref) + '</b> out of E£ ' + auK(c.orev) +
    ' of online revenue in this window. It is not in any margin number the team looks at daily, and on a channel whose whole gross profit is E£ ' + auK(c.ogp) + ' it is the difference between a contribution and a hole.'));
  if (M) { const cols = M.groups.filter(p => p.type === 'Collection' && p.sessions >= 300);
    const T = auByType(M), site = T.reduce((a, x) => a + x.orders, 0) / (T.reduce((a, x) => a + x.sessions, 0) || 1);
    const bad = cols.filter(p => p.cvr < site * 0.6), w = bad.reduce((a, b) => a + b.sessions, 0);
    if (bad.length) out.push(auFind(auK(w) + ' sessions',
      bad.length + ' collections take real traffic and convert below ' + (site * 60).toFixed(2) + '%',
      'Led by <b>' + bad.slice(0, 3).map(x => x.name + ' (' + (x.cvr * 100).toFixed(2) + '%)').join(', ') +
      '</b>. These are the pages paid traffic lands on. Every pound pointed at them is buying a bounce, and the collections table above names all of them.')); }
  if (A.listing && A.listing['30']) { const L = A.listing['30'].state, gap = L.UNPUB[1] + L.ABSENT[1], tot = gap + L.LIVE[1];
    out.push(auFind('E£ ' + auK(gap) + ' a month',
      auP(tot ? gap / tot * 100 : 0) + ' of what the shops sell has no live product page',
      '<b>' + (L.UNPUB[0] + L.ABSENT[0]).toLocaleString() + '</b> SKUs, of which <b>' + L.UNPUB[0].toLocaleString() +
      '</b> already exist in Shopify and were simply never published. Full ranking on the Listing Gap tab.')); }
  if (A.vendors) { const v = A.vendors.filter(x => x.gmroi != null && x.gmroi < 1 && x.own > 1e6).sort((a, b) => b.own - a.own);
    if (v.length) out.push(auFind('E£ ' + auK(v.reduce((a, x) => a + x.own, 0)) + ' of stock',
      v.length + ' vendors hold more than a million pounds of stock and return under 1.0×',
      'Worst is <b>' + v[0].v + '</b> at ' + auX(v[0].gmroi) + ' on E£ ' + auK(v[0].own) + ' of owned stock' +
      (v[0].r90 ? ', which still took in <b>E£ ' + auK(v[0].r90) + '</b> of goods in the last 90 days' : '') +
      '. Full table and the next-dollar call on the Vendor Capital tab.')); }
  return out.join('') || auFind('', 'No gap clears the threshold on this range', 'Widen the range or check that the sync is current.');
}
function auActions(c) {
  const out = [], A = AUD || {}, M = auRoute();
  if (A.listing && A.listing['30']) out.push(auPlan('Week 1 · no capital',
    'Publish the range we already own and already sell',
    'Push the ACTIVE-but-unpublished Shopify products live, newest arrivals first. Owner: e-commerce merchandising. Measure: listing coverage by in-store revenue, on the Listing Gap tab.'));
  if (c.atc && c.chk / c.atc < 0.5) out.push(auPlan('Weeks 1–6',
    'Instrument the cart and fix the step that loses ' + auP(100 - c.chk / c.atc * 100, 0),
    'Full mobile funnel instrumentation on cart and checkout, then work the top three drop reasons. Owner: e-commerce. Measure: cart→checkout, on this tab, against today’s ' + auP(c.atc ? c.chk / c.atc * 100 : null) + '.'));
  if (M) out.push(auPlan('Weeks 2–4',
    'Re-point spend off the collections that cannot convert it',
    'Move budget from the FIX BEFORE SENDING collections onto the SEND TRAFFIC HERE ones, and fix the weak pages before pointing anything new at them. Owner: performance + merchandising. Measure: collection CVR spread, table above.'));
  if (c.mediaOfGp != null && c.mediaOfGp > 55) out.push(auPlan('Weeks 4–8',
    'Prove or disprove the offline halo with a geo-holdout',
    'Meta is ' + auP(c.spend ? c.ms / c.spend * 100 : null) + ' of media and is judged here on online gross profit alone. Four weeks, two matched governorates dark, measure total revenue including in-store. Owner: performance + finance.'));
  if (A.vendors) out.push(auPlan('This buying cycle',
    'Put GMROI on the order form',
    'Release the cash sitting above three months of cover in the sub-1.0× vendors and redeploy it into the starved high-GMROI ones. Owner: buying. Measure: the release/deploy figures on the Vendor Capital tab.'));
  return out.join('');
}
function auDecisions(c) {
  const A = AUD || {}, d = [];
  if (A.listing) d.push(['Do we publish everything we stock, by default?',
    'Today the default is not to, and it costs about ' + auP(A.listing['30'] ? (A.listing['30'].state.UNPUB[1] + A.listing['30'].state.ABSENT[1]) / (A.listing['30'].state.UNPUB[1] + A.listing['30'].state.ABSENT[1] + A.listing['30'].state.LIVE[1]) * 100 : 0) +
    ' of in-store revenue in online sales we never get. The only real objection is stock accuracy, which is a settings question, not a strategy one.']);
  d.push(['Which number does the e-commerce team get judged on?',
    'I want it to be <b>GPPV net of returns</b>, not revenue and not ROAS. Revenue rewards buying traffic; ROAS rewards buying our own brand name back.']);
  if (c.mediaOfGp != null && c.mediaOfGp > 55) d.push(['Do we run the geo-holdout?',
    'It costs four weeks of slightly lower spend in two governorates. Without it we are defending E£ ' + auK(c.ms) + ' of Meta spend in this window on faith.']);
  d.push(['Who owns the reconciliation?',
    'Odoo, Shopify and GA4 disagree on what a sale is. One person, two weeks, one agreed definition — before the next budget is set on numbers we cannot defend.']);
  return d;
}

/* ======================================================== av — VENDOR CAPITAL */
function rAV() {
  if (TAB !== 'av') return; dCh();
  const g = document.getElementById('g_av'); if (!g) return;
  if (!auHas()) { auLoad(); return auNoData('g_av', 'Vendor GMROI, goods-received recency and the next-dollar call'); }
  const A = AUD, V = A.vendors, wk = String(AUW);
  const rk = { 7: 'r7', 30: 'r30', 90: 'r90', 365: 'r365' }[AUW];
  const inv = A.invTotal || {};
  const rel = V.reduce((a, x) => a + (x.rel || 0), 0), dep = V.reduce((a, x) => a + (x.dep || 0), 0);
  const recv = V.reduce((a, x) => a + (x[rk] || 0), 0);
  const own = V.filter(x => x.gmroi != null);

  document.getElementById('bn_av').innerHTML = '&#8635; <b>VENDOR CAPITAL</b> — every vendor judged on what its stock ' +
    'returns, not what it sells. <b>GMROI</b> is trailing-twelve-month gross profit ÷ the owned stock we hold at cost: ' +
    'below 1.0× a vendor destroys cash. Consigned stock is excluded from the denominator because we have not paid for it — ' +
    '<b>E£ ' + auK(inv.cons) + ' of the E£ ' + auK((inv.own || 0) + (inv.cons || 0)) + ' in our warehouses is not ours</b>. ' +
    '<b>Last received</b> and the receipt columns come from goods booked in from a supplier location. ' +
    '<b>Intake index</b> = a vendor’s share of its category’s 90-day intake ÷ its share of that category’s gross profit; ' +
    'above 1.0 we are buying it harder than it earns. Revenue, GMROI and cover are a rolling twelve months and do not ' +
    'follow the range bar — the receipt columns follow the snapshot chips below.';

  let h = auCard('The capital position', 'Rolling twelve months for revenue and GMROI · receipts over the last ' + AUW + ' days · stock as at ' + (A.pulled || '—'),
    auKpi('Owned stock at cost', 'E£ ' + auK(inv.own)) +
    auKpi('Consigned stock held', 'E£ ' + auK(inv.cons), 'in our warehouses, not on our books') +
    auKpi('Received last ' + AUW + 'd', 'E£ ' + auK(recv)) +
    auKpi('Cash to release', 'E£ ' + auK(rel), 'above 3 months of cover, low return') +
    auKpi('Cash to redeploy', 'E£ ' + auK(dep), 'into starved high-GMROI vendors') +
    auKpi('Net cash freed', 'E£ ' + auK(rel - dep)) +
    auKpi('Vendors ranked', V.length.toLocaleString()), auWinChips(), 'blend');

  /* verdict roll-up */
  const byV = {}; V.forEach(x => { const a = byV[x.verdict] = byV[x.verdict] || { n: 0, rev: 0, rc: 0, rel: 0, dep: 0 };
    a.n++; a.rev += x.rev; a.rc += x[rk] || 0; a.rel += x.rel || 0; a.dep += x.dep || 0; });
  const VC = { 'STOP BUYING': '#e23a63', 'OVER-BOUGHT': '#f0883e', 'BUY MORE': '#12a06e',
               'HOLD': '#5b6ee1', 'WIDEN': '#22a7b8', 'RENEGOTIATE': '#8a93a6', 'FIX MARGIN': '#f0883e', 'TOO SMALL': '#c3c9d4' };
  const vcol = k => VC[Object.keys(VC).find(p => k.indexOf(p) === 0)] || '#5b6ee1';
  const vk = Object.keys(byV).sort((a, b) => byV[b].rc - byV[a].rc);
  const mrc = Math.max.apply(null, vk.map(k => byV[k].rc)) || 1;
  h += auCard('Where the last ' + AUW + ' days of buying actually went',
    'Goods received at cost, grouped by the verdict each vendor earns. Red is money going into stock that does not return.',
    '', '<div style="padding:4px 14px 12px">' + vk.map(k => auBar(k.split(' — ')[0], byV[k].rc / mrc * 100,
      'E£ ' + auK(byV[k].rc), byV[k].n + ' vendors · E£ ' + auK(byV[k].rev) + ' TTM', vcol(k))).join('') + '</div>');

  /* worst GMROI */
  const worst = own.slice().sort((a, b) => a.gmroi - b.gmroi).slice(0, 12);
  h += auCard('Still receiving, still not returning', 'The twelve worst GMROI vendors, with what each has taken in over the last ' + AUW + ' days.',
    '', '<div style="padding:4px 14px 12px">' + worst.map(x => auBar(x.v, x.gmroi / 2 * 100, auX(x.gmroi),
      'E£ ' + auK(x.own) + ' stock · ' + (x.cover != null ? x.cover.toFixed(1) + 'mo cover' : '—') +
      ' · took E£ ' + auK(x[rk]) + ' in ' + AUW + 'd', x.gmroi < 1 ? '#e23a63' : '#f0883e')).join('') + '</div>');

  /* release / deploy */
  const alloc = V.filter(x => (x.rel > 250000 || x.dep > 250000) && x.v !== '(no vendor)')
    .sort((a, b) => (b.rel + b.dep) - (a.rel + a.dep)).slice(0, 14);
  const mal = Math.max.apply(null, alloc.map(x => Math.max(x.rel, x.dep))) || 1;
  h += auCard('The next dollar', 'Cash sitting above three months of cover in vendors that cannot return it, against what the starved high-GMROI vendors could absorb.',
    '', '<div style="padding:4px 14px 12px">' + alloc.map(x => auBar(x.v, Math.max(x.rel, x.dep) / mal * 100,
      'E£ ' + auK(Math.max(x.rel, x.dep)), (x.rel ? 'release' : 'deploy') + ' · GMROI ' + auX(x.gmroi) +
      ' · ' + (x.cover != null ? x.cover.toFixed(1) + 'mo' : '—'), x.rel ? '#e23a63' : '#12a06e')).join('') + '</div>');

  /* the table */
  const hd = ['Vendor', 'Category', 'Verdict', 'SKUs', 'TTM revenue', 'TTM GP', 'GM%', 'Owned stock', 'GMROI', 'Cover',
              'Last received', 'Recv ' + AUW + 'd', 'Intake idx', 'Unlisted rev', 'Release', 'Deploy'];
  const rows = V.slice(0, 200).map(x => '<tr>' +
    '<td style="text-align:left;font-weight:700">' + x.v + '</td><td style="text-align:left">' + (x.cat || '—') + '</td>' +
    '<td style="text-align:left;color:' + vcol(x.verdict) + ';font-weight:700">' + x.verdict.split(' — ')[0] + '</td>' +
    '<td>' + (x.skus || 0).toLocaleString() + '</td><td>' + auK(x.rev) + '</td><td>' + auK(x.gp) + '</td><td>' + auP(x.gm) + '</td>' +
    '<td>' + auK(x.own) + '</td><td style="font-weight:800;color:' + (x.gmroi == null ? '#8a93a6' : x.gmroi < 1 ? '#e23a63' : x.gmroi >= 2.5 ? '#12a06e' : '#0F172A') + '">' + auX(x.gmroi) + '</td>' +
    '<td>' + (x.cover != null ? x.cover.toFixed(1) : '—') + '</td>' +
    '<td>' + (x.last || '—') + (x.days != null ? ' <span style="color:#8a93a6">(' + x.days + 'd)</span>' : '') + '</td>' +
    '<td>' + (x[rk] ? auK(x[rk]) : '—') + '</td>' +
    '<td style="color:' + (x.idx > 1.4 ? '#e23a63' : '#0F172A') + '">' + auX(x.idx) + '</td>' +
    '<td>' + (x.gapRev ? auK(x.gapRev) : '—') + '</td>' +
    '<td>' + (x.rel ? auK(x.rel) : '—') + '</td>' +
    '<td style="color:#12a06e">' + (x.dep ? auK(x.dep) : '—') + '</td></tr>').join('');
  h += auCard('Every vendor', 'Top 200 by trailing-twelve-month revenue. Receipt column follows the snapshot chips.', '',
    '<div style="overflow-x:auto;padding:0 12px 12px"><table class="bt" style="font-size:11.5px"><thead><tr>' +
    hd.map(x => '<th>' + x + '</th>').join('') + '</tr></thead><tbody>' + rows + '</tbody></table></div>');

  /* ---- the argument ---- */
  const bad = own.filter(x => x.gmroi < 1).sort((a, b) => b.own - a.own);
  const star = own.filter(x => x.gmroi >= 2.5 && x.cover != null && x.cover < 2.5).sort((a, b) => b.gmroi - a.gmroi);
  const buying = own.filter(x => x.gmroi < 1.2 && x[rk] > 0).sort((a, b) => b[rk] - a[rk]);
  const consR = V.filter(x => x.model === 'Consignment').reduce((a, x) => a + x.rev, 0);
  h += auSec('What the vendor book is telling us');
  if (bad.length) h += auFind('E£ ' + auK(bad.reduce((a, x) => a + x.own, 0)) + ' of stock',
    bad.length + ' vendors return less than a pound of gross profit per pound of stock',
    'Largest is <b>' + bad[0].v + '</b> — E£ ' + auK(bad[0].own) + ' of owned stock at <b>' + auX(bad[0].gmroi) +
    '</b> on a ' + auP(bad[0].gm) + ' margin and ' + (bad[0].cover != null ? bad[0].cover.toFixed(1) + ' months of cover' : 'no cover reading') +
    '. Its last delivery was ' + (bad[0].last || 'never') + (bad[0].days != null ? ' (' + bad[0].days + ' days ago)' : '') + '.');
  if (buying.length) h += auFind('E£ ' + auK(buying[0][rk]) + ' received in ' + AUW + ' days',
    'We are still buying hardest into stock that does not return',
    '<b>' + buying[0].v + '</b> took in E£ ' + auK(buying[0][rk]) + ' over the last ' + AUW + ' days while returning ' +
    auX(buying[0].gmroi) + (buying[0].idx ? ', which is <b>' + auX(buying[0].idx) + ' its fair share</b> of its category’s intake measured against the gross profit it produces' : '') +
    '. The intake index column ranks every vendor on that test.');
  if (star.length) h += auFind('E£ ' + auK(star.reduce((a, x) => a + x.dep, 0)) + ' of headroom',
    'The vendors that do return are running on fumes', star.slice(0, 5).map(x => '<b>' + x.v + '</b> ' + auX(x.gmroi) +
    ' on ' + (x.cover != null ? x.cover.toFixed(1) : '—') + ' months').join(', ') +
    '. Bringing this group to 2.5 months of cover is where the released cash should go.', true);
  if (consR) h += auFind('E£ ' + auK(consR) + ' a year on zero capital',
    'A fifth of revenue already comes from vendors we finance nothing for',
    V.filter(x => x.model === 'Consignment').length + ' consignment vendors turn over E£ ' + auK(consR) +
    ' with no owned stock against them. Lower margin, but they consume none of the E£ ' + auK(inv.own) + ' of working capital the owned vendors do.', true);
  h += auSec('The action plan');
  h += auPlan('This buying cycle', 'Release ' + 'E£ ' + auK(rel) + ' and redeploy E£ ' + auK(dep),
    'Stop the reorders on everything marked STOP BUYING and OVER-BOUGHT, and move the freed cash into the BUY MORE list. Net E£ ' + auK(rel - dep) +
    ' back in the business, and gross profit rises because the money moves from a sub-1× return to a 3×–11× one. Owner: buying.');
  h += auPlan('Before the next order', 'Put GMROI, months of cover and the intake index on the order form',
    'None of these three numbers exists at the moment the order is placed, which is why the biggest cash commitments are not the best returns. Owner: buying + finance.');
  h += auPlan('Monthly', 'Report consigned versus owned margin separately',
    'Consignment is a fifth of revenue at a materially lower margin and nobody reports it apart. Set a minimum consigned margin by category before the next vendor conversation. Owner: finance.');
  h += auAsk([
    ['Do we stop the reorders on the sub-1.0× vendors?', 'That is E£ ' + auK(rel) + ' of cash. It needs one decision and one owner, and it will be unpopular with the vendors concerned.'],
    ['Do we set a consigned margin floor?', 'Consignment is E£ ' + auK(consR) + ' of revenue at a lower margin, invisible in the current reporting.'],
    ['Who signs off a purchase order against GMROI?', 'Today nobody sees the number when the order is placed. I want it on the form and a name against the exception.']]);
  g.innerHTML = h;
}

/* =========================================================== ag — LISTING GAP */
function rAG() {
  if (TAB !== 'ag') return; dCh();
  const g = document.getElementById('g_ag'); if (!g) return;
  if (!auHas() || !AUD.listing) { auLoad(); return auNoData('g_ag', 'The listing gap'); }
  const A = AUD, L = A.listing[String(AUW)];
  if (!L) return auNoData('g_ag', 'The listing gap for this window');
  const st = L.state, tot = st.LIVE[1] + st.UNPUB[1] + st.ABSENT[1];
  const gapRev = st.UNPUB[1] + st.ABSENT[1], gapSk = st.UNPUB[0] + st.ABSENT[0];
  const cov = tot ? st.LIVE[1] / tot * 100 : 0;
  const ratio = 0.236;                                  // measured online:in-store for live SKUs
  const prize = gapRev * ratio * (30 / AUW);
  document.getElementById('bn_ag').innerHTML = '&#8635; <b>LISTING GAP</b> — what the seven shops sell that the website ' +
    'cannot. Every product that sold in a branch in the window is matched <b>barcode to barcode</b> against the Shopify ' +
    'catalogue and labelled <b>live</b>, <b>in Shopify but never published</b>, or <b>not in Shopify at all</b>. ' +
    'The prize assumes an unlisted SKU, once listed, earns online at the same rate as an already-listed one — ' +
    '<b>23.6 E£ online per 100 E£ in store</b>, the measured ratio for the live SKUs. Snapshot window, not the range bar.';

  let h = auCard('The prize', 'Last ' + AUW + ' days of in-store trade · pulled ' + (A.pulled || '—'),
    auKpi('Listing coverage', auP(cov), 'of in-store revenue is buyable online') +
    auKpi('Not buyable online', 'E£ ' + auK(gapRev), auP(100 - cov) + ' of in-store revenue') +
    auKpi('SKUs in the gap', gapSk.toLocaleString(), 'of ' + (gapSk + st.LIVE[0]).toLocaleString() + ' trading SKUs') +
    auKpi('Online revenue available', 'E£ ' + auK(prize) + '/mo', 'at the measured online:in-store ratio') +
    auKpi('In Shopify, never published', 'E£ ' + auK(st.UNPUB[1]), st.UNPUB[0].toLocaleString() + ' SKUs — one toggle') +
    auKpi('Not in Shopify at all', 'E£ ' + auK(st.ABSENT[1]), st.ABSENT[0].toLocaleString() + ' SKUs — needs photography'),
    auWinChips(), 'blend');

  const mx = Math.max(st.LIVE[1], st.UNPUB[1], st.ABSENT[1]) || 1;
  h += auCard('Where the in-store revenue actually lives', 'By the Shopify status of the SKU that earned it.', '',
    '<div style="padding:4px 14px 12px">' +
    [['Live on the site', st.LIVE, 'var(--indigo)'], ['In Shopify, never published', st.UNPUB, '#f0883e'],
     ['Not in Shopify at all', st.ABSENT, '#e23a63']].map(x =>
      auBar(x[0], x[1][1] / mx * 100, 'E£ ' + auK(x[1][1]), x[1][0].toLocaleString() + ' SKUs · ' + auP(tot ? x[1][1] / tot * 100 : 0), x[2])).join('') + '</div>');

  /* coverage by category */
  const cats = Object.keys(L.cat || {}).filter(k => (L.cat[k].rev || 0) > 10000)
    .map(k => { const c = L.cat[k]; return { k: k, pct: c.rev ? (1 - c.gap / c.rev) * 100 : 0, c: c }; })
    .sort((a, b) => a.pct - b.pct);
  h += auCard('Listing coverage by category', 'Share of each category’s in-store revenue whose SKU is live on the site. Orange is below the company average.',
    '', '<div style="padding:4px 14px 12px">' + cats.map(x => auBar(x.k, x.pct, auP(x.pct),
      x.c.gapSkus.toLocaleString() + ' unlisted · E£ ' + auK(x.c.gap) + ' · E£ ' + auK(x.c.gapStock) + ' of stock',
      x.pct < cov ? '#f0883e' : 'var(--indigo)')).join('') + '</div>');

  /* priority list */
  const gaps = (A.gaps || []);
  const inStock = gaps.filter(x => x.s > 0);
  h += auCard('The priority list',
    'Ranked by 30-day in-store gross profit, weighted up for arrivals under 120 days old and down where there is no stock to sell. ' +
    inStock.length.toLocaleString() + ' of the top ' + gaps.length.toLocaleString() + ' shown are in stock right now — those need a merchandiser, not a purchase order. ' +
    'Full file: ' + (A.gapsN || gaps.length).toLocaleString() + ' SKUs.',
    auKpi('Shown', gaps.length.toLocaleString()) + auKpi('In stock now', inStock.length.toLocaleString()) +
    auKpi('Stock on them', 'E£ ' + auK(inStock.reduce((a, x) => a + x.sv, 0))) +
    auKpi('New arrivals (<120d)', gaps.filter(x => x.a <= 120).length.toLocaleString()),
    '<div style="overflow-x:auto;padding:0 12px 12px"><table class="bt" style="font-size:11.5px"><thead><tr>' +
    ['#', 'Product', 'Category', 'Vendor', 'Shopify', 'Age (d)', 'Units 30d', 'In-store rev', 'GP', 'Stock', 'Stock at cost']
      .map(x => '<th>' + x + '</th>').join('') + '</tr></thead><tbody>' +
    gaps.slice(0, 200).map((x, i) => '<tr><td>' + (i + 1) + '</td>' +
      '<td style="text-align:left;font-weight:600">' + (x.n || '').replace(/</g, '&lt;') + '</td>' +
      '<td style="text-align:left">' + x.c + '</td><td style="text-align:left">' + x.v + '</td>' +
      '<td style="color:' + (x.st === 'UNPUB' ? '#f0883e' : '#e23a63') + ';font-weight:700">' +
      (x.st === 'UNPUB' ? 'never published' : 'not in Shopify') + '</td>' +
      '<td>' + x.a + '</td><td>' + x.q + '</td><td>' + auK(x.r) + '</td><td>' + auK(x.g) + '</td>' +
      '<td style="color:' + (x.s > 0 ? '#12a06e' : '#e23a63') + ';font-weight:700">' + x.s + '</td><td>' + auK(x.sv) + '</td></tr>').join('') +
    '</tbody></table></div>');

  const newA = gaps.filter(x => x.a <= 120), stockVal = inStock.reduce((a, x) => a + x.sv, 0);
  const worstCat = cats.length ? cats[0] : null;
  h += auSec('What the gap is telling us');
  h += auFind('E£ ' + auK(gapRev) + ' per ' + AUW + ' days',
    auP(100 - cov) + ' of what the branches sell has no live product page',
    '<b>' + gapSk.toLocaleString() + '</b> SKUs. Not a long tail — they average E£ ' +
    auK(gapSk ? gapRev / gapSk : 0) + ' each over the window.');
  h += auFind(st.UNPUB[0].toLocaleString() + ' SKUs · E£ ' + auK(st.UNPUB[1]),
    'Most of the gap is already built in Shopify and was never published',
    'They have a barcode, a title and a price. Somebody made them and nobody pressed publish. That is ' +
    auP(tot ? st.UNPUB[1] / tot * 100 : 0) + ' of in-store revenue sitting behind a single toggle.');
  if (worstCat) h += auFind(auP(worstCat.pct) + ' coverage',
    worstCat.k + ' is the worst-covered category',
    '<b>' + worstCat.c.gapSkus.toLocaleString() + '</b> ' + worstCat.k + ' SKUs doing E£ ' + auK(worstCat.c.gap) +
    ' in the shops are not on the site, with E£ ' + auK(worstCat.c.gapStock) + ' of stock behind them.');
  if (newA.length) h += auFind(newA.length.toLocaleString() + ' SKUs',
    'The newer the stock, the less likely it is to be online',
    'Every one of these was first stocked in the last 120 days and has never gone live. Buying is landing range faster than merchandising is publishing it, and the newest range is the range with the most demand behind it.');
  if (inStock.length) h += auFind('E£ ' + auK(stockVal) + ' of stock',
    inStock.length.toLocaleString() + ' of them are sitting in a warehouse right now',
    'In stock, sellable, unlisted. Listing them costs a merchandiser’s time and nothing else.', true);
  h += auSec('The plan, by wave');
  h += auPlan('Week 1 · zero capital', 'Publish the in-stock new arrivals',
    'Highest revenue, freshest demand, stock on the shelf, most already built in Shopify. Owner: e-commerce merchandising.');
  h += auPlan('Weeks 2–4', 'Publish the rest of the in-stock range',
    'Older range, still selling, still in stock. Batch by category, worst-covered first. Owner: e-commerce merchandising.');
  h += auPlan('Weeks 2–6', 'Reorder and list the sold-out new range',
    'These are proven sellers with no stock left — a buying job, not a merchandising one. List on arrival, not six weeks later. Owner: buying.');
  h += auPlan('Ongoing · the rule that stops it recurring', 'No goods-received line closes without a Shopify record',
    'A weekly exception report to the same person every Monday. Owner: buying + IT.');
  h += auAsk([
    ['Do we publish everything we stock, by default?', 'Today the default is not to. I want it reversed: everything we buy goes online unless someone gives a reason.'],
    ['Who owns publish-on-arrival, and do they have the headcount?', st.ABSENT[0].toLocaleString() + ' of these SKUs need photography and copy from scratch. I would rather be told it is one person and a quarter than be told yes and watch it not happen.'],
    ['Do we fix the stock feed before publishing more?', 'Publishing on top of an inventory feed nobody trusts converts a merchandising problem into a customer-service one.']]);
  g.innerHTML = h;
}

/* ============================================================ aq — DEMAND GAP */
function rAQ() {
  if (TAB !== 'aq') return; dCh(); auLoad();
  const g = document.getElementById('g_aq'); if (!g) return;
  const SI = O.searchIntel || {}, terms = SI.terms || [];
  const c = auWin();
  const BR = ['our kids', 'ourkids', 'اور كيدز', 'اوركيدز', 'اور كدز', 'اور كيدس', 'فروع our'];
  const isBr = t => BR.some(b => (t || '').toLowerCase().indexOf(b) >= 0);
  const br = terms.filter(x => isBr(x.t)), nb = terms.filter(x => !isBr(x.t));
  const sum = (a, k) => a.reduce((s, x) => s + (x[k] || 0), 0);
  const zero = nb.filter(x => !x.cn);
  document.getElementById('bn_aq').innerHTML = '&#8635; <b>DEMAND GAP</b> — the demand that already reaches us and does ' +
    'not convert. Search terms are live Google data for <b>' + ((SI.win || [])[0] || '—') + ' → ' + ((SI.win || [])[1] || '—') +
    '</b>; the funnel block follows the range bar. The argument this tab makes is that fixing capture is worth more than ' +
    'any new category, and costs no capital.';

  let h = auCard('Where the demand leaks', 'Funnel over ' + ST.s + ' → ' + ST.e + ' · search terms over the Google window',
    auKpi('Sessions', auK(c.sess)) +
    auKpi('Carts lost before checkout', auP(c.atc ? (1 - c.chk / c.atc) * 100 : null),
      auK(c.atc - c.chk) + ' of ' + auK(c.atc) + ' carts') +
    auKpi('Checkouts lost', auP(c.chk ? (1 - c.oord / c.chk) * 100 : null), auK(c.chk - c.oord) + ' of ' + auK(c.chk)) +
    auKpi('Brand share of search spend', auP(sum(terms, 'sp') ? sum(br, 'sp') / sum(terms, 'sp') * 100 : null),
      'E£ ' + auK(sum(br, 'sp')) + ' buying back our own name') +
    auKpi('Non-brand spend with zero orders', auP(sum(nb, 'sp') ? sum(zero, 'sp') / sum(nb, 'sp') * 100 : null),
      'E£ ' + auK(sum(zero, 'sp')) + ' · ' + zero.length.toLocaleString() + ' terms') +
    auKpi('Non-brand ROAS', auX(sum(nb, 'sp') ? sum(nb, 'cv') / sum(nb, 'sp') : null),
      'brand reads ' + auX(sum(br, 'sp') ? sum(br, 'cv') / sum(br, 'sp') : null)), '', 'blend');

  /* what one recovered step is worth */
  const step = [['Cart → checkout to 22%', c.atc * .22 - c.chk], ['Cart → checkout to 30%', c.atc * .30 - c.chk]];
  const perOrd = c.chk ? c.oord / c.chk : 0;
  h += auCard('What one step is worth', 'Extra orders and revenue if the cart-to-checkout step recovers, at today’s checkout-to-order rate and AOV. Arithmetic on the selected range, not a forecast.',
    step.map(s => auKpi(s[0], 'E£ ' + auK(Math.max(0, s[1]) * perOrd * (c.aov || 0)),
      '+' + auK(Math.max(0, s[1]) * perOrd) + ' orders')).join('') +
    auKpi('Today', auP(c.atc ? c.chk / c.atc * 100 : null), 'of carts reach checkout'), '');

  /* biggest zero-converting spend */
  const zs = zero.slice().sort((a, b) => b.sp - a.sp).slice(0, 15);
  const mz = Math.max.apply(null, zs.map(x => x.sp)) || 1;
  h += auCard('Paid demand that produced nothing', 'Google search terms with spend and zero orders in the window. Where these are our own brands, the cause is stock or the product page, not the keyword.',
    '', '<div style="padding:4px 14px 12px">' + zs.map(x => auBar((x.t || '').slice(0, 34), x.sp / mz * 100,
      'E£ ' + auK(x.sp), auK(x.ck) + ' clicks · ' + auK(x.im) + ' impressions', '#e23a63')).join('') + '</div>');

  /* best converting demand, under-funded */
  const win = nb.filter(x => x.cn > 0 && x.ck > 40).map(x => ({ t: x.t, cvr: x.cn / x.ck * 100, roas: x.sp ? x.cv / x.sp : 0, sp: x.sp, ck: x.ck }))
    .sort((a, b) => b.roas - a.roas).slice(0, 15);
  const mw = Math.max.apply(null, win.map(x => x.roas)) || 1;
  h += auCard('Demand that converts and is starved', 'Non-brand terms with the best return in the window. Small spend, high return — the obvious place for the next media pound.',
    '', '<div style="padding:4px 14px 12px">' + win.map(x => auBar((x.t || '').slice(0, 34), x.roas / mw * 100,
      auX(x.roas), 'E£ ' + auK(x.sp) + ' spent · CVR ' + auP(x.cvr, 2), '#12a06e')).join('') + '</div>');

  /* ---- category economics, from the audit block ---- */
  const A = AUD || {}, CS = A.catSales || {}, CI = A.catIntake || {}, wk2 = String(AUW);
  const cats = Object.keys(CS).filter(k => k && k !== '?' && ((CS[k][wk2] || [])[0] || 0) > 10000).map(k => {
    const v = CS[k][wk2] || [0, 0, 0, 0], lc = (A.listing && A.listing[wk2] && A.listing[wk2].cat[k]) || null;
    const rev = v[0] + v[2], gp = v[1] + v[3];
    return { k: k, inRev: v[0], inGp: v[1], onRev: v[2], onGp: v[3], rev: rev, gp: gp,
             gm: rev ? gp / rev * 100 : 0, onShare: rev ? v[2] / rev * 100 : 0,
             intake: (CI[k] || {})[wk2] || 0,
             listed: lc && lc.rev ? (1 - lc.gap / lc.rev) * 100 : null,
             gapRev: lc ? lc.gap : 0 };
  }).sort((a, b) => b.rev - a.rev);
  if (cats.length) {
    const tR = cats.reduce((a, x) => a + x.rev, 0), tI = cats.reduce((a, x) => a + x.intake, 0) || 1;
    h += auSec('Category economics');
    h += auCard('What each category sells, earns, and gets bought',
      'Last ' + AUW + ' days. <b>Intake</b> is goods received at cost in the same window — the share column is what that category took of all buying. Where intake share runs far ahead of gross-profit share, the buying is pointed the wrong way.',
      auKpi('Categories trading', cats.length) +
      auKpi('Biggest by revenue', cats[0].k, 'E£ ' + auK(cats[0].rev) + ' · ' + auP(cats[0].rev / tR * 100)) +
      auKpi('Biggest by intake', (cats.slice().sort((a, b) => b.intake - a.intake)[0] || {}).k || '—',
        'E£ ' + auK((cats.slice().sort((a, b) => b.intake - a.intake)[0] || {}).intake || 0)) +
      auKpi('Total received', 'E£ ' + auK(tI)),
      '<div style="overflow-x:auto;padding:0 12px 12px"><table class="bt" style="font-size:11.5px"><thead><tr>' +
      ['Category', 'In-store rev', 'Online rev', 'Online %', 'Gross profit', 'GM%', 'Share of GP', 'Received', 'Share of intake', 'Intake vs GP', 'Listed %']
        .map(x => '<th>' + x + '</th>').join('') + '</tr></thead><tbody>' +
      cats.map(x => { const gpS = cats.reduce((a, y) => a + y.gp, 0), gs = gpS ? x.gp / gpS * 100 : 0,
          is = x.intake / tI * 100, idx = gs > 0.5 ? is / gs : null;
        return '<tr><td style="text-align:left;font-weight:700">' + x.k + '</td>' +
          '<td>' + auK(x.inRev) + '</td><td>' + auK(x.onRev) + '</td><td>' + auP(x.onShare) + '</td>' +
          '<td>' + auK(x.gp) + '</td><td>' + auP(x.gm) + '</td><td>' + auP(gs) + '</td>' +
          '<td>' + auK(x.intake) + '</td><td>' + auP(is) + '</td>' +
          '<td style="font-weight:800;color:' + (idx > 1.4 ? '#e23a63' : idx != null && idx < 0.7 ? '#12a06e' : '#0F172A') + '">' +
          (idx != null ? auX(idx) : '—') + '</td>' +
          '<td>' + (x.listed != null ? auP(x.listed) : '—') + '</td></tr>'; }).join('') +
      '</tbody></table></div>');
    const over = cats.filter(x => { const gpS = cats.reduce((a, y) => a + y.gp, 0); const gs = gpS ? x.gp / gpS * 100 : 0;
      return gs > 0.5 && (x.intake / tI * 100) / gs > 1.4; }).sort((a, b) => b.intake - a.intake);
    h += auSec('What the demand and the buying are telling us');
    if (over.length) h += auFind('E£ ' + auK(over[0].intake) + ' received in ' + AUW + ' days',
      over[0].k + ' is taking more of the buying than it earns of the profit',
      'It took <b>' + auP(over[0].intake / tI * 100) + '</b> of everything received while producing ' +
      auP(cats.reduce((a, y) => a + y.gp, 0) ? over[0].gp / cats.reduce((a, y) => a + y.gp, 0) * 100 : 0) +
      ' of gross profit, on a ' + auP(over[0].gm) + ' margin and ' + auP(over[0].onShare) + ' online penetration.');
    const thin = cats.filter(x => x.onShare < 8 && x.rev > 200000).sort((a, b) => b.rev - a.rev);
    if (thin.length) h += auFind('E£ ' + auK(thin[0].rev) + ' a window',
      thin[0].k + ' barely exists online',
      'Only <b>' + auP(thin[0].onShare) + '</b> of it sells online' + (thin[0].listed != null ?
      ', and it is ' + auP(thin[0].listed) + ' listed — so this is a merchandising and traffic problem, not a listing one' : '') + '.');
  }
  h += auSec('The plan');
  h += auPlan('Sprint 1', 'Stop paying for terms that produced nothing',
    'E£ ' + auK(sum(zero, 'sp')) + ' went to ' + zero.length.toLocaleString() +
    ' non-brand terms with zero orders. Where those are our own brands the cause is stock or the product page, not the keyword — fix the page, do not negate the term. Owner: performance + merchandising.');
  h += auPlan('Sprint 1–3', 'Fund the terms that convert',
    'The starved list above returns well on small spend. Move budget onto it before adding any new category. Owner: performance.');
  h += auPlan('Weeks 2–8', 'Fix the cart before buying more traffic',
    auP(c.atc ? (1 - c.chk / c.atc) * 100 : null) + ' of carts never reach checkout. Buying more demand into that is buying a bigger leak. Owner: e-commerce.');
  h += auAsk([
    ['Do we agree capture comes before category?', 'This tab argues against opening anything new until the cart and the stock feed are fixed. If we would rather grow range, say so now — it changes every priority.'],
    ['Who owns the cart investigation, and by when?', 'It is the single largest unmeasured number in the business. I want a named owner and a date, not a backlog ticket.'],
    ['Do we keep bidding on categories we half-serve?', 'Half-in is the most expensive option: we pay for the click and lose it on the page. Either the range and content get built, or we stop buying the traffic.']]);
  g.innerHTML = h;
}

/* ===================== deck furniture — findings, plans, decisions ==========
   The standalone decks were not KPI strips; they were an argument. These helpers
   rebuild that structure inside the dashboard: a finding carries what it is worth,
   the plan carries an owner and a measure, and the decisions are the things only
   the founders can answer. Every number in them is computed from the live window,
   so the prose cannot go stale the way a pasted deck does. */
function auSec(t) {
  return '<div class="card full" style="padding:0;border:none;background:none;box-shadow:none;margin:14px 0 2px">' +
    '<div style="font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;color:#8a93a6;font-weight:800;' +
    'padding-bottom:7px;border-bottom:1px solid #e3e6ee">' + t + '</div></div>';
}
function auFind(tag, head, body, good) {
  /* Matches the host dashboard's card idiom -- a top accent and a pill badge, the same
     treatment .card/.bg use everywhere else on this page -- rather than the side rule the
     standalone decks used. These live inside the dashboard now, so they should look it. */
  const c = good ? '#12a06e' : '#f0883e';
  const tint = good ? '#e8f6f0' : '#fdf0e6';
  return '<div class="card full" style="border-top:3px solid ' + c + '">' +
    (tag ? '<div style="display:inline-block;font-size:9.5px;font-weight:800;letter-spacing:.4px;' +
      'text-transform:uppercase;padding:3px 8px;border-radius:20px;background:' + tint + ';color:' + c +
      ';margin-bottom:7px">' + tag + '</div>' : '') +
    '<div style="font-size:14.5px;font-weight:800;margin-bottom:5px;line-height:1.3">' + head + '</div>' +
    '<div style="font-size:13px;color:#4a5266;line-height:1.55">' + body + '</div></div>';
}
function auPlan(when, head, body) {
  return '<div class="card full" style="border-top:3px solid #5b6ee1">' +
    '<div style="display:inline-block;font-size:9.5px;font-weight:800;letter-spacing:.4px;text-transform:uppercase;' +
    'padding:3px 8px;border-radius:20px;background:#eef0fb;color:#4b4fd0;margin-bottom:7px">' + when + '</div>' +
    '<div style="font-size:14.5px;font-weight:800;margin-bottom:5px">' + head + '</div>' +
    '<div style="font-size:13px;color:#4a5266;line-height:1.55">' + body + '</div></div>';
}
function auAsk(items) {
  return '<div class="card full" style="border-top:3px solid #0F172A">' +
    '<div style="font-size:14.5px;font-weight:800;margin-bottom:8px">What I need from you</div>' +
    '<div style="font-size:13px;color:#4a5266;line-height:1.6">' +
    items.map((x, i) => '<b>' + (i + 1) + '. ' + x[0] + '</b> ' + x[1]).join('<br><br>') + '</div></div>';
}
const auArrow = (v, f) => v == null ? '<span style="color:#9aa3b5">new</span>' :
  Math.abs(v) < 1e-9 ? '<span style="color:#9aa3b5">flat</span>' :
  '<span style="color:' + (v > 0 ? '#12a06e' : '#e23a63') + ';font-weight:800">' + (v > 0 ? '▲' : '▼') + ' ' + f(Math.abs(v)) + '</span>';

/* ---- landing-page CVR, reusing the Traffic Routing model so the two tabs agree ---- */
function auRoute() {
  try { const M = trModel(); if (!M) return null; return M; } catch (e) { return null; }
}
function auByType(M) {
  const t = {};
  M.pages.forEach(p => { const a = t[p.type] || (t[p.type] = { type: p.type, sessions: 0, atcW: 0, chkW: 0, orders: 0, pSessions: 0, pOrders: 0, n: 0 });
    a.sessions += p.sessions; a.atcW += p.atcW; a.chkW += p.chkW; a.orders += p.orders;
    a.pSessions += p.pSessions; a.pOrders += p.pOrders; a.n++; });
  return Object.keys(t).map(k => trFin(t[k])).filter(x => x.sessions > 200).sort((a, b) => b.sessions - a.sessions);
}

/* ---- the CVR comparison + collections league table, shared by au and aq ---- */
function auCvrBlocks() {
  const M = auRoute();
  if (!M) return '<div class="card full" style="padding:14px 17px;font-size:13px;color:#8a93a6">Landing-page CVR is not in this sync yet — it arrives with the Shopify routing pull.</div>';
  const C = M.C, days = C.days || 14, site = C.keptSess ? 0 : 0;
  const T = auByType(M), tot = T.reduce((a, b) => a + b.sessions, 0) || 1;
  const site_cvr = T.reduce((a, b) => a + b.orders, 0) / tot;
  const pf = v => (v * 100).toFixed(2) + '%';
  const pp = v => (v * 100).toFixed(2) + 'pp';

  /* 1. CVR by landing-page type, vs the window before */
  let h = auCard('Conversion by where the visit lands',
    'Last ' + days + ' days against the ' + days + ' before it. Site average is ' + pf(site_cvr) +
    '. A page type below that average is being subsidised by the ones above it.',
    T.slice(0, 5).map(x => auKpi(x.type, pf(x.cvr), auK(x.sessions) + ' sessions · ' + auArrow(x.dCvr, pp))).join(''),
    '<div style="overflow-x:auto;padding:0 12px 12px"><table class="bt" style="font-size:11.5px"><thead><tr>' +
    ['Landing page type', 'Pages', 'Sessions', 'Share', 'Session→cart', 'Cart→checkout', 'Checkout→order', 'CVR', 'vs prev', 'Orders', 'Sessions Δ']
      .map(x => '<th>' + x + '</th>').join('') + '</tr></thead><tbody>' +
    T.map(x => '<tr><td style="text-align:left;font-weight:700">' + x.type + '</td><td>' + x.n + '</td>' +
      '<td>' + auK(x.sessions) + '</td><td>' + auP(x.sessions / tot * 100) + '</td>' +
      '<td>' + pf(x.atc) + '</td><td>' + pf(x.atc2chk) + '</td><td>' + pf(x.chk2buy) + '</td>' +
      '<td style="font-weight:800;color:' + (x.cvr >= site_cvr ? '#12a06e' : '#e23a63') + '">' + pf(x.cvr) + '</td>' +
      '<td>' + auArrow(x.dCvr, pp) + '</td><td>' + auK(x.orders) + '</td><td>' + auArrow(x.dSessP, v => (v * 100).toFixed(0) + '%') + '</td></tr>').join('') +
    '</tbody></table></div>');

  /* 2. collections league table */
  const cols = M.groups.filter(p => p.type === 'Collection' && p.sessions >= 300).sort((a, b) => b.sessions - a.sessions);
  const send = cols.filter(p => p.cvr >= site_cvr * 1.15);
  const fix = cols.filter(p => p.cvr < site_cvr * 0.6);
  const wasted = fix.reduce((a, b) => a + b.sessions, 0);
  h += auCard('The collections, ranked on what they do with the traffic',
    cols.length + ' collections above 300 sessions in ' + days + ' days. <b>' + fix.length + '</b> of them take ' +
    auK(wasted) + ' sessions and convert below ' + pf(site_cvr * 0.6) + ' — that is the traffic to move, and the pages to fix before moving any more onto them.',
    auKpi('Collections ranked', cols.length) +
    auKpi('Send traffic here', send.length, 'converting 15%+ above site') +
    auKpi('Fix before sending', fix.length, 'converting 40%+ below site') +
    auKpi('Sessions on the weak ones', auK(wasted), auP(cols.reduce((a, b) => a + b.sessions, 0) ? wasted / cols.reduce((a, b) => a + b.sessions, 0) * 100 : 0) + ' of collection traffic'),
    '<div style="overflow-x:auto;padding:0 12px 12px"><table class="bt" style="font-size:11.5px"><thead><tr>' +
    ['Collection', 'Sessions', 'Sessions Δ', 'Session→cart', 'Cart→checkout', 'CVR', 'vs prev', 'Orders', 'Verdict']
      .map(x => '<th>' + x + '</th>').join('') + '</tr></thead><tbody>' +
    cols.slice(0, 40).map(x => {
      const v = x.cvr >= site_cvr * 1.15 ? ['SEND TRAFFIC HERE', '#12a06e'] :
                x.cvr < site_cvr * 0.6 ? ['FIX BEFORE SENDING', '#e23a63'] : ['HOLD', '#8a93a6'];
      return '<tr><td style="text-align:left;font-weight:600">' + x.name + '</td><td>' + auK(x.sessions) + '</td>' +
        '<td>' + auArrow(x.dSessP, y => (y * 100).toFixed(0) + '%') + '</td>' +
        '<td>' + pf(x.atc) + '</td><td>' + pf(x.atc2chk) + '</td>' +
        '<td style="font-weight:800;color:' + v[1] + '">' + pf(x.cvr) + '</td>' +
        '<td>' + auArrow(x.dCvr, pp) + '</td><td>' + x.orders + '</td>' +
        '<td style="color:' + v[1] + ';font-weight:700">' + v[0] + '</td></tr>';
    }).join('') + '</tbody></table></div>');
  return h;
}
