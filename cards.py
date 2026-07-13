"""The cards, rendered as HTML and shot with headless Chrome.

Matplotlib was the wrong tool. Every layout bug in this project came from placing text at an
absolute coordinate and hoping it did not collide with the text next to it. HTML does not have
that problem: a box is as tall as its contents, and two boxes cannot overlap. So the cards are
HTML now, and Chrome takes the picture.

If Chrome is not on the machine, analyze.py falls back to the old matplotlib cards, so this can
never take the report down.
"""
import os, sys, shutil, subprocess, tempfile, html as _html

# BIND TO THE RUNNING MODULE, NOT A SECOND COPY OF IT.
# analyze.py executes as __main__. `import analyze` therefore imports a SECOND, separate module
# object with its own globals, so DATES was empty and every date chip and previous-period number
# came out blank. Reach for the module that is actually running.
_m = sys.modules.get("__main__")
az = _m if hasattr(_m, "DATES") and hasattr(_m, "decompose") else __import__("analyze")

_k, r2, pct, safe = az._k, az.r2, az.pct, az.safe
_clip, pctile, _fmt_range = az._clip, az.pctile, az._fmt_range
decompose, attribution, proposals = az.decompose, az.attribution, az.proposals
fatigue_scan, winning_pattern, hit_rate = az.fatigue_scan, az.winning_pattern, az.hit_rate
brief_lines, daywk, is_catalogue = az.brief_lines, az.daywk, az.is_catalogue
CRIT, FATG, FATG_CRIT = az.CRIT, az.FATG, az.FATG_CRIT
SEGN, WIN_TITLE, WIN_FOOT = az.SEGN, az.WIN_TITLE, az.WIN_FOOT

W = 1760                      # wider canvas. Truncated names were a width problem, not a data problem.
FT = 1.30                     # every font size below is already scaled. Nothing shrinks.


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
body{width:%dpx;background:#FFFFFF;
     font-family:'DejaVu Sans','Segoe UI',Helvetica,Arial,sans-serif;
     color:#0F172A;-webkit-font-smoothing:antialiased}
.pg{padding:34px 38px 26px}

/* ---------- header ---------- */
.hd{display:flex;align-items:center;gap:0;font-size:15px;color:#64748B;
    letter-spacing:.08em;text-transform:uppercase;font-weight:800;margin-bottom:14px}
.hd .sep{color:#E2E8F0;margin:0 22px;font-weight:400}
.hd .dates{margin-left:auto;display:flex;align-items:center;gap:18px;
           letter-spacing:.02em;font-weight:800;color:#0F172A}
.hd .vs{color:#94A3B8;font-weight:700}
h1{font-size:46px;font-weight:800;letter-spacing:-1.2px;margin:0 0 8px;color:#0F172A}
.sub{font-size:19px;color:#64748B;margin-bottom:26px;font-weight:500}

/* ---------- section card ---------- */
.card{background:#FFFFFF;border:1px solid #E9EDF2;border-radius:20px;
      padding:26px 28px 28px;margin-bottom:22px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.ch{display:flex;align-items:center;gap:16px;margin-bottom:22px}
.ch .ic{width:46px;height:46px;border-radius:50%%;background:#1E293B;color:#fff;
        display:flex;align-items:center;justify-content:center;flex:0 0 46px}
.ch .ic svg{width:22px;height:22px}
.ch h2{font-size:26px;font-weight:800;letter-spacing:-.4px;color:#0F172A}
.ch .note{margin-left:auto;font-size:15px;color:#94A3B8;font-weight:600}

/* ---------- KPI cards ---------- */
.grid{display:grid;gap:16px}
.g4{grid-template-columns:repeat(4,1fr)}
.g3{grid-template-columns:repeat(3,1fr)}
.g2{grid-template-columns:repeat(2,1fr)}

.kpi{border:1px solid #E9EDF2;border-radius:16px;padding:20px 22px 18px;background:#fff}
.kpi.tint-g{background:#F7FDF9;border-color:#D6F0E0}
.kpi.tint-b{background:#F8FAFF;border-color:#DCE6FB}
.kpi.tint-p{background:#FAF9FF;border-color:#E4E0FB}
.kh{display:flex;align-items:center;gap:11px;margin-bottom:14px}
.kh .gi{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;
        justify-content:center;flex:0 0 34px}
.kh .gi svg{width:17px;height:17px}
.kh .lb{font-size:15px;font-weight:800;letter-spacing:.06em;color:#475569;text-transform:uppercase}
.kv{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.kv .v{font-size:42px;font-weight:800;letter-spacing:-1.4px;line-height:1;color:#0F172A}
.kv .vs{font-size:16px;color:#64748B;font-weight:600}
.prev{font-size:16px;color:#64748B;margin-top:12px;font-weight:600}

/* metric-matrix tile: same language, a size down */
.tile{border:1px solid #E9EDF2;border-radius:14px;padding:16px 18px;background:#fff}
.tile .kh{margin-bottom:10px}
.tile .kh .gi{width:30px;height:30px;flex:0 0 30px;border-radius:9px}
.tile .kh .gi svg{width:15px;height:15px}
.tile .kh .lb{font-size:14px}
.tile .kv .v{font-size:32px;letter-spacing:-1px}
.tile .prev{margin-top:8px;font-size:15px}

/* ---------- pills ---------- */
.pill{font-size:15px;font-weight:800;padding:5px 11px;border-radius:8px;white-space:nowrap;
      display:inline-flex;align-items:center;gap:4px}
.up{background:#E7F8EE;color:#0F7A43}
.dn{background:#FDECEC;color:#C0392B}
.nu{background:#F1F5F9;color:#475569}
.bl{background:#E8F0FE;color:#1D4ED8}
.am{background:#FEF6E7;color:#B45309}
.st{font-size:15px;font-weight:800;padding:6px 14px;border-radius:9px;letter-spacing:.05em;
    display:inline-block;text-transform:uppercase}
.st-scale,.st-headroom,.st-fresh{background:#0F7A43;color:#fff}
.st-cut,.st-fatigued{background:#C0392B;color:#fff}
.st-saturated,.st-fatiguing{background:#B45309;color:#fff}
.st-monitor,.st-no-prior{background:#475569;color:#fff}

/* ---------- callouts ---------- */
.callout{display:flex;gap:14px;align-items:center;border-radius:14px;padding:18px 20px;
         font-size:17px;line-height:1.5;margin-top:20px;font-weight:600}
.callout .ci{width:28px;height:28px;border-radius:50%%;display:flex;align-items:center;
             justify-content:center;flex:0 0 28px}
.callout .ci svg{width:15px;height:15px}
.c-g{background:#F3FBF6;color:#166534;border:1px solid #D6F0E0}
.c-g .ci{background:#0F7A43;color:#fff}
.c-r{background:#FDF3F2;color:#B91C1C;border:1px solid #FBD5D1}
.c-r .ci{background:#C0392B;color:#fff}
.c-b{background:#F5F8FF;color:#1D4ED8;border:1px solid #DCE6FB}
.c-b .ci{background:#2563EB;color:#fff}
.c-a{background:#FFFBF2;color:#B45309;border:1px solid #FCE9C4}
.c-a .ci{background:#D97706;color:#fff}

/* ---------- attribution ---------- */
.attr{display:flex;gap:40px;align-items:center}
.attr .lft{flex:1}
.attr h3{font-size:32px;font-weight:800;letter-spacing:-.8px;margin-bottom:4px}
.attr .cap{font-size:16px;color:#64748B;font-weight:600;margin-bottom:24px}
.leg{display:flex;gap:12px;margin-bottom:18px}
.leg .dot{width:14px;height:14px;border-radius:50%%;margin-top:5px;flex:0 0 14px}
.leg .t{font-size:19px;font-weight:800}
.leg .n{font-size:17px;font-weight:700;color:#0F172A;margin-top:3px}
.leg .m{font-size:15px;font-weight:600;color:#64748B;margin-top:3px}

/* ---------- tables ---------- */
table{width:100%%;border-collapse:collapse}
th{font-size:14px;font-weight:800;letter-spacing:.06em;color:#94A3B8;text-transform:uppercase;
   text-align:right;padding:0 10px 14px 10px}
th.l{text-align:left}
td{font-size:22px;font-weight:800;text-align:right;padding:22px 10px;border-top:1px solid #EEF1F5;
   font-variant-numeric:tabular-nums;color:#0F172A}
td.l{text-align:left;vertical-align:top}
td .s{display:block;font-size:14px;font-weight:700;color:#94A3B8;margin-top:6px;
      letter-spacing:.05em;text-transform:uppercase}
td .sn{display:block;font-size:16px;font-weight:600;color:#475569;margin-top:7px;
       text-transform:none;letter-spacing:0}
.ncell{width:430px;overflow:hidden}
.nm{font-size:24px;font-weight:800;letter-spacing:-.3px;color:#0F172A;display:block;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%%}
td .sn{overflow:hidden;text-overflow:ellipsis;max-width:100%%}
td .sn.one{white-space:nowrap}
.bar{width:6px;height:50px;border-radius:4px;display:inline-block;vertical-align:middle;margin-right:14px}
.g{color:#0F7A43}.r{color:#C0392B}.b{color:#1D4ED8}.a{color:#B45309}.m{color:#475569}
.ft{display:flex;font-size:16px;color:#64748B;padding-top:10px;font-weight:600;gap:14px;
    align-items:center}
.ft .rt{margin-left:auto;display:flex;gap:12px;align-items:center}
.ft .fi{width:34px;height:34px;border-radius:10px;background:#F1F5F9;display:flex;
        align-items:center;justify-content:center;flex:0 0 34px}
.ft .fi svg{width:16px;height:16px}
.tag{font-size:14px;font-weight:800;padding:4px 10px;border-radius:7px;letter-spacing:.03em}
.spark{display:block}
.why{font-size:17px;font-weight:600;color:#475569;padding:0 0 20px 20px;line-height:1.55}
.why b{color:#0F172A;font-weight:800}
"""


# ---------------------------------------------------------------- icons
def _svg(p, sw=2):
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="%s" '
            'stroke-linecap="round" stroke-linejoin="round">%s</svg>' % (sw, p))


IC = {
    "trend": _svg('<path d="M3 17l6-6 4 4 8-8"/><path d="M21 7h-5v5"/>'),
    "money": _svg('<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/>'),
    "coins": _svg('<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/>'
                  '<path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>'),
    "grid": _svg('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
                 '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
    "tag": _svg('<path d="M20.6 13.4l-7.2 7.2a2 2 0 01-2.8 0l-7-7A2 2 0 013 12.2V5a2 2 0 012-2h7.2a2 2 0 011.4.6l7 7a2 2 0 010 2.8z"/>'
                '<circle cx="7.5" cy="7.5" r="1.2" fill="currentColor"/>'),
    "bag": _svg('<path d="M6 7h12l1 13H5L6 7z"/><path d="M9 7V5a3 3 0 016 0v2"/>'),
    "pie": _svg('<path d="M12 3v9h9a9 9 0 10-9-9z"/><path d="M21 12a9 9 0 01-9 9"/>'),
    "mega": _svg('<path d="M3 11v2a1 1 0 001 1h2l5 4V6L6 10H4a1 1 0 00-1 1z"/><path d="M16 8a5 5 0 010 8"/>'),
    "click": _svg('<path d="M9 3v3M4.2 4.2l2.1 2.1M3 9h3M15 15l6 2-3 1-1 3-2-6z"/><path d="M12 12l3 3"/>'),
    "target": _svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>'),
    "search": _svg('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>'),
    "star": _svg('<path d="M12 3l2.6 5.6 6.1.8-4.5 4.2 1.2 6.1L12 16.8 6.6 19.7l1.2-6.1L3.3 9.4l6.1-.8L12 3z"/>'),
    "check": _svg('<path d="M4 12.5l5 5L20 6.5"/>', 3),
    "info": _svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.5v.5"/>'),
    "warn": _svg('<path d="M12 3l9.5 17H2.5L12 3z"/><path d="M12 9v5M12 17.5v.5"/>'),
    "down": _svg('<path d="M12 4v14M6 13l6 6 6-6"/>'),
    "up": _svg('<path d="M12 20V6M6 11l6-6 6 6"/>'),
    "swap": _svg('<path d="M4 8h13l-3-3M20 16H7l3 3"/>'),
    "cal": _svg('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>'),
    "people": _svg('<circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0113 0"/><circle cx="17.5" cy="9" r="2.6"/>'
                   '<path d="M15 20a5.5 5.5 0 016.5-4.3"/>'),
    "pencil": _svg('<path d="M4 20l4-1 11-11-3-3L5 16l-1 4z"/>'),
    "rules": _svg('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>'),
    "clock": _svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
}

TINT = {"g": ("#E7F8EE", "#0F7A43"), "b": ("#E8F0FE", "#1D4ED8"), "p": ("#EFEBFE", "#6D28D9"),
        "a": ("#FEF6E7", "#B45309"), "r": ("#FDECEC", "#C0392B"), "n": ("#F1F5F9", "#475569")}


def gi(icon, tint="b"):
    bg, fg = TINT.get(tint, TINT["b"])
    return '<span class="gi" style="background:%s;color:%s">%s</span>' % (bg, fg, IC.get(icon, IC["grid"]))




def esc(s): return _html.escape(str(s or ""))


def status(lab):
    """Loud. A classification you have to hunt for is a classification nobody acts on."""
    return '<span class="st st-%s">%s</span>' % (str(lab).lower().replace(" ", "-"), esc(lab))


def pill(p, lower_better=False, neutral=False, suffix="%"):
    """A percent with a colour that means BUSINESS, not direction. CPP down is good."""
    if p is None: return '<span class="pill nu">n/a</span>'
    if neutral:                       cls = "bl"
    elif abs(p) < 0.5:                cls = "nu"
    else:
        good = (p < 0) if lower_better else (p > 0)
        cls = "up" if good else "dn"
    return '<span class="pill %s">%s %+.0f%s</span>' % (cls, "▲" if p >= 0 else "▼", p, suffix)


def better(w_, l_, lower_better=False):
    """HOW MUCH better, in percent. 'winners hook 19.2%, losers 16.6%' is two numbers.
    '+16% better' is the finding."""
    if not w_ or not l_: return ""
    d = (w_ - l_) / abs(l_) * 100.0
    if lower_better: d = -d
    return '<span class="pill %s">%s%.0f%% %s</span>' % (
        "up" if d >= 0 else "dn", "+" if d >= 0 else "", d, "better" if d >= 0 else "worse")


def kpi(label, value, icon, tint, prev=None, p=None, lower_better=False, neutral=False, note=None,
        big=False, cls="kpi"):
    """The reference layout: icon chip, label, dominant number, delta pill, previous underneath."""
    return ('<div class="%s%s">'
            '<div class="kh">%s<span class="lb">%s</span></div>'
            '<div class="kv"><span class="v">%s</span>%s%s</div>'
            '<div class="prev">%s</div></div>'
            % (cls, (" tint-%s" % tint) if (big and tint in ("g", "b", "p")) else "",
               gi(icon, tint), esc(label), esc(value),
               ('<span class="vs">%s</span>' % esc(note)) if note else "",
               pill(p, lower_better, neutral) if p is not None else "",
               ("Previous: %s" % esc(prev)) if prev is not None else "&nbsp;"))


def tile(label, value, was=None, p=None, lower_better=False, neutral=False,
         icon="grid", tint="n"):
    return ('<div class="tile">'
            '<div class="kh">%s<span class="lb">%s</span></div>'
            '<div class="kv"><span class="v">%s</span>%s</div>'
            '<div class="prev">%s</div></div>'
            % (gi(icon, tint), esc(label), esc(value),
               pill(p, lower_better, neutral) if p is not None else "",
               ("was %s" % esc(was)) if was is not None else "&nbsp;"))


def head(icon, title, note=""):
    return ('<div class="ch"><span class="ic">%s</span><h2>%s</h2>%s</div>'
            % (IC.get(icon, IC["grid"]), esc(title),
               ('<span class="note">%s</span>' % esc(note)) if note else ""))


def call(kind, text, icon=None):
    ic = icon or {"c-g": "check", "c-r": "warn", "c-b": "info", "c-a": "warn"}[kind]
    return '<div class="callout %s"><span class="ci">%s</span><div>%s</div></div>' % (
        kind, IC[ic], text)


WINN = {"daily": "yesterday", "3day": "last 3 days", "7day": "last 7 days"}


def page(A, win, title, sub, body, height):
    cur, pre = az.DATES.get("label"), az.DATES.get("p_label")
    hd = ('<div class="hd"><span>%s</span><span class="sep">|</span><span>%s</span>'
          '<div class="dates"><span>%s</span><span class="vs">vs</span><span>%s</span></div></div>'
          % (esc(A["account"]["name"].upper()), esc(WIN_TITLE.get(win, "MEMO")),
             esc(_fmt_range(cur)), esc(_fmt_range(pre))))
    ft = ('<div class="ft"><span class="fi">%s</span><div>%s</div>'
          '<div class="rt"><span class="fi">%s</span>'
          '<div>Audience is Meta\'s own<br>breakdown by audience segment.</div></div></div>'
          % (IC["cal"], esc(WIN_FOOT.get(win, "")), IC["people"]))
    return ("<!doctype html><meta charset=utf-8><style>%s</style><body><div class=pg>%s<h1>%s</h1>"
            "<div class=sub>%s</div>%s%s</div></body>"
            % (CSS % W, hd, esc(title), esc(sub), body, ft)), height


def today_vs(dw, key="rev"):
    """TODAY, AS A PERCENT OF THIS ENTITY'S OWN AVERAGE FOR THE WINDOW.
    "TODAY NORMAL" on an ad that did 23% less than its own average is not information, it is
    noise wearing a label. Lead with the number, keep the band as context."""
    if not dw: return '<span class="pill nu">no daily history</span>'
    d = dw["rev_d"] if key == "rev" else dw["roas_d"]
    band = dw["state"]
    if d is None:
        return '<span class="pill nu">%s</span>' % esc(band)
    cls = "up" if d >= 0 else "dn"
    tag = {"NORMAL": "inside its normal band", "BELOW BAND": "OUTSIDE its normal band",
           "ABOVE BAND": "OUTSIDE its normal band"}[band]
    return ('<span class="pill %s">TODAY %+.0f%% vs its own 7d avg</span>'
            '<span class="sn">revenue %s vs %s / day &nbsp;·&nbsp; %s</span>'
            % (cls, d, _k(dw["rev_now"]), _k(dw["rev_avg"]), tag))


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
    s = A["summary"]
    p = s.get("prev") or {}     # analyze() hangs the previous period here
    D = decompose({"spend": s.get("spend"), "rev": s.get("rev")},
                  {"spend": p.get("spend"), "rev": p.get("rev")})
    d_rev = pct(s.get("rev") or 0, p.get("rev") or 0)
    d_sp = pct(s.get("spend") or 0, p.get("spend") or 0)
    d_roas = pct(s.get("roas") or 0, p.get("roas") or 0)

    over = '<div class="grid g3">%s%s%s</div>' % (
        kpi("Revenue", "%s EGP" % _k(s.get("rev") or 0), "money", "g",
            prev="%s EGP" % _k(p.get("rev") or 0), p=d_rev, big=True),
        kpi("Spend", "%s EGP" % _k(s.get("spend") or 0), "coins", "b",
            prev="%s EGP" % _k(p.get("spend") or 0), p=d_sp, neutral=True, big=True),
        kpi("ROAS", "%.2fx" % r2(s.get("roas") or 0), "trend", "p",
            prev="%.2fx" % r2(p.get("roas") or 0), p=d_roas, big=True,
            note=("(%.2fx → %.2fx)" % (r2(p.get("roas") or 0), r2(s.get("roas") or 0)))
                 if p.get("roas") else None))
    if D:
        ok = D["efficient"]
        over += call("c-g" if ok else "c-r", esc(az.verdict_line(D)))
    ov = '<div class="card">%s%s</div>' % (head("trend", "Performance overview"), over)

    MET = [("Spend", "spend", "%s", False, True, "coins", "b"),
           ("Revenue", "rev", "%s", False, False, "money", "g"),
           ("ROAS", "roas", "%.2fx", False, False, "trend", "p"),
           ("CPP", "cpa", "%s", True, False, "tag", "a"),
           ("AOV", "aov", "%s", False, False, "bag", "a"),
           ("CVR", "cvr", "%.2f%%", False, False, "pie", "p"),
           ("CPMR", "cpmr", "%s", True, False, "mega", "b"),
           ("Out CTR", "octr", "%.2f%%", False, False, "click", "g")]
    tiles = ""
    for lab, k, f, lb, nu, ic, tn in MET:
        a_, b_ = s.get(k) or 0, p.get(k) or 0
        fv = (lambda v: _k(v)) if f == "%s" else (lambda v: f % r2(v))
        tiles += tile(lab, fv(a_), fv(b_), pct(a_, b_), lower_better=lb, neutral=nu,
                      icon=ic, tint=tn)
    matrix = ('<div class="card">%s<div class="grid g4">%s</div></div>'
              % (head("grid", "Detailed metrics matrix",
                      "every number, and what it was in the previous period"), tiles))

    # ---- REVENUE GROWTH ATTRIBUTION. Stacked bar, leader lines, exactly like the reference.
    why = ""
    if D:
        tot = abs(D["spend_eff"]) + abs(D["perf_eff"]) or 1
        wsp = abs(D["spend_eff"]) / tot * 100
        wpf = 100 - wsp
        BH = 300.0
        h1_ = max(26.0, BH * wsp / 100.0)
        h2_ = max(26.0, BH * wpf / 100.0)
        c_sp, c_pf = "#2563EB", ("#16A34A" if D["perf_eff"] >= 0 else "#C0392B")
        y1 = 40.0
        y2 = y1 + h1_
        svg = ('<svg width="520" height="400" viewBox="0 0 520 400">'
               '<text x="150" y="24" text-anchor="middle" font-size="19" font-weight="800" '
               'fill="#0F172A" font-family="DejaVu Sans">%s%s EGP</text>'
               '<rect x="80" y="%.1f" width="140" height="%.1f" fill="%s" rx="3"/>'
               '<text x="150" y="%.1f" text-anchor="middle" font-size="18" font-weight="800" '
               'fill="#fff" font-family="DejaVu Sans">%.0f%%</text>'
               '<rect x="80" y="%.1f" width="140" height="%.1f" fill="%s" rx="3"/>'
               '<text x="150" y="%.1f" text-anchor="middle" font-size="18" font-weight="800" '
               'fill="#fff" font-family="DejaVu Sans">%.0f%%</text>'
               '<rect x="80" y="%.1f" width="140" height="16" fill="#CBD5E1" rx="3"/>'
               '<path d="M220 %.1f H300" stroke="#CBD5E1" stroke-width="1.5" stroke-dasharray="3 3"/>'
               '<circle cx="304" cy="%.1f" r="3.5" fill="%s"/>'
               '<text x="316" y="%.1f" font-size="19" font-weight="800" fill="%s" '
               'font-family="DejaVu Sans">%s%s EGP</text>'
               '<text x="316" y="%.1f" font-size="15" font-weight="700" fill="#64748B" '
               'font-family="DejaVu Sans">%.0f%% of the move</text>'
               '<path d="M220 %.1f H300" stroke="#CBD5E1" stroke-width="1.5" stroke-dasharray="3 3"/>'
               '<circle cx="304" cy="%.1f" r="3.5" fill="%s"/>'
               '<text x="316" y="%.1f" font-size="19" font-weight="800" fill="%s" '
               'font-family="DejaVu Sans">%s%s EGP</text>'
               '<text x="316" y="%.1f" font-size="15" font-weight="700" fill="#64748B" '
               'font-family="DejaVu Sans">%.0f%% of the move</text>'
               '</svg>'
               % ("+" if D["d_rev"] >= 0 else "-", _k(abs(D["d_rev"])),
                  y1, h1_, c_sp, y1 + h1_ / 2 + 6, wsp,
                  y2, h2_, c_pf, y2 + h2_ / 2 + 6, wpf,
                  y2 + h2_ + 4,
                  y1 + h1_ / 2, y1 + h1_ / 2, c_sp, y1 + h1_ / 2 - 2, c_sp,
                  "+" if D["spend_eff"] >= 0 else "-", _k(abs(D["spend_eff"])),
                  y1 + h1_ / 2 + 20, wsp,
                  y2 + h2_ / 2, y2 + h2_ / 2, c_pf, y2 + h2_ / 2 - 2, c_pf,
                  "+" if D["perf_eff"] >= 0 else "-", _k(abs(D["perf_eff"])),
                  y2 + h2_ / 2 + 20, wpf))
        legend = ('<div class="leg"><span class="dot" style="background:%s"></span><div>'
                  '<div class="t" style="color:%s">Budget impact</div>'
                  '<div class="n">%s%s EGP &nbsp;|&nbsp; %.0f%% of move</div>'
                  '<div class="m">Spend: %s → %s (%s)</div></div></div>'
                  '<div class="leg"><span class="dot" style="background:%s"></span><div>'
                  '<div class="t" style="color:%s">Account performance impact</div>'
                  '<div class="n">%s%s EGP &nbsp;|&nbsp; %.0f%% of move</div>'
                  '<div class="m">ROAS: %.2fx → %.2fx (%s)</div></div></div>'
                  % (c_sp, c_sp, "+" if D["spend_eff"] >= 0 else "-", _k(abs(D["spend_eff"])), wsp,
                     _k(p.get("spend") or 0), _k(s.get("spend") or 0),
                     ("%+.0f%%" % d_sp) if d_sp is not None else "n/a",
                     c_pf, c_pf, "+" if D["perf_eff"] >= 0 else "-", _k(abs(D["perf_eff"])), wpf,
                     r2(D["roas0"]), r2(D["roas1"]),
                     ("%+.0f%%" % d_roas) if d_roas is not None else "n/a"))
        why = ('<div class="card">%s'
               '<div class="attr"><div class="lft">'
               '<h3 class="%s">Revenue %s%s EGP</h3>'
               '<div class="cap">(change from the previous period)</div>%s</div>'
               '<div>%s</div></div>%s</div>'
               % (head("target", "Revenue growth attribution",
                       "these two add to the revenue change exactly. there is no third thing"),
                  "g" if D["d_rev"] >= 0 else "r",
                  "+" if D["d_rev"] >= 0 else "-", _k(abs(D["d_rev"])), legend, svg,
                  call("c-b",
                       "The move is mostly the <b>BUDGET you set</b>. The account did not really change."
                       if D["driver"] == "SPEND" else
                       "The move is mostly the <b>ACCOUNT</b>. This is real performance, not money.",
                       icon="star")))

    # AUDIENCE, ON THE FIRST SCREEN. An account number with no segment split hides which third
    # of the money actually moved.
    segs, sprev = A.get("segs") or {}, A.get("segs_prev") or {}
    t_sp = sum((segs.get(k) or {}).get("spend") or 0 for k in ("NEW", "ENGAGED", "EXISTING")) or 1
    t_rv = sum((segs.get(k) or {}).get("rev") or 0 for k in ("NEW", "ENGAGED", "EXISTING")) or 1
    rows = ""
    for kk in ("NEW", "ENGAGED", "EXISTING"):
        m, q = segs.get(kk) or {}, sprev.get(kk) or {}
        if not (m.get("spend") or 0) and not (q.get("spend") or 0): continue
        col = {"NEW": "#2563EB", "ENGAGED": "#B45309", "EXISTING": "#0F7A43"}[kk]
        dr = (m.get("rev") or 0) - (q.get("rev") or 0)
        rows += ('<tr><td class=l><div class="ncell"><span class="bar" style="background:%s"></span>'
                 '<span class="nm">%s</span></div></td>'
                 '<td>%s<span class="s">%.0f%% of spend</span></td><td>%s</td>'
                 '<td>%s<span class="s">%.0f%% of revenue</span></td><td>%s</td>'
                 '<td>%.2fx<span class="s">was %.2fx</span></td><td>%s</td>'
                 '<td class="%s">%s%s EGP<span class="s">revenue moved</span></td></tr>'
                 % (col, esc(SEGN[kk]),
                    _k(m.get("spend") or 0), (m.get("spend") or 0) / t_sp * 100,
                    pill(pct(m.get("spend") or 0, q.get("spend") or 0), neutral=True),
                    _k(m.get("rev") or 0), (m.get("rev") or 0) / t_rv * 100,
                    pill(pct(m.get("rev") or 0, q.get("rev") or 0)),
                    r2(m.get("roas") or 0), r2(q.get("roas") or 0),
                    pill(pct(m.get("roas") or 0, q.get("roas") or 0)),
                    "g" if dr >= 0 else "r", "+" if dr >= 0 else "-", _k(abs(dr))))
    aud = ('<div class="card">%s'
           '<table><tr><th class=l>Segment</th><th>Spend</th><th>vs prev</th><th>Revenue</th>'
           '<th>vs prev</th><th>ROAS</th><th>vs prev</th><th>Contribution</th></tr>%s</table></div>'
           % (head("people", "Audience segments",
                   "spend and attributed revenue vs the previous period"), rows)) if rows else ""

    nseg = rows.count("<tr>")
    h = 250 + 330 + (110 if D else 0) + 460 + (500 if D else 0) + (140 + 110 * nseg if nseg else 0) + 110
    return page(A, win, "Performance analysis: what happened",
                "A clear view of performance changes, what drove them, and which audience moved.",
                ov + matrix + why + aud, h)


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

    hdr = ('<div class="card"><div class="ch"><span class="ic">' + IC["swap"] + '</span><h2>Both sides of the move</h2>'
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
                cells += ('<td>%s<div style="margin-top:6px">%s</div>'
                          '<span class="s">%s · was %s</span></td>'
                          % (v, pill(pct(a_, b_), lower_better=(k in LOWER), neutral=nu),
                             esc(lab), (_k(b_) if f == "%s" else (f % r2(b_)))))
            dec = ""
            if D:
                dec = ('<span class="sn">budget <b>%s%s</b> &nbsp;·&nbsp; performance <b>%s%s</b> &nbsp;—&nbsp; %s</span>'
                       % ("+" if D["spend_eff"] >= 0 else "-", _k(abs(D["spend_eff"])),
                          "+" if D["perf_eff"] >= 0 else "-", _k(abs(D["perf_eff"])),
                          "mostly the budget you set" if D["driver"] == "SPEND"
                          else ("real performance, the account got better" if D["d_rev"] >= 0
                                else "a real performance change, not a budget change")))
            cause = ('<span class="tag %s">%s</span>' % ("am", esc(r["dx"][0]))) if r.get("dx") else ""
            tr += ('<tr><td class=l><div class="ncell"><span class="bar" style="background:%s"></span>'
                   '<span class="nm">%s</span>'
                   '<span class="sn" style="color:%s;font-weight:800;font-size:18px">%s%s EGP '
                   '&nbsp;·&nbsp; %+.0f%% of all movement</span>%s%s</div></td>%s</tr>'
                   % (col, esc(_clip(safe(r["name"]), 46)), col,
                      "+" if r["d_rev"] >= 0 else "-", _k(abs(r["d_rev"])), r["share"],
                      cause, dec, cells))
        return ('<div class="card">' + head(cls, title, note)
                + '<table><tr><th class=l>%s</th>%s</tr>%s</table></div>'
                % (level.title(), th, tr))

    body = hdr
    body += block(los, "What pulled it down", "down",
                  "every metric against the same entity's own previous period")
    body += block(gan, "What fought back", "up", "do not cut these by accident")
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
            an = today_vs(daywk(A, r["ad_id"]))
            tests = " &nbsp;·&nbsp; ".join(
                "<b>%s</b> %s (%s)" % ("PASS" if t["ok"] else "FAIL", esc(t["t"]), esc(t["v"]))
                for t in r["tests"][:4])
            tr += ('<tr><td class=l><div class="ncell"><span class="bar" style="background:%s"></span>'
                   '<span class="nm">%s</span>'
                   '<span class="sn">%s &nbsp; %s &nbsp;·&nbsp; confidence %s</span></div></td>'
                   '<td>%s<span class="s">spend · 7d</span></td>'
                   '<td class="%s">%.2fx<span class="s">roas · 7d · acct %.2fx</span></td>'
                   '<td>%s<span class="s">cpp · 7d</span></td><td>%s<span class="s">aov · 7d</span></td>'
                   '<td>%.2f%%<span class="s">cvr · 7d</span></td><td>%.2f%%<span class="s">out ctr · 7d</span></td>'
                   '<td class="%s">%.2f<span class="s">freq · 7d</span></td>'
                   '<td>%d<span class="s">purchases · 7d</span></td>'
                   '<td style="width:250px">%s</td></tr>'
                   '<tr><td colspan=10 class="why" style="border:0">%s</td></tr>'
                   % (col, esc(_clip(r["name"], 46)), status(lab), esc(r["kind"]), r["conf"],
                      _k(k.get("spend") or 0),
                      "g" if (k.get("roas") or 0) >= (acc.get("roas") or 0) else "r",
                      r2(k.get("roas") or 0), r2(acc.get("roas") or 0),
                      _k(k.get("cpa") or 0), _k(k.get("aov") or 0), r2(k.get("cvr") or 0),
                      r2(k.get("octr") or 0),
                      "r" if (k.get("freq") or 0) > 4.0 else "",
                      r2(k.get("freq") or 0), int(k.get("purch") or 0),
                      an, tests))
        return tr

    def blk(title, ic, note, rs):
        if not rs: return ""
        return '<div class="card">' + head(ic, title, note) + '<table>%s</table></div>' % rows(rs)

    crit = ""
    for lab in ("SCALE", "HEADROOM", "SATURATED", "CUT"):
        col = {"SCALE": "g", "HEADROOM": "g", "SATURATED": "a", "CUT": "r"}[lab]
        crit += ('<div class="tile"><div class="kh">%s<span class="lb">%s</span></div>%s</div>'
                 % (gi("rules", col), lab,
                    "".join('<div class="prev" style="color:#475569;margin-top:8px">· %s</div>'
                            % esc(c) for c in CRIT[lab])))
    rules = ('<div class="card"><div class="ch"><span class="ic">' + IC["rules"] + '</span>'
             '<h2>The criteria &nbsp;·&nbsp; this is the whole rulebook</h2></div>'
             '<div class="grid g4">%s</div>'
             '<div class="callout c-b">An ad must pass EVERY line of a label to get it. '
             'SATURATED needs 3 of 4 plus frequency over 4.0. '
             'CATALOGUE and DPA are never counted as creative winners.</div></div>' % crit)

    body = (blk("Working · creative", "up",
                "shot creative only. catalogue is separated out below", scale)
            + blk("Working · catalogue and DPA", "grid",
                  "these are a feed, not a shoot. scale the budget, do not brief a video off them", cat)
            + blk("Not working · cut or saturated", "down",
                  "with today measured against the ad's own last 7 days", cut)
            + rules)
    return page(A, win, "What is working, and what stopped",
                "Every label has printed criteria, this ad's actual value against each one, and "
                "whether today is normal for that ad or an anomaly.", body, 1760)


# ---------------------------------------------------------------- 4 · MONEY
def c_money(A, win):
    P = proposals(A, win)
    if not P or (not P["scale"] and not P["cut"]): return None
    acc = A.get("b7_acc") or A["summary"]
    board = '<div class="grid g3">%s%s%s</div>' % (
        kpi("Spend change", "%s%s / day" % ("+" if P["net_spend"] >= 0 else "-",
                                            _k(abs(P["net_spend"]))), "coins", "b",
            prev="add %s, free %s" % (_k(P["added"]), _k(P["freed"])), big=True),
        kpi("Expected revenue", "%s%s / day" % ("+" if P["net_rev"] >= 0 else "-",
                                                _k(abs(P["net_rev"]))), "money", "g",
            prev="at each ad's own 7 day ROAS", big=True),
        kpi("Account ROAS", "%.2fx" % r2(P["acc_roas"]), "trend", "p",
            prev="the bar every ad is judged against", big=True))
    board = ('<div class="card">%s%s%s</div>'
             % (head("coins", "The whole board, per day"), board,
                call("c-b", "Add <b>%s/day</b> to the winners, take <b>%s/day</b> off the losers. "
                            "Every ad is priced at <b>its own 7 day ROAS</b>, never the account average."
                     % (_k(P["added"]), _k(P["freed"])), icon="info")))

    # EVERY METRIC AGAINST ITS OWN PREVIOUS PERIOD. A recommendation with no baseline is a guess.
    def cmp3(r):
        k = r["k"]; q = k.get("prev") or {}
        out = ""
        for lab, key, f, lb in (("roas", "roas", "%.2fx", False),
                                ("cpp", "cpa", "%s", True),
                                ("freq", "freq", "%.2f", True)):
            a_, b_ = k.get(key) or 0, q.get(key) or 0
            v = _k(a_) if f == "%s" else (f % r2(a_))
            w = _k(b_) if f == "%s" else (f % r2(b_))
            out += ('<td>%s<div style="margin-top:7px">%s</div>'
                    '<span class="s">%s · 7d · was %s</span></td>'
                    % (v, pill(pct(a_, b_), lower_better=lb), lab, w))
        return out

    def row(r, up):
        col = "#0F7A43" if up else "#C0392B"
        why = " &nbsp;·&nbsp; ".join(esc(t["t"]) for t in r["tests"] if t["ok"])
        if up:
            money = ('<td class=b>+%s<span class="s">extra spend / day</span></td>'
                     '<td class=g>+%s<span class="s">expected revenue / day</span></td>'
                     '<td class=g>+%s<span class="s">better than the account avg</span></td>'
                     % (_k(r["add_day"]), _k(r["inc_rev"]), _k(max(0, r["inc_vs_acct"]))))
        else:
            money = ('<td class=r>-%s<span class="s">spend freed / day</span></td>'
                     '<td class=r>-%s<span class="s">revenue given up / day</span></td>'
                     '<td class=g>+%s<span class="s">net, redeployed at %.2fx</span></td>'
                     % (_k(r["cut_day"]), _k(r["lost_rev"]), _k(max(0, r["net"])),
                        r2(P["acc_roas"])))
        return ('<tr><td class=l><div class="ncell"><span class="bar" style="background:%s"></span>'
                '<span class="nm">%s</span>'
                '<span class="sn">%s &nbsp; %s &nbsp;·&nbsp; confidence %s</span></div></td>'
                '<td>%s<span class="s">now / day</span></td>'
                '<td class="%s">%s<span class="s">go to / day</span></td>'
                '%s%s</tr>'
                '<tr><td colspan=8 class="why" style="border:0"><b>WHY:</b> %s</td></tr>'
                % (col, esc(_clip(r["name"], 46)), status(r["label"]), esc(r["kind"]), r["conf"],
                   _k(r["sp_day"]), "b" if up else "r", _k(r["new_day"]),
                   cmp3(r), money, why))

    TH = ('<tr><th class=l>Ad</th><th>Now</th><th>Go to</th><th>ROAS</th><th>CPP</th>'
          '<th>Frequency</th><th>Spend</th><th>Revenue</th><th>Net</th></tr>')
    body = board
    if P["scale"]:
        body += ('<div class="card">%s<table>%s%s</table></div>'
                 % (head("up", "Put money in",
                         "an investment proposal, priced in EGP per day"),
                    TH, "".join(row(r, True) for r in P["scale"][:4])))
    if P["cut"]:
        body += ('<div class="card">%s<table>%s%s</table></div>'
                 % (head("down", "Take money out",
                         "what you give up, and what the freed money buys elsewhere"),
                    TH, "".join(row(r, False) for r in P["cut"][:4])))
    h = (250 + 330 + (150 + 205 * len(P["scale"][:4]) if P["scale"] else 0)
         + (150 + 205 * len(P["cut"][:4]) if P["cut"] else 0) + 110)
    return page(A, win, "What to do with the money",
                "Current budget, recommended budget, the extra spend, what that spend buys at "
                "that ad's own ROAS, and every metric against its own previous period.", body, h)


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
        cards += ('<div class="card">'
                  '<div class="ch"><span class="ic" style="background:%s">%s</span><h2>%s</h2>'
                  '<span class="note">%.0f%% of spend &nbsp;·&nbsp; %.0f%% of revenue</span></div>'
                  '<div class="grid g4">%s</div>%s</div>'
                  % (COL[k], IC["people"], esc(SEGN[k]),
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
        move = ('<div class="card"><div class="ch"><span class="ic">' + IC["swap"] + '</span>'
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

    top = ('<div class="card"><div class="ch"><span class="ic">' + IC["clock"] + '</span>'
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
        k = r["k"]
        vid = k.get("type") == "VIDEO"
        col = {"FATIGUED": "#B42318", "SATURATED": "#B54708", "FATIGUING": "#B54708",
               "FRESH": "#067647", "NO PRIOR": "#667085"}[r["state"]]
        dw = daywk(A, r["ad_id"])
        # THE WHOLE POINT: is this a week-long slide, or just a bad Tuesday.
        if dw:
            cl = {"NORMAL": "nu", "BELOW BAND": "dn", "ABOVE BAND": "up"}[dw["state"]]
            today = ('<span class="pill %s">TODAY %s</span>'
                     '<span class="sn">ROAS <b>%.2fx</b> vs <b>%.2fx</b> its own 7 day average '
                     '&nbsp;<b>%s</b></span>'
                     '<span class="sn">revenue %s vs %s / day</span>'
                     % (cl, dw["state"], r2(dw["roas_now"]), r2(dw["roas_avg"]),
                        ("%+.0f%%" % dw["roas_d"]) if dw["roas_d"] is not None else "n/a",
                        _k(dw["rev_now"]), _k(dw["rev_avg"])))
            sp = spark(dw["series"], "rev", 200, 52, dw["lo"], dw["hi"])
        else:
            today, sp = '<span class="pill nu">no daily history</span>', ""
        cells = ""
        for lab, v, lb in (("hook Δ · 7d vs prior 7d", r["d_hook"] if vid else None, False),
                           ("out ctr Δ · 7d vs prior 7d", r["d_octr"], False),
                           ("cpm Δ · 7d vs prior 7d", r["d_cpm"], True),
                           ("cpp Δ · 7d vs prior 7d", r["d_cpp"], True)):
            cells += '<td>%s<span class="s">%s</span></td>' % (pill(v, lower_better=lb), lab)
        tr += ('<tr><td class=l><div class="ncell"><span class="bar" style="background:%s"></span>'
               '<span class="nm">%s</span>'
               '<span class="sn">%s &nbsp; <b>%d of 5 tests fired</b> &nbsp;·&nbsp; %s</span>'
               '<span class="sn">frequency <b class="%s">%.2f</b> (last 7 days) &nbsp;·&nbsp; '
               'hook <b>%s</b> &nbsp;·&nbsp; hold <b>%s</b></span></div></td>'
               '<td>%s<span class="s">spend · 7d</span></td>'
               '<td>%.2fx<span class="s">roas · 7d</span></td>'
               '%s<td style="width:300px">%s</td>'
               '<td style="width:210px">%s<span class="s">14 days · its own band</span></td></tr>'
               % (col, esc(_clip(r["name"], 40)), status(r["state"]), r["hits"],
                  esc("CATALOGUE" if is_catalogue(k) else (k.get("type") or "")),
                  "r" if r["freq"] > FATG["sat"] else "", r["freq"],
                  ("%.1f%%" % r["hook"]) if vid else "n/a",
                  ("%.0f%%" % r["hold"]) if vid else "n/a",
                  _k(r["spend"]), r2(r["roas"]), cells, today, sp))

    table = ('<div class="card"><div class="ch"><span class="ic">' + IC["grid"] + '</span>'
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
    rules = ('<div class="card"><div class="ch"><span class="ic">' + IC["rules"] + '</span>'
             '<h2>The five fatigue tests &nbsp;·&nbsp; this is the whole rulebook</h2></div>'
             '<table>%s</table>'
             '<div class="callout c-r"><b>FATIGUED</b> = 3 or more of the five fire and frequency is '
             'over %.1f, OR frequency is over %.1f with 2 firing. &nbsp;<b>SATURATED</b> = frequency '
             'over %.1f on its own: the same people are seeing it again whatever else is true. '
             '&nbsp;<b>FATIGUING</b> = 2 fire. &nbsp;<b>FRESH</b> = 1 or none. A catalogue ad has no '
             'hook, so only four of the five can ever fire on it.</div></div>'
             % (crit, FATG["freq"], FATG["sat"], FATG["sat"]))
    return page(A, win, "Creative fatigue and hook rate",
                "The week tells you if it is decaying. Today tells you if it is an anomaly. "
                "Both are on every row.", top + table + rules, 1760)


# ---------------------------------------------------------------- 7 · MAKE MORE
def c_makemore(A, win):
    P = winning_pattern(A)
    LS = az.launch_stats(A, 30)
    if not P and not LS: return None

    top = ""
    if LS:
        top = ('<div class="card">%s<div class="grid g4">%s%s%s%s</div>%s%s</div>'
               % (head("star", "How many creatives to launch",
                       "counted on ads you actually LAUNCHED in the last 30 days"),
                  kpi("Launched · 30d", "%d" % LS["launched"], "pencil", "b",
                      prev="new ads created in the last 30 days", big=True),
                  kpi("Judged", "%d" % LS["judged"], "rules", "n",
                      prev="%d still gathering data" % LS["pending"], big=True),
                  kpi("Winners", "%d" % LS["winners"], "star", "g",
                      prev="%d lost to the account" % LS["losers"], big=True),
                  kpi("Hit rate", "%.0f%%" % LS["hr"], "target", "p",
                      prev="launch %d to get 1  ·  %d to get 3"
                           % (LS["need_1"], LS["need_3"]), big=True),
                  call("c-b",
                       "<b>THE CRITERIA.</b> An ad counts as JUDGED once it has spent at least "
                       "<b>%s EGP</b> and taken at least <b>%d purchases</b> in the 30 days. It counts "
                       "as a WINNER if its ROAS beats the account's <b>%.2fx over those same 30 days</b>. "
                       "Catalogue and DPA are excluded on both sides: a product feed is not a creative "
                       "you shot."
                       % (_k(LS["min_spend"]), LS["min_pur"], r2(LS["bar"])), icon="rules"),
                  call("c-a",
                       "At a <b>%.0f%%</b> hit rate you must launch <b>%d</b> new creatives to get 1 more "
                       "winner and <b>%d</b> to get 3. You launched <b>%d</b> in the last 30 days."
                       % (LS["hr"], LS["need_1"], LS["need_3"], LS["launched"]), icon="warn")))
    if not P:
        return page(A, win, "Make more of what worked",
                    "How many creatives you must launch, and against which bar.",
                    top, 250 + 560 + 110)

    Wn, Ls = P["W"], P["L"]
    MET = [("Hook rate", "hook", "%.1f%%"), ("Hold", "hold", "%.0f%%"),
           ("Outbound CTR", "octr", "%.2f%%"), ("CVR", "cvr", "%.2f%%"),
           ("Frequency", "freq", "%.2f"), ("ROAS", "roas", "%.2fx"),
           ("AOV", "aov", "%s"), ("CPP", "cpa", "%s")]
    for d in (Wn, Ls):
        for kk in ("hold", "r50", "r75"):
            if (d.get(kk) or 0) > 100: d[kk] = None   # Meta undercounts 3s views; never print >100%
    tiles = ""
    for lab, k, f in MET:
        w_, l_ = Wn.get(k), Ls.get(k)
        sw = (_k(w_) if f == "%s" else (f % w_)) if w_ else "n/a"
        sl = (_k(l_) if f == "%s" else (f % l_)) if l_ else "n/a"
        lb = k in ("freq", "cpa")
        good = w_ is not None and l_ is not None and ((w_ < l_) if lb else (w_ > l_))
        tiles += ('<div class="tile"><div class="kh">%s<span class="lb">%s · %s</span></div>'
                  '<div class="kv"><span class="v %s">%s</span>%s</div>'
                  '<div class="prev">losers %s</div></div>'
                  % (gi("target", "g" if good else "n"), esc(lab), esc(WINN.get(win, "")),
                     "g" if good else "", esc(sw), better(w_, l_, lower_better=lb), esc(sl)))
    pat = ('<div class="card">%s<div class="grid g4">%s</div>%s</div>'
           % (head("target", "What the winners have in common",
                   "winners vs the shot creative that lost to the account"), tiles,
              call("c-b", "Winners are the <b>%d</b> creative ads that beat the account's <b>%.2fx over "
                          "the last 7 days</b> on "
                          "at least 3 purchases. Losers are the <b>%d</b> that did not. Same window, same "
                          "account, same offer. No catalogue on either side."
                   % (P["n_win"], r2(P["acc_roas"]), len(P["losers"])), icon="info")))

    daily = A.get("ad_daily") or {}
    prev = A.get("previews") or {}
    tr = ""
    for k in P["top"][:3]:
        aid = str(k.get("ad_id"))
        dw = daywk(A, aid)
        sp = spark(daily.get(aid) or [], "rev", 220, 54,
                   dw["lo"] if dw else None, dw["hi"] if dw else None)
        # NAME THE THING. And name where it lives, so it can actually be found.
        nm = safe(k.get("ad_name") or "(unnamed ad)")
        tr += ('<tr><td class=l><div class="ncell">'
               '<span class="bar" style="background:#0F7A43"></span>'
               '<span class="nm">%s</span>'
               '<span class="sn">CAMPAIGN &nbsp;%s</span>'
               '<span class="sn">AD SET &nbsp;%s</span>'
               '<span class="sn one">%s &nbsp;·&nbsp; all numbers are the last 7 days</span></div></td>'
               '<td>%s<span class="s">spend · 7d</span></td><td>%s<span class="s">revenue · 7d</span></td>'
               '<td class=g>%.2fx<span class="s">roas · 7d</span></td><td>%.1f%%<span class="s">hook · 7d</span></td>'
               '<td>%.0f%%<span class="s">hold · 7d</span></td><td>%.2f<span class="s">freq · 7d</span></td>'
               '<td>%d<span class="s">purchases · 7d</span></td>'
               '<td style="width:300px">%s</td><td style="width:240px">%s</td></tr>'
               % (esc(_clip(nm, 46)),
                  esc(_clip(safe(k.get("campaign") or "(unknown campaign)"), 44)),
                  esc(_clip(safe(k.get("adset") or "(unknown ad set)"), 44)),
                  esc(k.get("type") or ""),
                  _k(k.get("spend") or 0), _k(k.get("rev") or 0), r2(k.get("roas") or 0),
                  k.get("hook") or 0, k.get("hold") or 0, r2(k.get("freq") or 0),
                  int(k.get("purch") or 0), today_vs(dw), sp))
    tops = ('<div class="card">%s<table>%s</table></div>'
            % (head("star", "Model the next shoot on these",
                    "named, with their campaign and ad set, and their own 14 day history"), tr))

    lis = "".join(call("c-g", esc(l)) for l in brief_lines(P)[:5])
    brief = '<div class="card">%s%s</div>' % (
        head("pencil", "The brief · give this to the editor"), lis)

    nb = len(brief_lines(P)[:5])
    h = (250 + (560 if LS else 0) + 480 + 150 + 230 * len(P["top"][:3]) + 120 + 92 * nb + 110)
    return page(A, win, "Make more of what worked",
                "How many creatives you launched, how many won, what the winners share, and the brief.",
                top + pat + tops + brief, h)



# ---------------------------------------------------------------- 8 · OBSERVATIONS
def c_observations(A, win):
    """The last screen. What a media buyer would actually say out loud after reading the rest."""
    s_ = A["summary"]; p_ = s_.get("prev") or {}
    D = decompose({"spend": s_.get("spend"), "rev": s_.get("rev")},
                  {"spend": p_.get("spend"), "rev": p_.get("rev")})
    F = fatigue_scan(A)
    P = proposals(A, win)
    LS = az.launch_stats(A, 30)
    segs = A.get("segs") or {}

    obs = []
    if D:
        obs.append(("c-b" if D["driver"] == "SPEND" else ("c-g" if D["perf_eff"] >= 0 else "c-r"),
                    "info",
                    "<b>The move was %s.</b> Revenue %s%s EGP: %s%s of it is the budget you set, "
                    "%s%s is the account itself. Efficiency %s (ROAS %.2fx to %.2fx)."
                    % ("the BUDGET" if D["driver"] == "SPEND" else "REAL PERFORMANCE",
                       "+" if D["d_rev"] >= 0 else "-", _k(abs(D["d_rev"])),
                       "+" if D["spend_eff"] >= 0 else "-", _k(abs(D["spend_eff"])),
                       "+" if D["perf_eff"] >= 0 else "-", _k(abs(D["perf_eff"])),
                       "improved" if D["efficient"] else "got worse",
                       r2(D["roas0"]), r2(D["roas1"]))))
    bad = [r for r in F if r["state"] in ("FATIGUED", "SATURATED")]
    if bad:
        burn = sum(r["spend"] for r in bad) / 7.0
        obs.append(("c-r", "warn",
                    "<b>%d %s fatigued or saturated</b> and %s carrying <b>%s EGP a day</b>. "
                    "The worst is <b>%s</b> at frequency %.2f. Refresh the creative or the audience; "
                    "more budget on a saturated ad buys repeats, not customers."
                    % (len(bad), "ad is" if len(bad) == 1 else "ads are",
                       "it is" if len(bad) == 1 else "they are",
                       _k(burn), esc(_clip(bad[0]["name"], 40)), bad[0]["freq"])))
    else:
        obs.append(("c-g", "check",
                    "<b>No ad is fatigued or saturated.</b> Nothing in the account is being shown to "
                    "the same people over and over, so frequency is not what is holding you back."))
    if P and P["scale"]:
        r = P["scale"][0]
        obs.append(("c-g", "up",
                    "<b>Biggest opportunity: %s.</b> It returns %.2fx against the account's %.2fx at "
                    "frequency %.2f. Take it from %s to %s a day: that is %s more spend for about "
                    "<b>%s more revenue a day</b>."
                    % (esc(_clip(r["name"], 40)), r2(r["roas"]), r2(P["acc_roas"]), r2(r["freq"]),
                       _k(r["sp_day"]), _k(r["new_day"]), _k(r["add_day"]), _k(r["inc_rev"]))))
    if P and P["cut"]:
        r = P["cut"][0]
        obs.append(("c-r", "down",
                    "<b>Biggest leak: %s.</b> %.2fx against the account's %.2fx. Cutting it frees "
                    "<b>%s a day</b>; redeployed at the account rate that is <b>+%s a day net</b>."
                    % (esc(_clip(r["name"], 40)), r2(r["roas"]), r2(P["acc_roas"]),
                       _k(r["cut_day"]), _k(max(0, r["net"])))))
    live = [k for k in ("NEW", "ENGAGED", "EXISTING") if (segs.get(k) or {}).get("spend")]
    if len(live) >= 2:
        best = max(live, key=lambda k: segs[k].get("roas") or 0)
        worst = min(live, key=lambda k: segs[k].get("roas") or 0)
        t_sp = sum(segs[k].get("spend") or 0 for k in live) or 1
        obs.append(("c-a", "swap",
                    "<b>The money is in the wrong audience.</b> %s returns <b>%.2fx</b> but only takes "
                    "%.0f%% of spend, while %s returns %.2fx on %.0f%% of it. Move budget from the "
                    "second to the first."
                    % (esc(SEGN[best]), r2(segs[best].get("roas") or 0),
                       (segs[best].get("spend") or 0) / t_sp * 100,
                       esc(SEGN[worst]), r2(segs[worst].get("roas") or 0),
                       (segs[worst].get("spend") or 0) / t_sp * 100)))
    if LS:
        obs.append(("c-b", "star",
                    "<b>Creative supply.</b> You launched <b>%d</b> ads in 30 days, <b>%d</b> got enough "
                    "data to judge, <b>%d</b> beat the account. That is a <b>%.0f%%</b> hit rate, so the "
                    "next winner costs about <b>%d launches</b>. Anything less than that is not a "
                    "creative plan."
                    % (LS["launched"], LS["judged"], LS["winners"], LS["hr"], LS["need_1"])))

    body = '<div class="card">%s%s</div>' % (
        head("search", "General observations",
             "what a media buyer would say out loud after reading the rest"),
        "".join(call(c, t, icon=i) for c, i, t in obs))
    body += call("c-b", "Margin and LTV are unknown from ad data. Nothing above assumes them.",
                 icon="info")
    return page(A, win, "General observations",
                "The account in plain sentences, with the money attached to each one.",
                body, 250 + 130 + 118 * len(obs) + 150)


# ---------------------------------------------------------------- driver
# TOP DOWN. Account, then campaigns, then ad sets, then ads, then what to do with the money,
# then the creative, then the sentences. You cannot judge an ad before you know whether the
# account moved, and you cannot judge a campaign before you know whether the account moved.
CARDS = (("1-account",     c_exec),
         ("2-campaigns",   lambda A, w: c_cause(A, w, "campaign")),
         ("3-adsets",      lambda A, w: c_cause(A, w, "adset")),
         ("4-ads",         lambda A, w: c_cause(A, w, "ad")),
         ("5-verdicts",    c_winners),
         ("6-money",       c_money),
         ("7-audience",    c_audience),
         ("8-fatigue",     c_fatigue),
         ("9-makemore",    c_makemore),
         ("10-observations", c_observations))


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
