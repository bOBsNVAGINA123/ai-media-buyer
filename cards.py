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


MAXH = 9000           # the measuring canvas. Nothing is ever this tall.


def _shot(html_path, png_path, height, scale):
    exe = _chrome()
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           "--force-device-scale-factor=%s" % scale, "--virtual-time-budget=2500",
           "--window-size=%d,%d" % (W, int(height)),
           "--screenshot=%s" % png_path, "file://" + html_path]
    subprocess.run(cmd, capture_output=True, timeout=120)
    return os.path.exists(png_path) and os.path.getsize(png_path) > 1000


def _content_height(png_path):
    """The last row of pixels that is not background, in CSS pixels."""
    try:
        from PIL import Image
    except Exception:
        return None
    im = Image.open(png_path).convert("RGB")
    w, h = im.size
    bg = im.getpixel((2, 2))
    px = im.load()
    for y in range(h - 1, -1, -1):
        for x in range(0, w, 8):
            p = px[x, y]
            if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 12:
                return y + 1
    return None


def shoot(html, height=None):
    """HTML in, PNG bytes out, cropped to the content — in two cheap passes.

    Declaring a pixel height per card was a guess, and every guess was wrong one of two ways:
    too short and the last row is sliced in half, too tall and a third of the image is dead
    white. So: pass one renders at 1x onto a tall canvas purely to MEASURE where the content
    ends. Pass two renders at 2x to exactly that height. Measuring at 1x matters — a 9000px
    canvas at 2x is a 60 megapixel screenshot, and doing that 66 times is how you turn a six
    minute job into a twenty minute one.
    """
    if not _chrome(): return None
    d = tempfile.mkdtemp()
    f = os.path.join(d, "c.html")
    p1 = os.path.join(d, "m.png"); p2 = os.path.join(d, "c.png")
    with open(f, "w") as fh: fh.write(html)
    try:
        h = None
        if _shot(f, p1, MAXH, 1):
            h = _content_height(p1)
        h = (h + 28) if h else int(height or 2200)      # a breath of margin under the last card
        h = max(500, min(MAXH, h))
        if _shot(f, p2, h, 2):
            with open(p2, "rb") as fh: return fh.read()
    except Exception:
        import traceback
        sys.stderr.write("[shoot] %s\n" % traceback.format_exc())
    return None


def to_pdf(pngs):
    """Every card for a brand and window, in order, as one PDF. Pillow does it; it is free."""
    if not pngs: return None
    try:
        from PIL import Image
    except Exception:
        return None
    import io as _io
    ims = []
    for p in pngs:
        try:
            im = Image.open(_io.BytesIO(p)).convert("RGB")
            # a 3520px wide page is pointless in a PDF. Halve it: same picture, a tenth the file.
            if im.width > 1800:
                im = im.resize((1760, int(im.height * 1760 / im.width)), Image.LANCZOS)
            ims.append(im)
        except Exception:
            continue
    if not ims: return None
    buf = _io.BytesIO()
    ims[0].save(buf, format="PDF", save_all=True, append_images=ims[1:], resolution=110.0)
    return buf.getvalue()


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
      padding:20px 24px 20px;margin-bottom:16px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.ch{display:flex;align-items:center;gap:14px;margin-bottom:16px}
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
td{font-size:20px;font-weight:800;text-align:right;padding:13px 10px;border-top:1px solid #EEF1F5;
   font-variant-numeric:tabular-nums;color:#0F172A}
td.l{text-align:left;vertical-align:top}
td .s{display:block;font-size:14px;font-weight:700;color:#94A3B8;margin-top:6px;
      letter-spacing:.05em;text-transform:uppercase}
td .sn{display:block;font-size:16px;font-weight:600;color:#475569;margin-top:7px;
       text-transform:none;letter-spacing:0}
.ncell{width:430px;overflow:hidden}
.nm{font-size:21px;font-weight:800;letter-spacing:-.3px;color:#0F172A;display:block;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%%}
td .sn{overflow:hidden;text-overflow:ellipsis;max-width:100%%;margin-top:4px;font-size:15px}
td .sn.one{white-space:nowrap}
.bar{width:6px;height:44px;border-radius:4px;display:inline-block;vertical-align:middle;margin-right:14px}
.g{color:#0F7A43}.r{color:#C0392B}.b{color:#1D4ED8}.a{color:#B45309}.m{color:#475569}
.ft{display:flex;font-size:16px;color:#64748B;padding-top:10px;font-weight:600;gap:14px;
    align-items:center}
.ft .rt{margin-left:auto;display:flex;gap:12px;align-items:center}
.ft .fi{width:34px;height:34px;border-radius:10px;background:#F1F5F9;display:flex;
        align-items:center;justify-content:center;flex:0 0 34px}
.ft .fi svg{width:16px;height:16px}
.tag{font-size:14px;font-weight:800;padding:4px 10px;border-radius:7px;letter-spacing:.03em}
.spark{display:block}
.why{font-size:15px;font-weight:600;color:#475569;padding:0 0 12px 20px;line-height:1.45}
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


WSHORT = {"daily": "1d", "3day": "3d", "7day": "7d", "30day": "30d"}


def cell(w, q, k7, lab, key, f, lower=False, win="daily"):
    """THE WINDOW IS THE HEADLINE. The 7 day figure sits under it as the benchmark.
    On a daily card a 'cut this today' decision must be labelled with TODAY's numbers. The week
    is there to tell you whether today is normal, not to stand in for it."""
    a_ = (w or {}).get(key) or 0
    b_ = (q or {}).get(key) or 0
    c7 = (k7 or {}).get(key) or 0
    v = _k(a_) if f == "%s" else (f % r2(a_))
    v7 = _k(c7) if f == "%s" else (f % r2(c7))
    ws = WSHORT.get(win, "1d")
    bench = ("<span class=\"s\">%s · %s &nbsp;·&nbsp; 7d %s</span>" % (lab, ws, v7)) if ws != "7d" \
        else ("<span class=\"s\">%s · 7d</span>" % lab)
    return ('<td>%s<div style="margin-top:5px">%s</div>%s</td>'
            % (v, pill(pct(a_, b_), lower_better=lower) if b_ else
               '<span class="pill nu">new</span>', bench))


def head(icon, title, note=""):
    return ('<div class="ch"><span class="ic">%s</span><h2>%s</h2>%s</div>'
            % (IC.get(icon, IC["grid"]), esc(title),
               ('<span class="note">%s</span>' % esc(note)) if note else ""))


def call(kind, text, icon=None):
    ic = icon or {"c-g": "check", "c-r": "warn", "c-b": "info", "c-a": "warn"}[kind]
    return '<div class="callout %s"><span class="ci">%s</span><div>%s</div></div>' % (
        kind, IC[ic], text)


WINN = {"daily": "yesterday", "3day": "last 3 days", "7day": "last 7 days",
        "30day": "last 30 days"}


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
           ("Out CTR", "octr", "%.2f%%", False, False, "click", "g"),
           ("ATC rate", "atc_rate", "%.1f%%", False, False, "bag", "g")]
    tiles = ""
    for lab, k, f, lb, nu, ic, tn in MET:
        a_, b_ = s.get(k) or 0, p.get(k) or 0
        fv = (lambda v: _k(v)) if f == "%s" else (lambda v: f % r2(v))
        tiles += tile(lab, fv(a_), fv(b_), pct(a_, b_), lower_better=lb, neutral=nu,
                      icon=ic, tint=tn)
    matrix = ('<div class="card">%s<div class="grid g3">%s</div></div>'
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
            ("ATC", "atc_rate", "%.1f%%", False),
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
            # THIS WINDOW is the headline; the 7 day is the benchmark beside it. The LABEL is
            # still a 7 day label, because one day must never be allowed to kill an ad.
            w = r.get("w") or k
            q = (w or {}).get("prev") or {}
            cells = ""
            for lab_, key, f, lb in (("spend", "spend", "%s", False), ("roas", "roas", "%.2fx", False),
                                     ("cpp", "cpa", "%s", True), ("aov", "aov", "%s", False),
                                     ("cvr", "cvr", "%.2f%%", False), ("atc", "atc_rate", "%.1f%%", False),
                                     ("out ctr", "octr", "%.2f%%", False),
                                     ("cpmr", "cpmr", "%s", True), ("freq", "freq", "%.2f", True)):
                cells += cell(w, q, k, lab_, key, f, lower=lb, win=win)
            tr += ('<tr><td class=l><div class="ncell"><span class="bar" style="background:%s"></span>'
                   '<span class="nm">%s</span>'
                   '<span class="sn one">%s &nbsp; %s &nbsp;·&nbsp; confidence %s &nbsp;·&nbsp; %d purchases (7d)</span>'
                   '<span class="sn one">account ROAS %.2fx &nbsp;·&nbsp; the label is a 7 DAY call, the '
                   'numbers are %s</span></div></td>%s'
                   '<td style="width:260px">%s</td></tr>'
                   '<tr><td colspan=10 class="why" style="border:0">%s</td></tr>'
                   % (col, esc(_clip(r["name"], 42)), status(lab), esc(r["kind"]), r["conf"],
                      int(k.get("purch") or 0), r2(acc.get("roas") or 0),
                      esc(WINN.get(win, "this window")), cells, an, tests))
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
def c_budget(A, win):
    """BUDGETS MOVE ON AD SETS. You cannot set a budget on an ad, so this screen never asks."""
    S = az.adset_plan(A, win)
    if not S or (not S["up"] and not S["down"]): return None
    up, dn = S["up"][:4], S["down"][:3]

    board = '<div class="grid g3">%s%s%s</div>' % (
        kpi("Budget to add", "+%s / day" % _k(S["add"]), "up", "g",
            prev="across %d ad sets" % len(up), big=True),
        kpi("Budget to free", "-%s / day" % _k(S["free"]), "down", "b",
            prev="across %d ad sets" % len(dn), big=True),
        kpi("Account ROAS", "%.2fx" % r2(S["acc_roas"]), "trend", "p",
            prev="the bar every ad set is judged against", big=True))
    body = ('<div class="card">%s%s%s</div>'
            % (head("coins", "The budget board, per day",
                    "ad set level, because that is where the budget field is"), board,
               call("c-a", "You cannot set a budget on an ad. Every number below is an <b>AD SET</b> "
                           "daily budget, taken from Meta. Where the campaign is <b>CBO</b> the ad set "
                           "has no budget field and the change is made on the campaign, named on the row.",
                    icon="warn")))

    def srow(r, is_up):
        col = "#0F7A43" if is_up else "#C0392B"
        m, q, m7 = r["m"], r["prev"] or {}, r.get("m7") or {}
        cells = ""
        for lab, key, f, lb in (("roas", "roas", "%.2fx", False), ("cpp", "cpa", "%s", True),
                                ("cvr", "cvr", "%.2f%%", False), ("atc", "atc_rate", "%.1f%%", False),
                                ("freq", "freq", "%.2f", True)):
            cells += cell(m, q, m7, lab, key, f, lower=lb, win=win)
        why = " &nbsp;·&nbsp; ".join(esc(t["t"]) for t in r["tests"] if t["ok"])
        if r["cbo"]:
            budget = ('<td colspan=3><span class="pill am">CBO — budget is on the campaign</span>'
                      '<span class="sn">campaign budget %s / day. Change it on <b>%s</b>.</span></td>'
                      % (_k(r["camp_daily"]) if r["camp_daily"] else "not set",
                         esc(_clip(r["campaign"], 38))))
        else:
            budget = ('<td>%s<span class="s">budget now</span></td>'
                      '<td class="%s">%s<span class="s">set it to</span></td>'
                      '<td class="%s">%s%s<span class="s">%s</span></td>'
                      % (_k(r["cur"]), "b" if is_up else "r", _k(max(0, r["new"])),
                         "g" if is_up else "r",
                         "+" if r["delta"] >= 0 else "-", _k(abs(r["delta"])),
                         "extra spend" if is_up else "freed"))
        return ('<tr><td class=l><div class="ncell">'
                '<span class="bar" style="background:%s"></span>'
                '<span class="nm">%s</span>'
                '<span class="sn one">%s &nbsp; %s &nbsp;·&nbsp; %d ads &nbsp;·&nbsp; %d purchases (7d)</span>'
                '<span class="sn one">CAMPAIGN &nbsp;%s &nbsp;·&nbsp; budget maths off the steady 7 day rate</span>'
                '</div></td>%s%s'
                '<td class="%s">%s%s<span class="s">revenue / day</span></td></tr>'
                '<tr><td colspan=8 class="why" style="border:0"><b>WHY:</b> %s</td></tr>'
                % (col, esc(_clip(r["name"], 40)), status(r["act"]), status(r["label"]),
                   r["n_ads"], int(r["purch"]), esc(_clip(r["campaign"], 40)), budget, cells,
                   "g" if r["inc"] >= 0 else "r",
                   "+" if r["inc"] >= 0 else "-", _k(abs(r["inc"])), why))

    TH = ('<tr><th class=l>Ad set</th><th>Now</th><th>Set to</th><th>Change</th>'
          '<th>ROAS</th><th>CPP</th><th>CVR</th><th>ATC</th><th>Freq</th><th>Revenue</th></tr>')
    if up:
        body += ('<div class="card">%s<table>%s%s</table></div>'
                 % (head("up", "Raise these ad set budgets", "this is the number to change"),
                    TH, "".join(srow(r, True) for r in up)))
    if dn:
        body += ('<div class="card">%s<table>%s%s</table></div>'
                 % (head("down", "Cut these ad set budgets",
                         "what you give up, and what the freed money buys elsewhere"),
                    TH, "".join(srow(r, False) for r in dn)))
    # ---- THE FULL SCORECARD. Every ad set, above or below the account, all metrics.
    # The RAISE / CUT tables above only carry ad sets that trip the strict label. A quiet 2x
    # ad set that is bleeding but not badly enough to trip CUT never appeared. Here it does.
    def scard(rows, above):
        col = "#0F7A43" if above else "#C0392B"
        tr = ""
        for r in rows[:8]:
            m, q, m7 = r["m"], r["prev"] or {}, r.get("m7") or {}
            cells = ""
            for lab, key, f, lb in (("roas", "roas", "%.2fx", False), ("cpp", "cpa", "%s", True),
                                    ("aov", "aov", "%s", False), ("cvr", "cvr", "%.2f%%", False),
                                    ("atc", "atc_rate", "%.1f%%", False),
                                    ("cpmr", "cpmr", "%s", True), ("freq", "freq", "%.2f", True)):
                cells += cell(m, q, m7, lab, key, f, lower=lb, win=win)
            vs = r["vs_acct_pct"]
            gap = ('<td class="%s">%s%s<span class="s">%s / day vs the account</span></td>'
                   % ("g" if r["gap_day"] >= 0 else "r",
                      "+" if r["gap_day"] >= 0 else "-", _k(abs(r["gap_day"])),
                      "earned" if r["gap_day"] >= 0 else "bled"))
            act = ("RAISE" if r["act"] == "RAISE" else
                   ("CUT" if r["act"] in ("REDUCE", "TURN OFF") else
                    ("HOLD" if above else "WATCH — below the account")))
            tr += ('<tr><td class=l><div class="ncell">'
                   '<span class="bar" style="background:%s"></span>'
                   '<span class="nm">%s</span>'
                   '<span class="sn one">%s &nbsp; %s the account (%s%.0f%%) &nbsp;·&nbsp; %d ads &nbsp;·&nbsp; %d purchases 7d</span>'
                   '<span class="sn one">CAMPAIGN &nbsp;%s &nbsp;·&nbsp; budget %s/day</span></div></td>'
                   '%s<td>%s<span class="s">spend %s</span></td>%s</tr>'
                   % (col, esc(_clip(r["name"], 40)), status(act),
                      "beats" if above else "below", "+" if vs >= 0 else "", vs,
                      r["n_ads"], int(r["purch"]), esc(_clip(r["campaign"], 40)),
                      "CBO campaign" if r["cbo"] else _k(r["cur"]),
                      cells, _k((m.get("spend") or 0)), esc(WINN.get(win, "")), gap))
        return tr

    if S.get("bad"):
        body += ('<div class="card">%s<table><tr><th class=l>Ad set · below the account</th>'
                 '<th>ROAS</th><th>CPP</th><th>AOV</th><th>CVR</th><th>ATC</th><th>CPMR</th><th>Freq</th>'
                 '<th>Spend</th><th>vs account</th></tr>%s</table>%s</div>'
                 % (head("down", "Every ad set BELOW the account — cut candidates",
                         "sorted worst first. these are dragging the account down, in order"),
                    scard(S["bad"], False),
                    call("c-r", "These ad sets return under the account's <b>%.2fx</b>. Together they are "
                                "bleeding about <b>%s EGP a day</b> against simply running that spend at "
                                "the account rate. Cut the budget or turn off the worst ads inside them."
                         % (r2(S["acc_roas"]), _k(S.get("bleed") or 0)), icon="warn")))
    if S.get("good"):
        body += ('<div class="card">%s<table><tr><th class=l>Ad set · above the account</th>'
                 '<th>ROAS</th><th>CPP</th><th>AOV</th><th>CVR</th><th>ATC</th><th>CPMR</th><th>Freq</th>'
                 '<th>Spend</th><th>vs account</th></tr>%s</table>%s</div>'
                 % (head("up", "Every ad set ABOVE the account — scale candidates",
                         "sorted best first. these are carrying the account, give them more room"),
                    scard(S["good"], True),
                    call("c-g", "These beat the account's <b>%.2fx</b>. Raise the budget on the ones with "
                                "frequency headroom; the ones already fatiguing need fresh creative, not "
                                "more money." % r2(S["acc_roas"]), icon="check")))

    h = 250 + 300 + (140 + 165 * len(up) if up else 0) + (140 + 165 * len(dn) if dn else 0) + 700
    return page(A, win, "The budget — on the ad sets",
                "Budgets move on ad sets. Every ad set ranked against the account, above and below.",
                body, h)


def c_adverdict(A, win):
    """ADS ARE NOT A BUDGET DECISION. They are a keep or a kill."""
    P = proposals(A, win)
    if not P or (not P["scale"] and not P["cut"]): return None
    good = [r for r in P["scale"] if not r["cat"]][:3]
    bad = P["cut"][:3]
    if not good and not bad: return None

    def arow(r, ok):
        col = "#0F7A43" if ok else "#C0392B"
        k = r["k"]
        act = ("KEEP RUNNING" if ok else
               ("TURN THIS AD OFF" if r["label"] == "CUT" else "REFRESH THE CREATIVE"))
        note = ("Beating the account. To give it more money, duplicate it into an ad set and raise "
                "the budget THERE." if ok else
                ("Switch it off inside its ad set. The budget stays with the ad set and goes to the "
                 "other ads in it." if r["label"] == "CUT" else
                 "The same people are seeing it too often. New creative, or a wider audience."))
        why = " &nbsp;·&nbsp; ".join(esc(t["t"]) for t in r["tests"] if t["ok"])
        w = r.get("w") or k
        q = (w or {}).get("prev") or {}
        cells = ""
        for lab_, key, f, lb in (("spend", "spend", "%s", False), ("roas", "roas", "%.2fx", False),
                                 ("cpp", "cpa", "%s", True), ("cvr", "cvr", "%.2f%%", False),
                                 ("atc", "atc_rate", "%.1f%%", False), ("freq", "freq", "%.2f", True)):
            cells += cell(w, q, k, lab_, key, f, lower=lb, win=win)
        return ('<tr><td class=l><div class="ncell">'
                '<span class="bar" style="background:%s"></span>'
                '<span class="nm">%s</span>'
                '<span class="sn one">%s &nbsp; %s &nbsp;·&nbsp; confidence %s</span>'
                '<span class="sn one">AD SET &nbsp;%s</span></div></td>'
                '%s'
                '<td>%d<span class="s">purchases · 7d</span></td>'
                '<td style="width:340px"><span class="sn" style="margin:0">%s</span></td></tr>'
                '<tr><td colspan=9 class="why" style="border:0"><b>WHY:</b> %s</td></tr>'
                % (col, esc(_clip(r["name"], 40)), status(act), esc(r["kind"]), r["conf"],
                   esc(_clip(safe(k.get("adset") or "(unknown ad set)"), 40)),
                   cells, int(k.get("purch") or 0), esc(note), why))

    TH = ('<tr><th class=l>Ad</th><th>Spend</th><th>ROAS</th><th>CPP</th><th>CVR</th><th>ATC</th>'
          '<th>Freq</th><th>Purchases</th><th class=l>What to do</th></tr>')
    NOTE = ("the big number is %s. the small one under it is the 7 day benchmark. the VERDICT is "
            "always a 7 day call — one day never kills an ad" % WINN.get(win, "this window"))
    body = ""
    if good:
        body += ('<div class="card">%s<table>%s%s</table></div>'
                 % (head("star", "Good ads — keep them running", NOTE),
                    TH, "".join(arow(r, True) for r in good)))
    if bad:
        body += ('<div class="card">%s<table>%s%s</table></div>'
                 % (head("warn", "Bad ads — switch them off", NOTE),
                    TH, "".join(arow(r, False) for r in bad)))
    h = 250 + (140 + 160 * len(good) if good else 0) + (140 + 160 * len(bad) if bad else 0) + 110
    return page(A, win, "The ads — keep or kill",
                "An ad has no budget field. It gets kept, refreshed, or switched off.", body, h)


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
               ("ATC rate", "%.1f%%" % (m.get("atc_rate") or 0), "%.1f%%" % (q.get("atc_rate") or 0),
                pct(m.get("atc_rate") or 0, q.get("atc_rate") or 0), False, False),
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
    """THE CREATIVE SCREEN. How many you launched, how many won, the criteria, the winners by
    name with every metric against the previous period, and the ones that are burning out."""
    LS = az.launch_stats(A, 30)
    P = winning_pattern(A)
    F = fatigue_scan(A)
    if not LS and not P and not F: return None

    body = ""
    if LS:
        body += ('<div class="card">%s<div class="grid g4">%s%s%s%s</div>%s%s</div>'
                 % (head("star", "How many creatives to launch",
                         "counted on ads you actually LAUNCHED in the last 30 days"),
                    kpi("Launched · 30d", "%d" % LS["launched"], "pencil", "b",
                        prev="new ads created in the last 30 days", big=True),
                    kpi("Judged", "%d" % LS["judged"], "rules", "n",
                        prev="%d still gathering data" % LS["pending"], big=True),
                    kpi("Winners", "%d" % LS["winners"], "star", "g",
                        prev="%d cleared %.2fx  ·  %d only matched the account" % (
                            LS["winners"], r2(LS["bar"]), LS["near"]), big=True),
                    kpi("Hit rate", "%.0f%%" % LS["hr"], "target", "p",
                        prev="launch %d to get 1  ·  %d to get 3" % (LS["need_1"], LS["need_3"]),
                        big=True),
                    call("c-b",
                         "<b>THE CRITERIA, IN FULL.</b> An ad is <b>JUDGED</b> once it has spent at least "
                         "<b>%s EGP</b> and taken at least <b>%d purchases</b> inside the 30 days. "
                         "It is a <b>WINNER</b> only if its ROAS is at least <b>%d%% above the account "
                         "average</b> — the account ran <b>%.2fx</b> over those 30 days, so the winner "
                         "bar is <b>%.2fx</b>. Beating the account by 1%% is not a winner, it is a "
                         "rounding error, and <b>%d ad(s) landed in exactly that gap</b> this month. "
                         "Catalogue and DPA are excluded from both sides: a product feed is not a "
                         "creative you shot."
                         % (_k(LS["min_spend"]), LS["min_pur"], LS["margin"],
                            r2(LS["acc_roas"]), r2(LS["bar"]), LS["near"]), icon="rules"),
                    call("c-a",
                         "At a <b>%.0f%%</b> hit rate you must launch <b>%d</b> new creatives for the next "
                         "winner and <b>%d</b> for three. You launched <b>%d</b> in the last 30 days."
                         % (LS["hr"], LS["need_1"], LS["need_3"], LS["launched"]), icon="warn")))

        # ---- THE BEST CREATIVES OF THE LAST 30 DAYS, BY NAME, WITH THE PREVIOUS PERIOD.
        def crow(k, good):
            col = "#0F7A43" if good else "#C0392B"
            q = k.get("prev") or {}
            cells = ""
            for lab, key, f, lb in (("spend", "spend", "%s", False), ("revenue", "rev", "%s", False),
                                    ("roas", "roas", "%.2fx", False), ("cpp", "cpa", "%s", True),
                                    ("aov", "aov", "%s", False), ("cvr", "cvr", "%.2f%%", False),
                                    ("atc", "atc_rate", "%.1f%%", False),
                                    ("cpmr", "cpmr", "%s", True), ("freq", "freq", "%.2f", True)):
                a_, b_ = k.get(key) or 0, q.get(key) or 0
                v = _k(a_) if f == "%s" else (f % r2(a_))
                cells += ('<td>%s<div style="margin-top:5px">%s</div>'
                          '<span class="s">%s · 30d</span></td>'
                          % (v, pill(pct(a_, b_), lower_better=lb) if b_ else
                             '<span class="pill nu">new</span>', lab))
            born = ((LS.get("created") or {}).get(str(k.get("ad_id"))) or {}).get("created") or ""
            return ('<tr><td class=l><div class="ncell">'
                    '<span class="bar" style="background:%s"></span>'
                    '<span class="nm">%s</span>'
                    '<span class="sn one">%s &nbsp;·&nbsp; %s &nbsp;·&nbsp; launched %s</span>'
                    '<span class="sn one">%s</span></div></td>%s</tr>'
                    % (col, esc(_clip(safe(k.get("ad_name") or ""), 40)),
                       status("WINNER" if good else "LOSER"), esc(k.get("type") or ""),
                       esc(born or "n/a"),
                       esc(_clip(safe(k.get("campaign") or ""), 44)), cells))

        TH = ('<tr><th class=l>Creative · last 30 days</th><th>Spend</th><th>Revenue</th><th>ROAS</th>'
              '<th>CPP</th><th>AOV</th><th>CVR</th><th>ATC</th><th>CPMR</th><th>Freq</th></tr>')
        if LS["best"]:
            body += ('<div class="card">%s<table>%s%s</table></div>'
                     % (head("target", "The winning creatives — last 30 days, by name",
                             "cleared %.2fx (%d%% above the account's %.2fx) on %s+ spend and %d+ purchases"
                             % (r2(LS["bar"]), LS["margin"], r2(LS["acc_roas"]),
                                _k(LS["min_spend"]), LS["min_pur"])),
                        TH, "".join(crow(k, True) for k in LS["best"])))
        if LS["worst"]:
            body += ('<div class="card">%s<table>%s%s</table></div>'
                     % (head("down", "The losing creatives — last 30 days",
                             "judged, but never cleared the %.2fx winner bar" % r2(LS["bar"])),
                        TH, "".join(crow(k, False) for k in LS["worst"])))

    # ---- WHAT SEPARATES THEM
    if P:
        Wn, Ls = P["W"], P["L"]
        for d in (Wn, Ls):
            for kk in ("hold", "r50", "r75"):
                if (d.get(kk) or 0) > 100: d[kk] = None
        MET = [("Hook rate", "hook", "%.1f%%"), ("Hold", "hold", "%.0f%%"),
               ("Outbound CTR", "octr", "%.2f%%"), ("CVR", "cvr", "%.2f%%"),
               ("ATC rate", "atc_rate", "%.1f%%"), ("Frequency", "freq", "%.2f"),
               ("ROAS", "roas", "%.2fx"), ("AOV", "aov", "%s"), ("CPMR", "cpmr", "%s")]
        tiles = ""
        for lab, k, f in MET:
            w_, l_ = Wn.get(k), Ls.get(k)
            sw = (_k(w_) if f == "%s" else (f % w_)) if w_ else "n/a"
            sl = (_k(l_) if f == "%s" else (f % l_)) if l_ else "n/a"
            lb = k in ("freq", "cpa", "cpmr")
            good = w_ is not None and l_ is not None and ((w_ < l_) if lb else (w_ > l_))
            tiles += ('<div class="tile"><div class="kh">%s<span class="lb">%s</span></div>'
                      '<div class="kv"><span class="v %s">%s</span>%s</div>'
                      '<div class="prev">losers %s</div></div>'
                      % (gi("target", "g" if good else "n"), esc(lab),
                         "g" if good else "", esc(sw), better(w_, l_, lower_better=lb), esc(sl)))
        body += ('<div class="card">%s<div class="grid g4">%s</div>%s</div>'
                 % (head("target", "What the winners have in common",
                         "winners vs the shot creative that lost to the account"), tiles,
                    call("c-b", "Winners are the <b>%d</b> creative ads that beat the account's "
                                "<b>%.2fx over the last 7 days</b> on at least 3 purchases. Losers are "
                                "the <b>%d</b> that did not. No catalogue on either side."
                         % (P["n_win"], r2(P["acc_roas"]), len(P["losers"])), icon="info")))
        body += ('<div class="card">%s%s</div>'
                 % (head("pencil", "The brief · give this to the editor"),
                    "".join(call("c-g", esc(l)) for l in brief_lines(P)[:5])))

    # ---- AND THE ONES THAT ARE BURNING OUT
    tired = [r for r in F if r["state"] in ("FATIGUED", "SATURATED", "FATIGUING")][:5]
    if tired:
        tr = ""
        for r in tired:
            k = r["k"]; vid = k.get("type") == "VIDEO"
            col = {"FATIGUED": "#C0392B", "SATURATED": "#C0392B",
                   "FATIGUING": "#B45309"}[r["state"]]
            cells = ""
            for lab, v, lb in (("hook Δ", r["d_hook"] if vid else None, False),
                               ("out ctr Δ", r["d_octr"], False),
                               ("cpm Δ", r["d_cpm"], True), ("cpp Δ", r["d_cpp"], True)):
                cells += '<td>%s<span class="s">%s · 7d vs prior 7d</span></td>' % (
                    pill(v, lower_better=lb), lab)
            tr += ('<tr><td class=l><div class="ncell">'
                   '<span class="bar" style="background:%s"></span>'
                   '<span class="nm">%s</span>'
                   '<span class="sn one">%s &nbsp; %d of 5 tests fired</span>'
                   '<span class="sn one">frequency <b>%.2f</b> (7d) · hook %s · hold %s</span></div></td>'
                   '<td>%s<span class="s">spend · 7d</span></td>'
                   '<td>%.2fx<span class="s">roas · 7d</span></td>%s</tr>'
                   % (col, esc(_clip(r["name"], 40)), status(r["state"]), r["hits"], r["freq"],
                      ("%.1f%%" % r["hook"]) if vid else "n/a",
                      ("%.0f%%" % r["hold"]) if vid else "n/a",
                      _k(r["spend"]), r2(r["roas"]), cells))
        body += ('<div class="card">%s<table>%s</table>%s</div>'
                 % (head("clock", "Creatives that are burning out — replace these first",
                         "these are the slots your new creatives are for"), tr,
                    call("c-r", "FATIGUED = 3+ of the five tests fire and frequency is over %.1f, or "
                                "frequency is over %.1f with 2 firing. SATURATED = frequency over %.1f "
                                "on its own. FATIGUING = 2 fire."
                         % (FATG["freq"], FATG["sat"], FATG["sat"]), icon="warn")))

    return page(A, win, "Creative — what to make next",
                "How many you launched, how many won, the criteria, the winners by name, and what "
                "is burning out.", body, 0)


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


# ---------------------------------------------------------------- NEW LAUNCHES
def c_launches(A, win):
    """Every ad born in the last 3 days, and how it is doing since. A launch nobody follows is
    a launch nobody made."""
    L = az.new_launches(A, 3)
    if not L: return None
    if not L["rows"]:
        body = ('<div class="card">%s%s</div>'
                % (head("pencil", "New launches", "ads created in the last 3 days"),
                   call("c-b", "<b>No new ads were created in the last 3 days.</b> The creative "
                               "pipeline is empty, and an account with no new creative is an account "
                               "waiting for its winners to fatigue.", icon="warn")))
        return page(A, win, "New launches — the last 3 days",
                    "Every ad born in the last 3 days, followed daily until it earns a verdict.",
                    body, 0)

    board = '<div class="grid g4">%s%s%s%s</div>' % (
        kpi("Launched · 3d", "%d" % L["n"], "pencil", "b",
            prev="new ads created in the last 3 days", big=True),
        kpi("Winning", "%d" % L["win"], "star", "g",
            prev="already beating the account's %.2fx" % r2(L["acc_roas"]), big=True),
        kpi("Failing", "%d" % L["fail"], "warn", "r",
            prev="under 60%% of the account", big=True),
        kpi("Still learning", "%d" % L["early"], "clock", "n",
            prev="not enough data to judge yet", big=True))
    body = ('<div class="card">%s%s%s</div>'
            % (head("pencil", "New launches", "ads created in the last 3 days, followed daily"),
               board,
               call("c-b", "An ad is only <b>judged</b> once it clears <b>%s spend</b> and <b>%d "
                           "purchases</b>. Before that it reads TOO EARLY or LEARNING, and that is not "
                           "a hedge — killing an ad on day one is how you never find a winner."
                    % (_k(az.EVIDENCE["spend"]), L["min_pur"]), icon="rules")))

    COL = {"WINNING": "#0F7A43", "BEHIND": "#B45309", "FAILING": "#C0392B",
           "LEARNING": "#475569", "TOO EARLY": "#94A3B8"}
    tr = ""
    for r in L["rows"][:10]:
        k = r["k"]; q = k.get("prev") or {}
        cells = ""
        for lab, key, f, lb in (("spend", "spend", "%s", False), ("revenue", "rev", "%s", False),
                                ("roas", "roas", "%.2fx", False), ("cpp", "cpa", "%s", True),
                                ("aov", "aov", "%s", False), ("cvr", "cvr", "%.2f%%", False),
                                ("atc", "atc_rate", "%.1f%%", False),
                                ("cpmr", "cpmr", "%s", True), ("freq", "freq", "%.2f", True)):
            a_, b_ = k.get(key) or 0, q.get(key) or 0
            v = _k(a_) if f == "%s" else (f % r2(a_))
            cells += ('<td>%s<div style="margin-top:5px">%s</div><span class="s">%s</span></td>'
                      % (v, pill(pct(a_, b_), lower_better=lb) if b_ else
                         '<span class="pill nu">first run</span>', lab))
        sp = spark(r["series"], "rev", 200, 50) if len(r["series"]) >= 4 else ""
        tr += ('<tr><td class=l><div class="ncell">'
               '<span class="bar" style="background:%s"></span>'
               '<span class="nm">%s</span>'
               '<span class="sn one">%s &nbsp;·&nbsp; day %d &nbsp;·&nbsp; born %s</span>'
               '<span class="sn one">%s</span></div></td>%s'
               '<td style="width:240px">%s<span class="s">since launch</span></td></tr>'
               '<tr><td colspan=11 class="why" style="border:0"><b>READ:</b> %s</td></tr>'
               % (COL[r["state"]], esc(_clip(r["name"], 40)), status(r["state"]), r["age"],
                  esc(r["born"]), esc(_clip(safe(k.get("adset") or ""), 44)), cells, sp,
                  esc(r["why"])))
    body += ('<div class="card">%s'
             '<table><tr><th class=l>Ad · born in the last 3 days</th><th>Spend</th><th>Revenue</th>'
             '<th>ROAS</th><th>CPP</th><th>AOV</th><th>CVR</th><th>ATC</th><th>CPMR</th><th>Freq</th>'
             '<th>Since launch</th></tr>%s</table></div>'
             % (head("target", "Follow up on every one",
                     "every metric against the same ad's own previous period"), tr))
    return page(A, win, "New launches — the last 3 days",
                "Every ad born in the last 3 days, followed daily until it earns a verdict.",
                body, 0)


# ---------------------------------------------------------------- driver
# TOP DOWN. Account, then campaigns, then ad sets, then ads, then what to do with the money,
# then the creative, then the sentences. You cannot judge an ad before you know whether the
# account moved, and you cannot judge a campaign before you know whether the account moved.
CARDS = (("1-account",     c_exec),
         ("2-campaigns",   lambda A, w: c_cause(A, w, "campaign")),
         ("3-adsets",      lambda A, w: c_cause(A, w, "adset")),
         ("4-ads",         lambda A, w: c_cause(A, w, "ad")),
         ("5-verdicts",    c_winners),
         ("6-budget",      c_budget),
         ("7-adverdicts",  c_adverdict),
         ("8-audience",    c_audience),
         ("9-fatigue",     c_fatigue),
         ("10-makemore",   c_makemore),
         ("11-observations", c_observations))


def render_one(A, win, fn):
    """One card, on demand. The launch feed is not part of the daily deck."""
    try:
        r = fn(A, win)
        if not r: return None
        return shoot(r[0], r[1])
    except Exception:
        import traceback
        sys.stderr.write("[html] render_one failed:\n%s\n" % traceback.format_exc())
        return None


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
