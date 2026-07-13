"""The cards, rendered as HTML and shot with headless Chrome.

Matplotlib was the wrong tool. Every layout bug in this project came from placing text at an
absolute coordinate and hoping it did not collide with the text next to it. HTML does not have
that problem: a box is as tall as its contents, and two boxes cannot overlap. So the cards are
HTML now, and Chrome takes the picture.

If Chrome is not on the machine, analyze.py falls back to the old matplotlib cards, so this can
never take the report down.
"""
import os, re, shutil, subprocess, tempfile, html as _html

import analyze as az
from analyze import (_k, r2, pct, safe, _clip, pctile, decompose, attribution, proposals,
                     fatigue_scan, winning_pattern, hit_rate, brief_lines, daywk, is_catalogue,
                     CRIT, FATG, FATG_CRIT, SEGN, DATES, WIN_TITLE, WIN_FOOT, _fmt_range)

W = 1400                      # every card is this wide. Fixed canvas, no surprises.


# ---------------------------------------------------------------- chrome
def _chrome():
    for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
              "/usr/bin/google-chrome", "/usr/bin/chromium"):
        p = shutil.which(c) if not c.startswith("/") else (c if os.path.exists(c) else None)
        if p: return p
    return None


def shoot(html, height):
    """HTML in, PNG bytes out. Chrome screenshots exactly the window, so the card declares its
    own height and the body is clipped to it. No scrollbars, no half-cut panels."""
    exe = _chrome()
    if not exe: return None
    d = tempfile.mkdtemp()
    f = os.path.join(d, "c.html"); png = os.path.join(d, "c.png")
    with open(f, "w") as fh: fh.write(html)
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           "--force-device-scale-factor=2", "--default-background-color=00000000",
           "--virtual-time-budget=2500",
           "--window-size=%d,%d" % (W, int(height)),
           "--screenshot=%s" % png, "file://" + f]
    try:
        subprocess.run(cmd, capture_output=True, timeout=90)
        if os.path.exists(png) and os.path.getsize(png) > 2000:
            with open(png, "rb") as fh: return fh.read()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- design system
CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{width:%dpx;background:#F4F5F7;font-family:'DejaVu Sans','Segoe UI',Helvetica,Arial,sans-serif;
     color:#111827;-webkit-font-smoothing:antialiased}
.pg{padding:26px 28px 22px}
.hd{display:flex;align-items:center;gap:10px;font-size:12px;color:#6B7280;
    letter-spacing:.06em;text-transform:uppercase;font-weight:700;margin-bottom:6px}
.hd .sep{color:#D1D5DB}
.hd .dates{margin-left:auto;display:flex;align-items:center;gap:8px;text-transform:none;
           letter-spacing:0;font-weight:600}
.chip{background:#fff;border:1px solid #E5E7EB;border-radius:8px;padding:5px 10px;font-size:11px;color:#374151}
.chip b{color:#111827}
h1{font-size:31px;font-weight:800;letter-spacing:-.5px;margin:2px 0 3px}
.sub{font-size:13.5px;color:#6B7280;margin-bottom:16px}
.card{background:#fff;border:1px solid #E5E7EB;border-radius:14px;padding:16px 18px;margin-bottom:12px;
      box-shadow:0 1px 2px rgba(16,24,40,.04)}
.ch{display:flex;align-items:center;gap:9px;margin-bottom:13px}
.ch .ic{width:26px;height:26px;border-radius:7px;background:#EEF2FF;color:#4F46E5;font-size:13px;
        display:flex;align-items:center;justify-content:center;font-weight:800}
.ch h2{font-size:14.5px;font-weight:800;letter-spacing:.01em}
.ch .note{margin-left:auto;font-size:11.5px;color:#9CA3AF;font-weight:600}
.grid{display:grid;gap:10px}
.g4{grid-template-columns:repeat(4,1fr)}
.g3{grid-template-columns:repeat(3,1fr)}
.g2{grid-template-columns:repeat(2,1fr)}
.tile{border:1px solid #EEF0F3;border-radius:11px;padding:11px 12px;background:#FCFCFD}
.tile .lb{font-size:10px;font-weight:800;letter-spacing:.07em;color:#8A94A6;text-transform:uppercase}
.tile .rowv{display:flex;align-items:baseline;gap:8px;margin-top:5px}
.tile .v{font-size:23px;font-weight:800;letter-spacing:-.4px}
.tile .was{font-size:11px;color:#9CA3AF;margin-top:3px}
.pill{font-size:11px;font-weight:800;padding:2px 7px;border-radius:999px;white-space:nowrap}
.up{background:#ECFDF3;color:#067647}
.dn{background:#FEF3F2;color:#B42318}
.nu{background:#F2F4F7;color:#475467}
.bl{background:#EFF6FF;color:#1D4ED8}
.am{background:#FFFAEB;color:#B54708}
.callout{display:flex;gap:9px;align-items:flex-start;border-radius:10px;padding:10px 12px;
         font-size:13px;line-height:1.45;margin-top:11px}
.c-g{background:#F0FDF4;color:#15803D;border:1px solid #DCFCE7}
.c-r{background:#FEF2F2;color:#B91C1C;border:1px solid #FEE2E2}
.c-b{background:#EFF6FF;color:#1E40AF;border:1px solid #DBEAFE}
.c-a{background:#FFFBEB;color:#B45309;border:1px solid #FDE68A}
table{width:100%%;border-collapse:collapse}
th{font-size:9.5px;font-weight:800;letter-spacing:.07em;color:#8A94A6;text-transform:uppercase;
   text-align:right;padding:0 0 7px}
th.l{text-align:left}
td{font-size:12.5px;font-weight:700;text-align:right;padding:9px 0;border-top:1px solid #F1F3F5;
   font-variant-numeric:tabular-nums}
td.l{text-align:left;font-weight:800}
td .s{display:block;font-size:10.5px;font-weight:600;color:#9CA3AF;margin-top:2px}
.bar{position:relative;left:0;width:4px;height:30px;border-radius:3px;display:inline-block;
     vertical-align:middle;margin-right:9px}
.g{color:#067647}.r{color:#B42318}.b{color:#1D4ED8}.a{color:#B54708}.m{color:#6B7280}
.ft{display:flex;font-size:11px;color:#9CA3AF;padding-top:4px}
.ft .rt{margin-left:auto}
.tag{font-size:10px;font-weight:800;padding:2px 7px;border-radius:6px;letter-spacing:.03em}
.spark{display:block}
"""


def esc(s): return _html.escape(str(s or ""))


def pill(p, lower_better=False, neutral=False, suffix="%"):
    """A percent with a colour that means BUSINESS, not direction. CPP down is good."""
    if p is None: return '<span class="pill nu">n/a</span>'
    cls = "nu"
    if neutral:
        cls = "bl"
    elif abs(p) < 0.5:
        cls = "nu"
    else:
        good = (p < 0) if lower_better else (p > 0)
        cls = "up" if good else "dn"
    ar = "▲" if p >= 0 else "▼"
    return '<span class="pill %s">%s %+.0f%s</span>' % (cls, ar, p, suffix)


def tile(label, value, was=None, p=None, lower_better=False, neutral=False):
    return ('<div class="tile"><div class="lb">%s</div><div class="rowv"><div class="v">%s</div>%s</div>'
            '<div class="was">%s</div></div>'
            % (esc(label), esc(value), pill(p, lower_better, neutral) if p is not None else "",
               ("was %s" % esc(was)) if was is not None else "&nbsp;"))


def page(A, win, title, sub, body, height):
    cur, pre = DATES.get("label"), DATES.get("p_label")
    head = ('<div class="hd"><span>%s</span><span class="sep">|</span><span>%s</span>'
            '<div class="dates"><span class="chip">THIS <b>%s</b></span>'
            '<span class="chip">vs PREVIOUS <b>%s</b></span></div></div>'
            % (esc(A["account"]["name"].upper()), esc(WIN_TITLE.get(win, "MEMO")),
               esc(_fmt_range(cur)), esc(_fmt_range(pre))))
    foot = ('<div class="ft"><div>%s</div><div class="rt">Audience is Meta\'s own breakdown '
            'by audience segment.</div></div>' % esc(WIN_FOOT.get(win, "")))
    return ("<!doctype html><meta charset=utf-8><style>%s</style><body><div class=pg>%s<h1>%s</h1>"
            "<div class=sub>%s</div>%s%s</div></body>"
            % (CSS % W, head, esc(title), esc(sub), body, foot)), height


# ---------------------------------------------------------------- sparkline
def spark(series, key="rev", w=150, h=34, lo=None, hi=None):
    """That entity's own last 14 days, its normal band, and today. Small, honest, no axes."""
    pts = [(p.get(key) or 0) for p in (series or [])]
    if len(pts) < 4: return ""
    mn, mx = min(pts), max(pts)
    rng = (mx - mn) or 1.0
    n = len(pts)
    X = lambda i: 2 + i * (w - 4) / (n - 1)
    Y = lambda v: h - 3 - (v - mn) / rng * (h - 8)
    band = ""
    if lo is not None and hi is not None and hi > lo:
        band = '<rect x="0" y="%.1f" width="%d" height="%.1f" fill="#EEF0F3"/>' % (
            Y(hi), w, max(1.0, Y(lo) - Y(hi)))
    d = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(i), Y(v)) for i, v in enumerate(pts))
    last = pts[-1]
    col = "#111827"
    if lo is not None and hi is not None:
        col = "#B42318" if last < lo else ("#067647" if last > hi else "#111827")
    return ('<svg class="spark" width="%d" height="%d" viewBox="0 0 %d %d">%s'
            '<path d="%s" fill="none" stroke="#111827" stroke-width="1.6"/>'
            '<circle cx="%.1f" cy="%.1f" r="3.4" fill="%s"/></svg>'
            % (w, h, w, h, band, d, X(n - 1), Y(last), col))


# ---------------------------------------------------------------- 1 · EXECUTIVE
def c_exec(A, win):
    s, p = A["summary"], A.get("prev_summary") or {}
    D = decompose({"spend": s.get("spend"), "rev": s.get("rev")},
                  {"spend": p.get("spend"), "rev": p.get("rev")})
    d_rev = pct(s.get("rev") or 0, p.get("rev") or 0)
    d_sp = pct(s.get("spend") or 0, p.get("spend") or 0)
    d_roas = pct(s.get("roas") or 0, p.get("roas") or 0)

    top = '<div class="grid g3">%s%s%s</div>' % (
        tile("Revenue", "%s EGP" % _k(s.get("rev") or 0), "%s EGP" % _k(p.get("rev") or 0), d_rev),
        tile("Spend", "%s EGP" % _k(s.get("spend") or 0), "%s EGP" % _k(p.get("spend") or 0), d_sp,
             neutral=True),
        tile("ROAS", "%.2fx" % r2(s.get("roas") or 0), "%.2fx" % r2(p.get("roas") or 0), d_roas))
    if D:
        ok = D["efficient"]
        top += ('<div class="callout %s"><b>%s</b>&nbsp;%s</div>'
                % ("c-g" if ok else "c-r",
                   "EFFICIENCY IMPROVED." if ok else "EFFICIENCY GOT WORSE.",
                   esc(az.verdict_line(D))))

    MET = [("Spend", "spend", "%s", False, True), ("Revenue", "rev", "%s", False, False),
           ("ROAS", "roas", "%.2fx", False, False), ("CPP", "cpa", "%s", True, False),
           ("AOV", "aov", "%s", False, False), ("CVR", "cvr", "%.2f%%", False, False),
           ("CPMR", "cpmr", "%s", True, False), ("Out CTR", "octr", "%.2f%%", False, False)]
    tiles = ""
    for lab, k, f, lb, nu in MET:
        a_, b_ = s.get(k) or 0, p.get(k) or 0
        fv = (lambda v: _k(v)) if f == "%s" else (lambda v: f % r2(v))
        tiles += tile(lab, fv(a_), fv(b_), pct(a_, b_), lower_better=lb, neutral=nu)
    matrix = ('<div class="card"><div class="ch"><div class="ic">#</div><h2>The whole card</h2>'
              '<div class="note">every number, and what it was in the previous period</div></div>'
              '<div class="grid g4">%s</div></div>' % tiles)

    # WHY: budget or the account. Exact identity, no residual.
    why = ""
    if D:
        tot = abs(D["spend_eff"]) + abs(D["perf_eff"]) or 1
        wsp = abs(D["spend_eff"]) / tot * 100
        why = ('<div class="card"><div class="ch"><div class="ic">%%</div>'
               '<h2>Why revenue moved &nbsp;·&nbsp; the budget, or the account</h2>'
               '<div class="note">these two add to the revenue change exactly. there is no third thing</div></div>'
               '<div style="font-size:26px;font-weight:800;margin-bottom:12px" class="%s">REVENUE %s%s EGP</div>'
               '<table><tr><th class=l>Driver</th><th>What changed</th><th>Worth</th><th>Share of the move</th></tr>'
               '<tr><td class=l><span class="bar" style="background:#1D4ED8"></span>BECAUSE YOU CHANGED THE BUDGET'
               '<span class="s">spend %s → %s (%s)</span></td>'
               '<td class=b>%s</td><td class=b>%s%s EGP</td><td>%.0f%%</td></tr>'
               '<tr><td class=l><span class="bar" style="background:%s"></span>BECAUSE THE ACCOUNT CHANGED'
               '<span class="s">ROAS %.2fx → %.2fx</span></td>'
               '<td class="%s">%s</td><td class="%s">%s%s EGP</td><td>%.0f%%</td></tr></table>'
               '<div class="callout %s">%s</div></div>'
               % ("g" if D["d_rev"] >= 0 else "r",
                  "+" if D["d_rev"] >= 0 else "-", _k(abs(D["d_rev"])),
                  _k(p.get("spend") or 0), _k(s.get("spend") or 0),
                  ("%+.0f%%" % d_sp) if d_sp is not None else "n/a",
                  ("%+.0f%%" % d_sp) if d_sp is not None else "n/a",
                  "+" if D["spend_eff"] >= 0 else "-", _k(abs(D["spend_eff"])), wsp,
                  "#067647" if D["perf_eff"] >= 0 else "#B42318",
                  r2(D["roas0"]), r2(D["roas1"]),
                  "g" if D["perf_eff"] >= 0 else "r",
                  ("%+.0f%%" % d_roas) if d_roas is not None else "n/a",
                  "g" if D["perf_eff"] >= 0 else "r",
                  "+" if D["perf_eff"] >= 0 else "-", _k(abs(D["perf_eff"])), 100 - wsp,
                  "c-b" if D["driver"] == "SPEND" else ("c-g" if D["perf_eff"] >= 0 else "c-r"),
                  esc("The move is mostly the BUDGET you set. The account did not really change."
                      if D["driver"] == "SPEND"
                      else "The move is mostly the ACCOUNT. This is real performance, not money.")))

    # AUDIENCE, ON THE FIRST SCREEN. He asked for it here and he is right: an account-level
    # number with no segment split hides which third of the money actually moved.
    segs, sprev = A.get("segs") or {}, A.get("segs_prev") or {}
    t_sp = sum((segs.get(k) or {}).get("spend") or 0 for k in ("NEW", "ENGAGED", "EXISTING")) or 1
    t_rv = sum((segs.get(k) or {}).get("rev") or 0 for k in ("NEW", "ENGAGED", "EXISTING")) or 1
    rows = ""
    for kk in ("NEW", "ENGAGED", "EXISTING"):
        m, q = segs.get(kk) or {}, sprev.get(kk) or {}
        if not (m.get("spend") or 0) and not (q.get("spend") or 0): continue
        col = {"NEW": "#1D4ED8", "ENGAGED": "#B54708", "EXISTING": "#067647"}[kk]
        rows += ('<tr><td class=l><span class="bar" style="background:%s"></span>%s</td>'
                 '<td>%s<span class="s">%.0f%% of spend</span></td><td>%s</td>'
                 '<td>%s<span class="s">%.0f%% of revenue</span></td><td>%s</td>'
                 '<td>%.2fx<span class="s">was %.2fx</span></td><td>%s</td>'
                 '<td class="%s">%s%s EGP</td></tr>'
                 % (col, esc(SEGN[kk]),
                    _k(m.get("spend") or 0), (m.get("spend") or 0) / t_sp * 100,
                    pill(pct(m.get("spend") or 0, q.get("spend") or 0), neutral=True),
                    _k(m.get("rev") or 0), (m.get("rev") or 0) / t_rv * 100,
                    pill(pct(m.get("rev") or 0, q.get("rev") or 0)),
                    r2(m.get("roas") or 0), r2(q.get("roas") or 0),
                    pill(pct(m.get("roas") or 0, q.get("roas") or 0)),
                    "g" if (m.get("rev") or 0) - (q.get("rev") or 0) >= 0 else "r",
                    "+" if (m.get("rev") or 0) - (q.get("rev") or 0) >= 0 else "-",
                    _k(abs((m.get("rev") or 0) - (q.get("rev") or 0)))))
    aud = ('<div class="card"><div class="ch"><div class="ic">◐</div>'
           '<h2>Audience segments &nbsp;·&nbsp; spend and attributed revenue vs the previous period</h2>'
           '<div class="note">Meta\'s own breakdown</div></div>'
           '<table><tr><th class=l>Segment</th><th>Spend</th><th>vs prev</th><th>Revenue</th>'
           '<th>vs prev</th><th>ROAS</th><th>vs prev</th><th>Revenue moved</th></tr>%s</table></div>'
           % rows) if rows else ""

    body = ('<div class="card"><div class="ch"><div class="ic">▲</div><h2>The call</h2></div>%s</div>'
            '%s%s%s' % (top, matrix, why, aud))
    return page(A, win, "What happened",
                "Revenue is never read without spend, and never without the segment it came from.",
                body, 1560)


# ---------------------------------------------------------------- 2 · CAUSE
def c_cause(A, win, level="campaign"):
    B = attribution(A, level, n=40)
    if not B or not B["all"]: return None
    live = [r for r in B["all"] if (r["now"] or {}).get("spend") or abs(r["d_rev"]) >= 1]
    if not live: return None
    los = sorted([r for r in live if r["d_rev"] < 0], key=lambda r: r["d_rev"])[:4]
    gan = sorted([r for r in live if r["d_rev"] > 0], key=lambda r: -r["d_rev"])[:3]
    t_g = sum(r["d_rev"] for r in live if r["d_rev"] > 0)
    t_l = sum(r["d_rev"] for r in live if r["d_rev"] < 0)

    head = ('<div class="card"><div class="ch"><div class="ic">±</div><h2>Both sides of the move</h2>'
            '<div class="note">every %s, not just the ones shown</div></div>'
            '<div class="grid g3">%s%s%s</div></div>'
            % (level,
               tile("Net", "%s%s EGP" % ("+" if B["d_tot"] >= 0 else "-", _k(abs(B["d_tot"])))),
               tile("Pulled down", "%s EGP" % _k(t_l)),
               tile("Fought back", "+%s EGP" % _k(t_g))))

    COLS = [("Spend", "spend", "%s", True), ("Revenue", "rev", "%s", False),
            ("ROAS", "roas", "%.2fx", False), ("CPP", "cpa", "%s", False),
            ("AOV", "aov", "%s", False), ("CVR", "cvr", "%.2f%%", False),
            ("CPMR", "cpmr", "%s", False), ("Out CTR", "octr", "%.2f%%", False),
            ("Freq", "freq", "%.2f", False)]
    LOWER = {"cpa", "cpmr", "cpm"}

    def block(rows, title, cls, note):
        if not rows: return ""
        th = "".join('<th>%s</th>' % c[0] for c in COLS)
        tr = ""
        for r in rows:
            m, q = r["now"] or {}, r["pre"] or {}
            D = r.get("dec")
            col = "#067647" if r["d_rev"] >= 0 else "#B42318"
            cells = ""
            for lab, k, f, nu in COLS:
                a_, b_ = m.get(k) or 0, q.get(k) or 0
                v = _k(a_) if f == "%s" else (f % r2(a_))
                cells += ('<td>%s<span class="s">was %s</span><div style="margin-top:3px">%s</div></td>'
                          % (v, (_k(b_) if f == "%s" else (f % r2(b_))),
                             pill(pct(a_, b_), lower_better=(k in LOWER), neutral=nu)))
            dec = ""
            if D:
                dec = ('<span class="s">budget %s%s &nbsp;·&nbsp; performance %s%s &nbsp;—&nbsp; %s</span>'
                       % ("+" if D["spend_eff"] >= 0 else "-", _k(abs(D["spend_eff"])),
                          "+" if D["perf_eff"] >= 0 else "-", _k(abs(D["perf_eff"])),
                          "mostly the budget you set" if D["driver"] == "SPEND"
                          else ("real performance, the account got better" if D["d_rev"] >= 0
                                else "a real performance change, not a budget change")))
            cause = ('<span class="tag %s">%s</span>' % ("am", esc(r["dx"][0]))) if r.get("dx") else ""
            tr += ('<tr><td class=l style="width:270px"><span class="bar" style="background:%s"></span>%s'
                   '<span class="s" style="color:%s;font-weight:800">%s%s EGP &nbsp;·&nbsp; %+.0f%% of all movement</span>'
                   '%s%s</td>%s</tr>'
                   % (col, esc(_clip(safe(r["name"]), 34)), col,
                      "+" if r["d_rev"] >= 0 else "-", _k(abs(r["d_rev"])), r["share"],
                      cause, dec, cells))
        return ('<div class="card"><div class="ch"><div class="ic">%s</div><h2>%s</h2>'
                '<div class="note">%s</div></div>'
                '<table><tr><th class=l>%s</th>%s</tr>%s</table></div>'
                % (cls, esc(title), esc(note), level.title(), th, tr))

    body = head
    body += block(los, "What pulled it down", "▼",
                  "every metric against the same entity's own previous period")
    body += block(gan, "What fought back", "▲", "do not cut these by accident")
    return page(A, win, "What caused it",
                "Each one split into the budget you changed and the performance that changed, "
                "and every metric carries its own previous period.", body, 1680)


# ---------------------------------------------------------------- 3 · WINNERS
def c_winners(A, win):
    P = proposals(A, win)
    if not P: return None
    acc = A.get("b7_acc") or A["summary"]
    # CREATIVE winners first. Catalogue is a budget decision, not a creative one, and it gets
    # its own block so it can never be mistaken for something worth reshooting.
    scale = [r for r in P["scale"] if not r["cat"]][:4]
    cat = [r for r in P["scale"] if r["cat"]][:3]
    cut = P["cut"][:4]
    if not scale and not cut and not cat: return None

    def rows(rs):
        tr = ""
        for r in rs:
            k = r["k"]; lab = r["label"]
            col = {"SCALE": "#067647", "HEADROOM": "#067647", "SATURATED": "#B54708",
                   "CUT": "#B42318", "MONITOR": "#1D4ED8"}.get(lab, "#6B7280")
            dw = daywk(A, r["ad_id"])
            an = ""
            if dw:
                cl = {"NORMAL": "nu", "BELOW BAND": "dn", "ABOVE BAND": "up"}[dw["state"]]
                an = ('<span class="pill %s">TODAY %s</span><span class="s">rev %s vs %s/day avg (%s)</span>'
                      % (cl, dw["state"], _k(dw["rev_now"]), _k(dw["rev_avg"]),
                         ("%+.0f%%" % dw["rev_d"]) if dw["rev_d"] is not None else "n/a"))
            tests = " &nbsp;·&nbsp; ".join(
                "%s %s (%s)" % ("PASS" if t["ok"] else "FAIL", esc(t["t"]), esc(t["v"]))
                for t in r["tests"][:3])
            tr += ('<tr><td class=l style="width:250px"><span class="bar" style="background:%s"></span>%s'
                   '<span class="s"><b style="color:%s">%s</b> &nbsp;·&nbsp; %s &nbsp;·&nbsp; confidence %s</span></td>'
                   '<td>%s<span class="s">spend</span></td><td>%.2fx<span class="s">acct %.2fx</span></td>'
                   '<td>%s<span class="s">CPP</span></td><td>%s<span class="s">AOV</span></td>'
                   '<td>%.2f%%<span class="s">CVR</span></td><td>%.2f%%<span class="s">CTR-O</span></td>'
                   '<td>%.2f<span class="s">freq</span></td><td>%d<span class="s">purchases</span></td>'
                   '<td style="width:210px">%s</td></tr>'
                   '<tr><td colspan=10 style="border:0;padding:0 0 8px 13px"><span class="s">%s</span></td></tr>'
                   % (col, esc(_clip(r["name"], 32)), col, lab, esc(r["kind"]), r["conf"],
                      _k(k.get("spend") or 0), r2(k.get("roas") or 0), r2(acc.get("roas") or 0),
                      _k(k.get("cpa") or 0), _k(k.get("aov") or 0), r2(k.get("cvr") or 0),
                      r2(k.get("octr") or 0), r2(k.get("freq") or 0), int(k.get("purch") or 0),
                      an, tests))
        return tr

    def blk(title, ic, note, rs):
        if not rs: return ""
        return ('<div class="card"><div class="ch"><div class="ic">%s</div><h2>%s</h2>'
                '<div class="note">%s</div></div><table>%s</table></div>'
                % (ic, esc(title), esc(note), rows(rs)))

    crit = ""
    for lab in ("SCALE", "HEADROOM", "SATURATED", "CUT"):
        col = {"SCALE": "g", "HEADROOM": "g", "SATURATED": "a", "CUT": "r"}[lab]
        crit += ('<div class="tile"><div class="lb %s" style="font-size:12px">%s</div>%s</div>'
                 % (col, lab, "".join('<div class="was" style="color:#4B5563;margin-top:5px">· %s</div>'
                                      % esc(c) for c in CRIT[lab])))
    rules = ('<div class="card"><div class="ch"><div class="ic">§</div>'
             '<h2>The criteria &nbsp;·&nbsp; this is the whole rulebook</h2></div>'
             '<div class="grid g4">%s</div>'
             '<div class="callout c-b">An ad must pass EVERY line of a label to get it. '
             'SATURATED needs 3 of 4 plus frequency over 4.0. '
             'CATALOGUE and DPA are never counted as creative winners.</div></div>' % crit)

    body = (blk("Working · creative", "▲",
                "shot creative only. catalogue is separated out below", scale)
            + blk("Working · catalogue and DPA", "▦",
                  "these are a feed, not a shoot. scale the budget, do not brief a video off them", cat)
            + blk("Not working · cut or saturated", "▼",
                  "with today measured against the ad's own last 7 days", cut)
            + rules)
    return page(A, win, "What is working, and what stopped",
                "Every label has printed criteria, this ad's actual value against each one, and "
                "whether today is normal for that ad or an anomaly.", body, 1760)


# ---------------------------------------------------------------- 4 · MONEY
def c_money(A, win):
    P = proposals(A, win)
    if not P or (not P["scale"] and not P["cut"]): return None
    board = '<div class="grid g3">%s%s%s</div>' % (
        tile("Spend change", "%s%s / day" % ("+" if P["net_spend"] >= 0 else "-",
                                             _k(abs(P["net_spend"])))),
        tile("Expected revenue", "%s%s / day" % ("+" if P["net_rev"] >= 0 else "-",
                                                 _k(abs(P["net_rev"])))),
        tile("Account ROAS", "%.2fx" % r2(P["acc_roas"])))
    board = ('<div class="card"><div class="ch"><div class="ic">₤</div>'
             '<h2>The whole board, per day</h2></div>%s'
             '<div class="callout c-b">Add %s/day to the winners, take %s/day off the losers. '
             'Every ad is priced at ITS OWN 7 day ROAS, never the account average.</div></div>'
             % (board, _k(P["added"]), _k(P["freed"])))

    def put(rs):
        tr = ""
        for i, r in enumerate(rs, 1):
            why = " &nbsp;·&nbsp; ".join(esc(t["t"]) for t in r["tests"] if t["ok"])
            tr += ('<tr><td class=l style="width:300px"><span class="bar" style="background:#067647"></span>%s'
                   '<span class="s">%s &nbsp;·&nbsp; %s &nbsp;·&nbsp; confidence %s &nbsp;·&nbsp; ROAS %.2fx '
                   '&nbsp;·&nbsp; freq %.2f</span></td>'
                   '<td>%s<span class="s">now / day</span></td>'
                   '<td class=b>%s<span class="s">go to / day</span></td>'
                   '<td class=b>+%s<span class="s">extra spend / day</span></td>'
                   '<td class=g>+%s<span class="s">expected revenue / day</span></td>'
                   '<td class=g>+%s<span class="s">better than the account average</span></td></tr>'
                   '<tr><td colspan=6 style="border:0;padding:0 0 8px 13px"><span class="s">WHY: %s</span></td></tr>'
                   % (esc(_clip(r["name"], 34)), r["label"], esc(r["kind"]), r["conf"],
                      r2(r["roas"]), r2(r["freq"]),
                      _k(r["sp_day"]), _k(r["new_day"]), _k(r["add_day"]),
                      _k(r["inc_rev"]), _k(max(0, r["inc_vs_acct"])), why))
        return tr

    def take(rs):
        tr = ""
        for r in rs:
            why = " &nbsp;·&nbsp; ".join(esc(t["t"]) for t in r["tests"] if t["ok"])
            tr += ('<tr><td class=l style="width:300px"><span class="bar" style="background:#B42318"></span>%s'
                   '<span class="s">%s &nbsp;·&nbsp; %s &nbsp;·&nbsp; confidence %s &nbsp;·&nbsp; ROAS %.2fx '
                   '&nbsp;·&nbsp; freq %.2f</span></td>'
                   '<td>%s<span class="s">now / day</span></td>'
                   '<td class=r>%s<span class="s">go to / day</span></td>'
                   '<td class=r>-%s<span class="s">spend freed / day</span></td>'
                   '<td class=r>-%s<span class="s">revenue given up / day</span></td>'
                   '<td class=g>+%s<span class="s">net, redeployed at %.2fx</span></td></tr>'
                   '<tr><td colspan=6 style="border:0;padding:0 0 8px 13px"><span class="s">WHY: %s</span></td></tr>'
                   % (esc(_clip(r["name"], 34)), r["label"], esc(r["kind"]), r["conf"],
                      r2(r["roas"]), r2(r["freq"]),
                      _k(r["sp_day"]), _k(r["new_day"]), _k(r["cut_day"]), _k(r["lost_rev"]),
                      _k(max(0, r["net"])), r2(P["acc_roas"]), why))
        return tr

    body = board
    if P["scale"]:
        body += ('<div class="card"><div class="ch"><div class="ic">↑</div><h2>Put money in</h2>'
                 '<div class="note">an investment proposal, priced in EGP per day</div></div>'
                 '<table>%s</table></div>' % put(P["scale"][:4]))
    if P["cut"]:
        body += ('<div class="card"><div class="ch"><div class="ic">↓</div><h2>Take money out</h2>'
                 '<div class="note">what you give up, and what the freed money buys elsewhere</div></div>'
                 '<table>%s</table></div>' % take(P["cut"][:4]))
    return page(A, win, "What to do with the money",
                "Current budget, recommended budget, the extra spend, and what that spend buys "
                "at that ad's own ROAS.", body, 1500)


# ---------------------------------------------------------------- 5 · AUDIENCE
def c_audience(A, win):
    segs, sprev = A.get("segs") or {}, A.get("segs_prev") or {}
    keys = [k for k in ("NEW", "ENGAGED", "EXISTING") if (segs.get(k) or {}).get("spend")]
    if not keys: return None
    t_sp = sum((segs.get(k) or {}).get("spend") or 0 for k in keys) or 1
    t_rv = sum((segs.get(k) or {}).get("rev") or 0 for k in keys) or 1
    gross = sum(abs(((segs.get(k) or {}).get("rev") or 0) - ((sprev.get(k) or {}).get("rev") or 0))
                for k in keys) or 1
    COL = {"NEW": "#1D4ED8", "ENGAGED": "#B54708", "EXISTING": "#067647"}

    cards = ""
    for k in keys:
        m, q = segs.get(k) or {}, sprev.get(k) or {}
        D = decompose({"spend": m.get("spend"), "rev": m.get("rev")},
                      {"spend": q.get("spend"), "rev": q.get("rev")})
        dr = (m.get("rev") or 0) - (q.get("rev") or 0)
        MET = [("Spend", _k(m.get("spend") or 0), _k(q.get("spend") or 0),
                pct(m.get("spend") or 0, q.get("spend") or 0), False, True),
               ("Revenue", _k(m.get("rev") or 0), _k(q.get("rev") or 0),
                pct(m.get("rev") or 0, q.get("rev") or 0), False, False),
               ("ROAS", "%.2fx" % r2(m.get("roas") or 0), "%.2fx" % r2(q.get("roas") or 0),
                pct(m.get("roas") or 0, q.get("roas") or 0), False, False),
               ("CPP", _k(m.get("cpa") or 0), _k(q.get("cpa") or 0),
                pct(m.get("cpa") or 0, q.get("cpa") or 0), True, False),
               ("AOV", _k(m.get("aov") or 0), _k(q.get("aov") or 0),
                pct(m.get("aov") or 0, q.get("aov") or 0), False, False),
               ("CVR", "%.2f%%" % r2(m.get("cvr") or 0), "%.2f%%" % r2(q.get("cvr") or 0),
                pct(m.get("cvr") or 0, q.get("cvr") or 0), False, False),
               ("CPMR", _k(m.get("cpmr") or 0), _k(q.get("cpmr") or 0),
                pct(m.get("cpmr") or 0, q.get("cpmr") or 0), True, False),
               ("Frequency", "%.2f" % r2(m.get("freq") or 0), "%.2f" % r2(q.get("freq") or 0),
                pct(m.get("freq") or 0, q.get("freq") or 0), True, False)]
        tiles = "".join(tile(a, b, c, d, lower_better=e, neutral=f) for a, b, c, d, e, f in MET)
        dec = ""
        if D:
            dec = ('<div class="callout %s">Revenue moved <b>%s%s EGP</b>, which is <b>%.0f%%</b> of '
                   'all segment movement. Budget %s%s &nbsp;·&nbsp; performance %s%s.</div>'
                   % ("c-g" if dr >= 0 else "c-r", "+" if dr >= 0 else "-", _k(abs(dr)),
                      abs(dr) / gross * 100,
                      "+" if D["spend_eff"] >= 0 else "-", _k(abs(D["spend_eff"])),
                      "+" if D["perf_eff"] >= 0 else "-", _k(abs(D["perf_eff"]))))
        cards += ('<div class="card"><div class="ch">'
                  '<div class="ic" style="background:%s22;color:%s">●</div><h2>%s</h2>'
                  '<div class="note">%.0f%% of spend &nbsp;·&nbsp; %.0f%% of revenue</div></div>'
                  '<div class="grid g4">%s</div>%s</div>'
                  % (COL[k], COL[k], esc(SEGN[k]),
                     (m.get("spend") or 0) / t_sp * 100, (m.get("rev") or 0) / t_rv * 100,
                     tiles, dec))

    # the budget shift, in EGP a day, not in percentage points
    a_roas = A["summary"].get("roas") or 0
    best = max(keys, key=lambda k: (segs[k].get("roas") or 0))
    worst = min(keys, key=lambda k: (segs[k].get("roas") or 0))
    move = ""
    if best != worst:
        nd = {"daily": 1.0, "3day": 3.0, "7day": 7.0}.get(win, 1.0)
        shift = (segs[worst].get("spend") or 0) * 0.20 / nd
        gain = shift * ((segs[best].get("roas") or 0) - (segs[worst].get("roas") or 0))
        move = ('<div class="card"><div class="ch"><div class="ic">⇄</div>'
                '<h2>The budget move &nbsp;·&nbsp; in EGP per day, not in percentage points</h2></div>'
                '<div class="grid g3">%s%s%s</div>'
                '<div class="callout c-g">%s earns more budget: it returns <b>%.2fx</b> against the '
                'account\'s %.2fx at frequency %.2f, so it still has room. %s gives budget up: '
                '<b>%.2fx</b> at frequency %.2f.</div></div>'
                % (tile("Move", "%s / day" % _k(shift)),
                   tile("From", "%s (%.2fx)" % (SEGN[worst], r2(segs[worst].get("roas") or 0))),
                   tile("To", "%s (%.2fx)" % (SEGN[best], r2(segs[best].get("roas") or 0))),
                   esc(SEGN[best]), r2(segs[best].get("roas") or 0), r2(a_roas),
                   r2(segs[best].get("freq") or 0), esc(SEGN[worst]),
                   r2(segs[worst].get("roas") or 0), r2(segs[worst].get("freq") or 0)))
        move += ('<div class="callout c-b" style="margin:0">Expected <b>+%s revenue / day</b> at the '
                 'SAME total budget.</div>' % _k(max(0, gain)))
    return page(A, win, "Where the money is",
                "Meta's own segments. Spend share, revenue share, every metric against the "
                "previous period, and exactly how much to shift.",
                cards + move, 1620)


# ---------------------------------------------------------------- 6 · FATIGUE
def c_fatigue(A, win):
    F = fatigue_scan(A)
    if not F: return None
    rows = F[:8]
    n_f = sum(1 for r in F if r["state"] == "FATIGUED")
    n_g = sum(1 for r in F if r["state"] == "FATIGUING")
    burn = sum(r["spend"] for r in F if r["state"] in ("FATIGUED", "FATIGUING")) / 7.0
    vids = [r for r in F if r["k"].get("type") == "VIDEO" and r["hook"]]
    vh = az.med([r["hook"] for r in vids]) if vids else None

    top = ('<div class="card"><div class="ch"><div class="ic">◔</div>'
           '<h2>The state of the creative</h2>'
           '<div class="note">of %d ads with real money behind them</div></div>'
           '<div class="grid g3">%s%s%s</div>'
           '<div class="callout c-a">Hook rate is 3 second views over impressions and only means '
           'anything on VIDEO. The account\'s video ads run at %s. Catalogue and image ads show n/a.</div></div>'
           % (len(F),
              tile("Fatigued", "%d" % n_f), tile("Fatiguing", "%d" % n_g),
              tile("Behind tiring creative", "%s EGP / day" % _k(burn)),
              ("%.1f%%" % vh) if vh else "not enough video"))

    tr = ""
    for r in rows:
        col = {"FATIGUED": "#B42318", "FATIGUING": "#B54708",
               "FRESH": "#067647", "NO PRIOR": "#9CA3AF"}[r["state"]]
        k = r["k"]
        vid = k.get("type") == "VIDEO"
        dw = daywk(A, r["ad_id"])
        # THE WHOLE POINT: is this a week-long slide, or just a bad Tuesday.
        if dw:
            cl = {"NORMAL": "nu", "BELOW BAND": "dn", "ABOVE BAND": "up"}[dw["state"]]
            today = ('<span class="pill %s">TODAY %s</span>'
                     '<span class="s">ROAS %.2fx vs %.2fx its 7d avg (%s)</span>'
                     '<span class="s">rev %s vs %s/day</span>'
                     % (cl, dw["state"], r2(dw["roas_now"]), r2(dw["roas_avg"]),
                        ("%+.0f%%" % dw["roas_d"]) if dw["roas_d"] is not None else "n/a",
                        _k(dw["rev_now"]), _k(dw["rev_avg"])))
            sp = spark(dw["series"], "rev", 140, 34, dw["lo"], dw["hi"])
        else:
            today, sp = '<span class="pill nu">no daily history</span>', ""
        wk = ('<span class="s">frq <b>%.2f</b> &nbsp; hook %s &nbsp; hold %s</span>'
              % (r["freq"],
                 ("<b>%.1f%%</b>" % r["hook"]) if vid else "n/a",
                 ("%.0f%%" % r["hold"]) if vid else "n/a"))
        cells = ""
        for lab, v, lb in (("HOOK Δ", r["d_hook"] if vid else None, False),
                           ("CTR-O Δ", r["d_octr"], False),
                           ("CPM Δ", r["d_cpm"], True),
                           ("CPP Δ", r["d_cpp"], True)):
            cells += '<td>%s<span class="s">%s</span></td>' % (pill(v, lower_better=lb), lab)
        tr += ('<tr><td class=l style="width:230px"><span class="bar" style="background:%s"></span>%s'
               '<span class="s"><b style="color:%s">%s</b> · %d of 5 tests fired · %s</span>%s</td>'
               '<td>%s<span class="s">spend 7d</span></td><td>%.2fx<span class="s">ROAS 7d</span></td>'
               '%s<td style="width:230px">%s</td><td style="width:150px">%s<span class="s">14 days, its own band</span></td></tr>'
               % (col, esc(_clip(r["name"], 28)), col, r["state"], r["hits"],
                  esc("CATALOGUE" if is_catalogue(k) else (k.get("type") or "")), wk,
                  _k(r["spend"]), r2(r["roas"]), cells, today, sp))

    table = ('<div class="card"><div class="ch"><div class="ic">≡</div>'
             '<h2>Every ad with real money behind it</h2>'
             '<div class="note">Δ = THE WEEK: this ad\'s last 7 days vs the 7 before &nbsp;·&nbsp; '
             'TODAY = that same ad against its own last 7 days</div></div>'
             '<table>%s</table>'
             '<div class="callout c-b">Read the two together. A red weekly Δ with a NORMAL today is '
             'a real slide. A green week with a BELOW BAND today is one bad day, not a dying ad, '
             'and killing it would be a mistake.</div></div>' % tr)

    crit = ""
    for i, (c, why) in enumerate(FATG_CRIT, 1):
        crit += ('<tr><td class=l style="width:40px">%d.</td><td class=l>%s</td>'
                 '<td class=l style="font-weight:600;color:#6B7280">%s</td></tr>' % (i, esc(c), esc(why)))
    rules = ('<div class="card"><div class="ch"><div class="ic">§</div>'
             '<h2>The five fatigue tests &nbsp;·&nbsp; this is the whole rulebook</h2></div>'
             '<table>%s</table>'
             '<div class="callout c-r"><b>FATIGUED</b> = 3 or more of the five fire AND frequency is '
             'over %.1f. &nbsp;<b>FATIGUING</b> = 2 fire. &nbsp;<b>FRESH</b> = 1 or none. '
             'An ad with no previous period cannot be judged for fatigue at all.</div></div>'
             % (crit, FATG["freq"]))
    return page(A, win, "Creative fatigue and hook rate",
                "The week tells you if it is decaying. Today tells you if it is an anomaly. "
                "Both are on every row.", top + table + rules, 1760)


# ---------------------------------------------------------------- 7 · MAKE MORE
def c_makemore(A, win):
    P = winning_pattern(A)
    if not P: return None
    HR = hit_rate(A)
    Wn, Ls = P["W"], P["L"]
    top = ""
    if HR:
        top = ('<div class="card"><div class="ch"><div class="ic">✦</div>'
               '<h2>How many creatives to launch</h2>'
               '<div class="note">catalogue and DPA excluded. this is a creative hit rate</div></div>'
               '<div class="grid g4">%s%s%s%s</div>'
               '<div class="callout c-b">Winner here means it clears the full SCALE bar, which is '
               'stricter than simply beating the account. At this rate, shooting fewer than %d new '
               'creatives is not a plan, it is a hope.</div></div>'
               % (tile("Hit rate", "%.0f%%" % HR["hr"]),
                  tile("Winners", "%d of %d" % (HR["winners"], HR["tested"])),
                  tile("Launch to get 1", "%d" % HR["need_1"]),
                  tile("Launch to get 3", "%d" % HR["need_3"]), HR["need_1"]))

    MET = [("Hook rate", "hook", "%.1f%%"), ("Hold", "hold", "%.0f%%"),
           ("Watched 50%", "r50", "%.0f%%"), ("Outbound CTR", "octr", "%.2f%%"),
           ("CVR", "cvr", "%.2f%%"), ("Frequency", "freq", "%.2f"),
           ("ROAS", "roas", "%.2fx"), ("AOV", "aov", "%s")]
    tiles = ""
    for lab, k, f in MET:
        w_, l_ = Wn.get(k), Ls.get(k)
        sw = (_k(w_) if f == "%s" else (f % w_)) if w_ else "n/a"
        sl = (_k(l_) if f == "%s" else (f % l_)) if l_ else "n/a"
        good = w_ is not None and l_ is not None and (w_ > l_ if k != "freq" else w_ < l_)
        tiles += ('<div class="tile"><div class="lb">%s</div><div class="rowv">'
                  '<div class="v %s">%s</div></div><div class="was">losers %s</div></div>'
                  % (esc(lab), "g" if good else "", esc(sw), esc(sl)))
    pat = ('<div class="card"><div class="ch"><div class="ic">◎</div>'
           '<h2>What the winners have in common</h2>'
           '<div class="note">winners vs the shot creative that lost to the account</div></div>'
           '<div class="grid g4">%s</div>'
           '<div class="callout c-b">Winners are the %d creative ads that beat the account\'s %.2fx on '
           'at least 3 purchases. Losers are the %d that did not. Same week, same account, same offer. '
           'No catalogue on either side.</div></div>'
           % (tiles, P["n_win"], r2(P["acc_roas"]), len(P["losers"])))

    daily = A.get("ad_daily") or {}
    tr = ""
    for k in P["top"][:3]:
        dw = daywk(A, k.get("ad_id"))
        sp = spark(daily.get(str(k.get("ad_id"))) or [], "rev", 190, 40,
                   dw["lo"] if dw else None, dw["hi"] if dw else None)
        st = ""
        if dw:
            cl = {"NORMAL": "nu", "BELOW BAND": "dn", "ABOVE BAND": "up"}[dw["state"]]
            st = '<span class="pill %s">TODAY %s</span>' % (cl, dw["state"])
        tr += ('<tr><td class=l style="width:280px"><span class="bar" style="background:#067647"></span>%s'
               '<span class="s">%s</span></td>'
               '<td>%s<span class="s">spend</span></td><td>%s<span class="s">revenue</span></td>'
               '<td>%.2fx<span class="s">ROAS</span></td><td>%.1f%%<span class="s">hook</span></td>'
               '<td>%.0f%%<span class="s">hold</span></td><td>%.2f<span class="s">freq</span></td>'
               '<td>%d<span class="s">purchases</span></td>'
               '<td style="width:120px">%s</td><td style="width:200px">%s</td></tr>'
               % (esc(_clip(safe(k.get("ad_name") or ""), 34)), esc(k.get("type") or ""),
                  _k(k.get("spend") or 0), _k(k.get("rev") or 0), r2(k.get("roas") or 0),
                  k.get("hook") or 0, k.get("hold") or 0, r2(k.get("freq") or 0),
                  int(k.get("purch") or 0), st, sp))
    tops = ('<div class="card"><div class="ch"><div class="ic">★</div>'
            '<h2>Model the next shoot on these</h2>'
            '<div class="note">with their own 14 day history, so it is a pattern and not one lucky day</div>'
            '</div><table>%s</table></div>' % tr)

    lis = "".join('<div class="callout c-g" style="margin-top:8px">%s</div>' % esc(l)
                  for l in brief_lines(P)[:5])
    brief = ('<div class="card"><div class="ch"><div class="ic">✎</div>'
             '<h2>The brief &nbsp;·&nbsp; give this to the editor</h2></div>%s</div>' % lis)
    return page(A, win, "Make more of what worked",
                "How many creatives you must launch, what the winners actually share, and the brief.",
                top + pat + tops + brief, 1620)


# ---------------------------------------------------------------- driver
CARDS = (("1-executive", c_exec), ("2-cause", c_cause), ("3-winners", c_winners),
         ("4-money", c_money), ("5-audience", c_audience), ("6-fatigue", c_fatigue),
         ("7-makemore", c_makemore))


def available():
    return _chrome() is not None


def render(A, win):
    out = []
    for suf, fn in CARDS:
        try:
            r = fn(A, win)
            if not r: continue
            html, h = r
            png = shoot(html, h)
            if png: out.append((suf, png))
        except Exception as e:
            import sys, traceback
            sys.stderr.write("[html] %s failed: %s\n%s\n" % (suf, e, traceback.format_exc()))
    return out


def dump(A, win, d):
    """Write the raw HTML so it can be opened in a real browser and looked at."""
    os.makedirs(d, exist_ok=True)
    for suf, fn in CARDS:
        r = fn(A, win)
        if not r: continue
        with open(os.path.join(d, "%s.html" % suf), "w") as fh: fh.write(r[0])
