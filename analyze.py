#!/usr/bin/env python3
"""
AI MEDIA BUYER v4  |  Senior Meta Ads Growth Operator.

Runs on GitHub Actions (free cron) with ONLY a Meta token + Slack bot token.
Each account posts to its OWN channels:
  #meta-ourkids / #meta-playmore     compact daily action digest + 3-day pulse
  #ourkids-launches / #playmore-launches   new-launch performance by adset + best ad

Design: short, scannable, action-first. Ad names are hyperlinks straight to the
ad in Ads Manager. Every message states its exact time window. A winner must beat
the account cold ROAS, not a weak floor. Never a lone metric.

House style: line break after every period, no em dashes, CTR = Outbound CTR.
"""
import os, sys, json, time, argparse, datetime, math, statistics as st
import urllib.request, urllib.parse, urllib.error

GRAPH = "https://graph.facebook.com/v21.0"
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
DATA_PATH = os.path.join(DOCS, "data.json")
TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "").strip()

def load_json(p, d):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return d
def save_json(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: json.dump(o, f, ensure_ascii=False, indent=2)

CONFIG = load_json(os.path.join(ROOT, "config.json"), {})
TH = CONFIG.get("thresholds", {})
SL = CONFIG.get("slack", {})
CH = SL.get("channels", {})
MENTION = "<@%s>" % SL.get("mention_user_id", "") if SL.get("mention_user_id") else "@Ahmed"
TZ = CONFIG.get("timezone", "Africa/Cairo")

MIN_SPEND = TH.get("winner_min_spend", 1500)
MIN_PUR = TH.get("winner_min_purchases", 5)
ROAS_T = TH.get("roas_target", 2.0)
FAT_FREQ = TH.get("fatigue_freq_increase", 30)
FAT_CTR = TH.get("fatigue_ctr_drop", 20)
AN_ROAS = TH.get("anomaly_roas_drop", 25)
AN_CPA = TH.get("anomaly_cpa_spike", 30)

WARM_KW = ["retarget", " rt ", "rt_", "catalog", "dpa", "promocode", "promo code",
           "didn't purchase", "didnt purchase", "back2cart", "atc ", "atc_", "zombie",
           "existing", "evergreen", "ever green", "abandon", "viewed", "add to cart", "savewith"]
CAT_KW = ["catalog", "dpa"]
# ---------- audience segments (New / Engaged / Existing) ----------
# TRUTH SOURCE: the ad set's actual targeting (which custom audiences it targets), NOT the ad/campaign name.
# Names lie: "Existing Content" is a CREATIVE naming convention, not an existing-customer audience.
SEGN = {"NEW": "New audience", "ENGAGED": "Engaged", "EXISTING": "Existing customers"}
SEGMAP = {}   # adset_id -> "NEW"/"ENGAGED"/"EXISTING", rebuilt per account from targeting
WIN_TITLE = {"daily": "DAILY MEMO", "3day": "3-DAY MEMO", "7day": "7-DAY MEMO"}
WIN_FOOT = {"daily": "Daily memo. Yesterday vs the day before. One day is noisy, treat it as a signal, not a verdict.",
            "3day": "3-day memo. Last 3 days vs the 3 before.",
            "7day": "7-day memo. Last 7 days vs the 7 before."}

def ca_segment(name, subtype=""):
    """Classify one custom audience. A lookalike is prospecting (NEW), not the seed audience."""
    n = (name or "").lower()
    if (subtype or "").upper() == "LOOKALIKE" or "lookalike" in n or n.startswith("lal"):
        return "NEW"
    # "wsv 30 day didn't purchase" / "atc 60 didn't pur 30" are ENGAGERS, never customers
    if "didn't pur" in n or "didnt pur" in n or "not purchas" in n or "non purchas" in n:
        return "ENGAGED"
    if any(k in n for k in ("purchase", "purchaser", "buyer", "customer", " ltv", "repeat", "loyal")):
        return "EXISTING"
    return "ENGAGED"   # any other custom audience (engagers, viewers, ATC, ICO, site visitors) is a warm pool

# name-based fallback, ONLY used when an ad set has no resolvable targeting
EXIST_KW = ["existing customer", "past buyer", "repeat", "loyal", " ltv"]
ENGAGED_KW = ["retarget", " rt ", "rt_", "atc", "add to cart", "back2cart", "didn't purchase", "didnt purchase",
              "abandon", "viewed", "view content", "engag", "zombie", "savewith", "cart", "wsv", "ico"]
def segment(blob):
    b = " " + (blob or "").lower() + " "
    # strip creative-naming false positives before looking for audience words
    for junk in ("existing content", "existing posts", "existing post"):
        b = b.replace(junk, " ")
    if any(k in b for k in ENGAGED_KW): return "ENGAGED"
    if any(k in b for k in EXIST_KW): return "EXISTING"
    return "NEW"

def _match(name, keys):
    n = (name or "").lower()
    return [(k, v) for k, v in CH.items() if k in keys and v]
def channel_for(name):
    n = (name or "").lower()
    if "playmore" in n: return CH.get("playmore") or CH.get("default")
    if "kids" in n: return CH.get("ourkids") or CH.get("default")
    return CH.get("default")
def channel_launch_for(name):
    n = (name or "").lower()
    if "playmore" in n: return CH.get("playmore_launch")
    if "kids" in n: return CH.get("ourkids_launch")
    return None
def channel_advisor_for(name):
    n = (name or "").lower()
    if "playmore" in n: return CH.get("playmore_advisor")
    if "kids" in n: return CH.get("ourkids_advisor")
    return None
def channel_3sec_for(name):
    n = (name or "").lower()
    if "playmore" in n: return CH.get("playmore_3sec")
    if "kids" in n: return CH.get("ourkids_3sec")
    return None
def channel_window_for(name, win):
    """win = daily | 3day | 7day. Each brand has its own channel per read."""
    n = (name or "").lower()
    brand = "playmore" if "playmore" in n else ("ourkids" if "kids" in n else None)
    if not brand: return None
    return CH.get("%s_%s" % (brand, win))

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def money(v): return "{:,.0f}".format(v) if v is not None else "n/a"
def pct(new, old):
    if not old: return None
    return (new - old) / old * 100.0
def sp(x): return "n/a" if x is None else ("+" if x >= 0 else "") + "{:.0f}%".format(x)
def bw(gap):
    if gap is None: return "n/a"
    return "%d%% under group" % abs(gap) if gap <= 0 else "+%d%% over group" % gap
def fmt_day(d): return d.strftime("%b ") + str(d.day)
def vs_avg(ref, pct=False, is_money=False):
    """Raw side-by-side vs the account average. House rule: never percentage points, show both numbers."""
    if ref is None: return ""
    v = money(ref) if is_money else ("%s%%" % ref if pct else str(ref))
    return " vs %s avg" % v

DATES = {}
NUMID = ""  # numeric ad-account id for building Ads Manager links, set per account
STATUS = {}  # {ad_id: effective_status} for the account being rendered
STAT = {"ACTIVE": "ACTIVE", "PAUSED": "PAUSED", "ADSET_PAUSED": "ADSET PAUSED",
        "CAMPAIGN_PAUSED": "CAMPAIGN PAUSED", "IN_PROCESS": "IN REVIEW", "PENDING_REVIEW": "IN REVIEW",
        "PENDING_BILLING_INFO": "BILLING ISSUE", "DISAPPROVED": "DISAPPROVED", "WITH_ISSUES": "WITH ISSUES",
        "ARCHIVED": "ARCHIVED", "DELETED": "DELETED", "PREAPPROVED": "APPROVED"}
def statuslabel(c):
    s = c.get("status") or STATUS.get(c.get("ad_id"))
    return STAT.get(s, s or "UNKNOWN")
def safe(s): return (s or "").replace("|", " ").replace("<", " ").replace(">", " ").strip()[:70]
def nm(c):
    name = safe(c.get("ad_name"))
    if NUMID and c.get("ad_id"):
        u = "https://business.facebook.com/adsmanager/manage/ads?act=%s&selected_ad_ids=%s" % (NUMID, c["ad_id"])
        return "<%s|%s>" % (u, name or "(unnamed)")
    return "*%s*" % (name or "(unnamed)")
def aset_link(name, sid):
    name = safe(name)
    if NUMID and sid:
        u = "https://business.facebook.com/adsmanager/manage/adsets?act=%s&selected_adset_ids=%s" % (NUMID, sid)
        return "<%s|%s>" % (u, name or "(adset)")
    return "*%s*" % (name or "(adset)")


# ----------------------- Graph API -----------------------
def api_get(path, params):
    params = dict(params); params["access_token"] = TOKEN
    url = "%s/%s?%s" % (GRAPH, path, urllib.parse.urlencode(params))
    last = ""
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e.read().decode("utf-8", "ignore"); low = last.lower()
            if (e.code in (429, 500, 502, 503) or "throttl" in low or "reduce the amount" in low) and i < 4:
                time.sleep(min(90, 2 ** i * 6)); continue
            sys.stderr.write("[api] %s %s: %s\n" % (e.code, path, last[:200])); return {"error": last}
        except Exception as e:
            last = str(e); time.sleep(2 ** i)
    return {"error": last}

def get_accounts():
    cfg = CONFIG.get("accounts", {})
    if cfg.get("mode") == "manual":
        return [{"id": a["id"] if str(a["id"]).startswith("act_") else "act_" + str(a["id"]),
                 "name": a.get("name", ""), "currency": a.get("currency", "")} for a in cfg.get("include", [])]
    out, after = [], None
    while True:
        p = {"fields": "name,account_status,currency", "limit": 200}
        if after: p["after"] = after
        d = api_get("me/adaccounts", p)
        if "error" in d: break
        for a in d.get("data", []):
            if a.get("account_status") == 1:
                out.append({"id": a["id"], "name": a.get("name", a["id"]), "currency": a.get("currency", "")})
        after = d.get("paging", {}).get("cursors", {}).get("after")
        if not after or not d.get("data"): break
    return out

AD_FIELDS = ",".join(["ad_id", "ad_name", "campaign_name", "adset_name", "adset_id", "objective",
    "spend", "impressions", "reach", "frequency", "cpm", "ctr", "inline_link_click_ctr",
    "outbound_clicks_ctr", "actions", "action_values", "video_play_actions",
    "video_p25_watched_actions", "video_p50_watched_actions", "video_p75_watched_actions",
    "video_p95_watched_actions", "video_p100_watched_actions", "video_thruplay_watched_actions"])
LITE_FIELDS = "spend,reach,purchase_roas,actions,action_values"

def get_insights(acct, tr, level="ad", fields=AD_FIELDS, extra=""):
    out, after = [], None
    ff = fields + ("," + extra if extra else "")
    while True:
        p = {"level": level, "fields": ff, "time_range": json.dumps(tr), "limit": 400}
        if after: p["after"] = after
        d = api_get("%s/insights" % acct, p)
        if "error" in d: break
        out += d.get("data", [])
        after = d.get("paging", {}).get("cursors", {}).get("after")
        if not after: break
    return out

def get_ad_statuses(acct, max_pages=12):
    """{ad_id: effective_status} for every ad in the account (delivery state)."""
    out, after, pages = {}, None, 0
    while pages < max_pages:
        p = {"fields": "id,effective_status", "limit": 500}
        if after: p["after"] = after
        d = api_get("%s/ads" % acct, p)
        if "error" in d: break
        for a in d.get("data", []): out[a["id"]] = a.get("effective_status")
        after = d.get("paging", {}).get("cursors", {}).get("after"); pages += 1
        if not after: break
    return out

def get_custom_audiences(acct, max_pages=8):
    """{custom_audience_id: NEW|ENGAGED|EXISTING} for the account."""
    out, after, pages = {}, None, 0
    while pages < max_pages:
        p = {"fields": "id,name,subtype", "limit": 200}
        if after: p["after"] = after
        d = api_get("%s/customaudiences" % acct, p)
        if "error" in d: break
        for a in d.get("data", []):
            out[str(a.get("id"))] = ca_segment(a.get("name"), a.get("subtype"))
        after = d.get("paging", {}).get("cursors", {}).get("after"); pages += 1
        if not after: break
    return out

def build_segmap(acct, max_pages=12):
    """adset_id -> audience segment, read from the ad set's REAL targeting.
    No custom audience on the ad set = broad / interest / lookalike prospecting = NEW."""
    CA = get_custom_audiences(acct)
    out, after, pages = {}, None, 0
    while pages < max_pages:
        p = {"fields": "id,name,targeting", "limit": 200}
        if after: p["after"] = after
        d = api_get("%s/adsets" % acct, p)
        if "error" in d: break
        for a in d.get("data", []):
            t = a.get("targeting") or {}
            cas = t.get("custom_audiences") or []
            segs = []
            for x in cas:
                s = CA.get(str(x.get("id")))
                if not s and x.get("name"): s = ca_segment(x.get("name"))
                if s: segs.append(s)
            if "EXISTING" in segs:  s = "EXISTING"
            elif "ENGAGED" in segs: s = "ENGAGED"
            elif segs:              s = "NEW"      # lookalike-only = prospecting
            else:                   s = "NEW"      # no custom audience at all = broad/interest = prospecting
            out[str(a.get("id"))] = s
        after = d.get("paging", {}).get("cursors", {}).get("after"); pages += 1
        if not after: break
    return out

def get_adset_targeting(acct, max_pages=8):
    """Active adsets with their targeting, for audience-overlap analysis."""
    out, after, pages = [], None, 0
    while pages < max_pages:
        p = {"fields": "id,name,effective_status,targeting", "limit": 200}
        if after: p["after"] = after
        d = api_get("%s/adsets" % acct, p)
        if "error" in d: break
        for a in d.get("data", []):
            if a.get("effective_status") == "ACTIVE": out.append(a)
        after = d.get("paging", {}).get("cursors", {}).get("after"); pages += 1
        if not after: break
    return out

def _tsig(t):
    t = t or {}; geo = t.get("geo_locations", {}) or {}
    ca = tuple(sorted(str(x.get("id")) for x in (t.get("custom_audiences") or [])))
    ints = set()
    for fs in (t.get("flexible_spec") or []):
        for k in ("interests", "behaviors"):
            for it in (fs.get(k) or []): ints.add(str(it.get("id")))
    return {"countries": tuple(sorted(geo.get("countries", []) or [])),
            "age": (t.get("age_min"), t.get("age_max")), "genders": tuple(t.get("genders", []) or []),
            "ca": ca, "ints": tuple(sorted(ints)), "broad": (not ca and not ints)}

def _age_overlap(a, b):
    lo = max(a[0] or 13, b[0] or 13); hi = min(a[1] or 65, b[1] or 65); return lo <= hi

def audience_overlap(adsets, spend_by):
    rows = [(a["id"], a.get("name", ""), _tsig(a.get("targeting")), spend_by.get(a["id"], 0)) for a in adsets]
    rows = [r for r in rows if r[3] >= MIN_SPEND * 0.3]
    pairs = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            sa, sb = rows[i][2], rows[j][2]
            shared = set(sa["ca"]) & set(sb["ca"])
            if shared: why = "share a custom audience"
            elif sa["broad"] and sb["broad"] and sa["countries"] == sb["countries"] and _age_overlap(sa["age"], sb["age"]) and sa["genders"] == sb["genders"]:
                why = "both broad, same geo, age and gender"
            elif sa["ints"] and (set(sa["ints"]) & set(sb["ints"])) and sa["countries"] == sb["countries"]:
                why = "share interests and geo"
            else: continue
            pairs.append((rows[i][3] + rows[j][3], rows[i][1], rows[j][1], why))
    pairs.sort(reverse=True)
    return pairs[:5]

def get_new_ad_ids(acct, since_date, max_pages=6):
    """Ad ids created on/after since_date (YYYY-MM-DD). Filtered client-side."""
    ids, after, pages = {}, None, 0
    while pages < max_pages:
        p = {"fields": "id,name,created_time,adset_id,adset_name", "limit": 300}
        if after: p["after"] = after
        d = api_get("%s/ads" % acct, p)
        if "error" in d: break
        for a in d.get("data", []):
            ct = (a.get("created_time") or "")[:10]
            if ct and ct >= since_date:
                ids[a["id"]] = {"adset": a.get("adset_name", ""), "adset_id": a.get("adset_id"), "created": ct}
        after = d.get("paging", {}).get("cursors", {}).get("after")
        pages += 1
        if not after: break
    return ids


# ----------------------- metrics -----------------------
def pick(rows, ordered):
    if not rows: return 0.0
    d = {a.get("action_type"): a.get("value") for a in rows}
    for t in ordered:
        if t in d: return f(d[t])
    return 0.0
def first(rows): return f(rows[0].get("value")) if rows else 0.0
PURCH = ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"]

ATC = ["add_to_cart", "omni_add_to_cart", "offsite_conversion.fb_pixel_add_to_cart"]

def metric(r):
    spend = f(r.get("spend")); impr = f(r.get("impressions")); reach = f(r.get("reach"))
    purch = pick(r.get("actions"), PURCH); rev = pick(r.get("action_values"), PURCH)
    lc = pick(r.get("actions"), ["link_click"]); atc = pick(r.get("actions"), ATC)
    v3 = first(r.get("video_play_actions")); p25 = first(r.get("video_p25_watched_actions"))
    p50 = first(r.get("video_p50_watched_actions")); p75 = first(r.get("video_p75_watched_actions"))
    p95 = first(r.get("video_p95_watched_actions")); p100 = first(r.get("video_p100_watched_actions"))
    thru = first(r.get("video_thruplay_watched_actions"))
    octr = first(r.get("outbound_clicks_ctr")) or f(r.get("inline_link_click_ctr"))
    name = r.get("ad_name", ""); camp = r.get("campaign_name", ""); adset = r.get("adset_name", "")
    blob = (" %s %s %s " % (name, camp, adset)).lower()
    warm = any(k in blob for k in WARM_KW)
    if any(k in blob for k in CAT_KW): typ = "CATALOGUE"
    elif p25 > impr * 0.02: typ = "VIDEO"
    else: typ = "IMAGE"
    # audience comes from the AD SET'S REAL TARGETING. Names are only a last resort.
    seg = SEGMAP.get(str(r.get("adset_id"))) or segment(blob)
    return {"ad_id": r.get("ad_id"), "ad_name": name, "campaign": camp, "adset": adset, "adset_id": r.get("adset_id"),
            "aud": "WARM" if warm else "COLD", "type": typ, "seg": seg,
            "spend": round(spend, 2), "impr": int(impr), "reach": int(reach),
            "freq": round(f(r.get("frequency")) or (impr / reach if reach else 0), 2),
            "cpm": round(f(r.get("cpm")), 2), "cpmr": round(spend / reach * 1000, 2) if reach else 0.0,
            "ctr": round(f(r.get("ctr")), 2), "octr": round(octr, 2),
            "purch": round(purch, 1), "rev": round(rev, 2), "lc": round(lc, 1), "atc": round(atc, 1),
            "cpa": round(spend / purch, 2) if purch else None,
            "roas": round(rev / spend, 2) if spend else 0.0,
            "aov": round(rev / purch, 2) if purch else None,
            "cvr": round(purch / lc * 100, 2) if lc else 0.0,
            "cpc": round(spend / lc, 2) if lc else 0.0,
            "atc_rate": round(atc / lc * 100, 1) if lc else 0.0,
            "hook": round(v3 / impr * 100, 1) if impr else 0.0,
            "hold": round(p25 / impr * 100, 1) if impr else 0.0,
            "v3": round(v3), "thru": round(thru),
            # retention as % of impressions (reliable), how far people actually watch
            "r25": round(p25 / impr * 100, 1) if impr else 0, "r50": round(p50 / impr * 100, 1) if impr else 0,
            "r75": round(p75 / impr * 100, 1) if impr else 0, "r95": round(p95 / impr * 100, 1) if impr else 0,
            "r100": round(p100 / impr * 100, 1) if impr else 0}

def med(xs): return round(st.median(xs), 2) if xs else None

def benchmarks(rows):
    B = {}
    for s in set("%s/%s" % (c["aud"], c["type"]) for c in rows):
        g = [c for c in rows if "%s/%s" % (c["aud"], c["type"]) == s and c["cpa"] and c["spend"] >= 500]
        if len(g) >= 2:
            B[s] = {"cpa_med": med([c["cpa"] for c in g]), "roas_med": med([c["roas"] for c in g]),
                    "cpmr_med": med([c["cpmr"] for c in g]), "cvr_med": med([c["cvr"] for c in g]),
                    "hook_med": med([c["hook"] for c in g]), "hold_med": med([c["hold"] for c in g]), "n": len(g)}
    return B

def label(c, B, prev, cold_roas):
    seg = "%s/%s" % (c["aud"], c["type"]); b = B.get(seg)
    p = prev.get(c["ad_id"]) if prev else None
    c["d_roas"] = pct(c["roas"], p["roas"]) if p else None
    c["d_cpa"] = pct(c["cpa"], p["cpa"]) if (p and p.get("cpa") and c["cpa"]) else None
    c["d_freq"] = pct(c["freq"], p["freq"]) if p else None
    c["d_octr"] = pct(c["octr"], p["octr"]) if p else None
    c["d_cpm"] = pct(c["cpm"], p["cpm"]) if p else None
    c["prev"] = {k: p.get(k) for k in ("cpa", "roas", "freq", "octr", "spend", "cpm", "cpmr", "rev", "reach", "purch", "cvr", "aov", "impr", "lc", "ctr")} if p else None
    if c["spend"] >= MIN_SPEND and p and p.get("freq") and p.get("octr") \
       and c["d_freq"] is not None and c["d_freq"] > FAT_FREQ \
       and c["d_octr"] is not None and c["d_octr"] < -FAT_CTR:
        return "FATIGUE"
    if (c["spend"] < MIN_SPEND * 0.5) and (c["purch"] or 0) < MIN_PUR:
        return "UNDERFUNDED"
    if c["aud"] == "WARM":
        return "AUDIENCE-ASSISTED"
    if not b or not c["cpa"]:
        return "STEADY"
    strong_cpa = c["cpa"] <= b["cpa_med"] * 0.85
    beats_value = c["roas"] >= max(ROAS_T, cold_roas * 0.95)
    if c["spend"] >= MIN_SPEND and c["purch"] >= MIN_PUR and strong_cpa and beats_value and c["freq"] < 3.5:
        return "SCALE OPPORTUNITY"
    if c["spend"] >= MIN_SPEND and strong_cpa and not beats_value:
        return "EFFICIENT-LOW-ROAS"
    if c["spend"] >= MIN_SPEND and c["cpa"] > b["cpa_med"] * 1.3:
        return "BAD CREATIVE"
    if c["cpa"] <= b["cpa_med"]:
        return "PERFORMS"
    return "WATCH"

def agg(ms):
    spend = sum(c["spend"] for c in ms); rev = sum(c["rev"] for c in ms); purch = sum(c["purch"] for c in ms)
    reach = sum(c["reach"] for c in ms); impr = sum(c["impr"] for c in ms)
    lc = sum(c["lc"] for c in ms); atc = sum(c["atc"] for c in ms)
    return {"spend": round(spend), "rev": round(rev), "purch": int(purch), "reach": int(reach), "lc": round(lc),
            "roas": round(rev / spend, 2) if spend else 0, "cpa": round(spend / purch) if purch else None,
            "cpm": round(spend / impr * 1000, 2) if impr else 0, "cpmr": round(spend / reach * 1000, 2) if reach else 0,
            "cvr": round(purch / lc * 100, 2) if lc else 0, "atc_rate": round(atc / lc * 100, 1) if lc else 0,
            "cpc": round(spend / lc, 2) if lc else 0,
            "aov": round(rev / purch) if purch else None,
            "ctr": round(sum(c["ctr"] * c["impr"] for c in ms) / impr, 2) if impr else 0}

def attribute(rows, prev, key):
    """Which single creative drove the account-level change in `key` the most (in EGP)."""
    best, bestv = None, 0
    for c in rows:
        pc = prev.get(c["ad_id"])
        if not pc or c["spend"] < 500: continue
        if key == "cvr":   impact = c["lc"] * (c["cvr"] - pc["cvr"]) / 100 * (c["aov"] or 0)   # revenue EGP
        elif key == "aov": impact = c["purch"] * ((c["aov"] or 0) - (pc["aov"] or 0))          # revenue EGP
        elif key == "cpc": impact = -c["lc"] * ((c["cpc"] or 0) - (pc["cpc"] or 0))            # cheaper clicks = +EGP saved
        else: impact = 0
        if abs(impact) > abs(bestv): best, bestv = c, impact
    return best, round(bestv)

def tag(d, up_good):
    """plain word for a % move; up_good=True means higher is better."""
    if d is None or abs(d) < 5: return "~flat"
    good = (d > 0) == up_good
    return ("up" if d > 0 else "down") + (" (good)" if good else " (drag)")

def contrib(m, p):
    """How much of the ROAS move each lever is responsible for, in %.
    ROAS = CVR x AOV / CPC is an exact identity, so the log of each ratio is that lever's
    additive share of the move. Shares are absolute and always sum to 100%."""
    def lr(a, b):
        try:
            a = float(a or 0); b = float(b or 0)
            if a > 0 and b > 0: return math.log(a / b)
        except Exception: pass
        return 0.0
    t = {"CVR": lr(m.get("cvr"), p.get("cvr")),
         "AOV": lr(m.get("aov"), p.get("aov")),
         "CPC": -lr(m.get("cpc"), p.get("cpc"))}   # cheaper clicks LIFT roas, so flip the sign
    tot = sum(abs(v) for v in t.values())
    if tot <= 0: return None
    egp = {"CVR": round(m["lc"] * ((m.get("cvr") or 0) - (p.get("cvr") or 0)) / 100 * (m.get("aov") or 0)),
           "AOV": round(m["purch"] * ((m.get("aov") or 0) - (p.get("aov") or 0))),
           "CPC": round(-m["lc"] * ((m.get("cpc") or 0) - (p.get("cpc") or 0)))}
    return [{"k": k, "pct": round(abs(t[k]) / tot * 100), "help": t[k] >= 0, "egp": egp[k]}
            for k in sorted(t, key=lambda k: -abs(t[k]))]

def share_line(m, p):
    """'CVR did 52% of the move (helped, +29,071 EGP) · AOV 38% (hurt, -18,400) · CPC 10% ...'"""
    c = contrib(m, p)
    if not c: return None
    bits = []
    for x in c:
        bits.append("*%s %d%%* of the move (%s, %s%s EGP)" % (
            x["k"], x["pct"], "helped" if x["help"] else "hurt",
            "+" if x["egp"] >= 0 else "-", money(abs(x["egp"]))))
    return "  ·  ".join(bits)

def diagnose(m, p, rows=None, prev=None, tf=None):
    """Revenue = Traffic x CVR x AOV, clicks priced by CPC.
    Report all 3 levers (CPC / CVR / AOV) with % change every time, name which moved, then dig to the ad."""
    if not p or not p.get("spend"): return "No comparable prior period to diagnose."
    if tf is None:
        tf = "%s→%s vs %s→%s" % (DATES["label"][0], DATES["label"][1], DATES["p_label"][0], DATES["p_label"][1])
    drev = pct(m["rev"], p["rev"]); droas = pct(m["roas"], p["roas"]); dspend = pct(m["spend"], p["spend"])
    dcpc = pct(m["cpc"], p["cpc"]); dcvr = pct(m["cvr"], p["cvr"]); daov = pct(m["aov"] or 0, p["aov"] or 0)
    head = "*%s* · Revenue %s (%s vs %s EGP) · Spend %s · ROAS %s (%s)." % (
        tf, sp(drev), money(m["rev"]), money(p["rev"]), sp(dspend), m["roas"], sp(droas))
    lines = [
        "   • CPC: %s → %s EGP (%s, %s)" % (p["cpc"], m["cpc"], sp(dcpc), tag(dcpc, False)),
        "   • CVR: %s%% → %s%% (%s, %s)" % (p["cvr"], m["cvr"], sp(dcvr), tag(dcvr, True)),
        "   • AOV: %s → %s EGP (%s, %s)" % (money(p["aov"] or 0), money(m["aov"] or 0), sp(daov), tag(daov, True))]
    # which single lever moved revenue the most (by |%|)
    levers = [("CVR", "cvr", dcvr, True, "conversion rate"),
              ("AOV", "aov", daov, True, "order value"),
              ("CPC", "cpc", dcpc, False, "click cost")]
    levers = [x for x in levers if x[2] is not None]
    if not levers or max(abs(x[2]) for x in levers) < 5:
        return head + "\n" + "\n".join(lines) + "\n   *Read:* all three held flat, revenue is steady, nothing to act on."
    name, key, d, up_good, word = max(levers, key=lambda x: abs(x[2]))
    prevv, curv = p[key], m[key]
    # EGP the lever is worth. CVR/AOV = revenue at same clicks; CPC = spend saved.
    if key == "cvr":   egp = round(m["lc"] * (curv - prevv) / 100 * (m["aov"] or 0)); egpw = "%s EGP of revenue at the same clicks" % money(egp)
    elif key == "aov": egp = round(m["purch"] * (curv - prevv));                       egpw = "%s EGP of revenue" % money(egp)
    else:              egp = round(-m["lc"] * (curv - prevv));                          egpw = "%s EGP of spend" % money(egp)
    # ROAS is what the 3 levers exactly control (ROAS = CVR x AOV / CPC). Revenue = that efficiency x spend.
    read = ("*%s* %s %s, the biggest of the three." % (name, "rose" if d > 0 else "fell", sp(d)) + IND +
            "Worth about *%s*." % egpw + IND +
            "That drove ROAS %s." % sp(droas) + IND +
            "Revenue %s because spend %s." % (sp(drev), sp(dspend)))
    out = [head] + lines
    sl = share_line(m, p)
    if sl: out.append("   *Who moved it:* " + sl + ".")
    out.append("   *Read:* " + read)
    culprit, cimp = attribute(rows, prev, key) if (rows and prev) else (None, 0)
    if culprit and abs(cimp) > 0:
        pc = prev.get(culprit["ad_id"], {})
        u = "%" if key == "cvr" else " EGP"
        share = round(cimp / egp * 100) if egp else None
        sw = ("~%d%% of the swing" % abs(share)) if share is not None else "the single biggest cause"
        out.append("   *Deeper:* biggest single mover was %s, its %s went %s%s→%s%s on %s EGP spend (%s)." % (
            nm(culprit), name, pc.get(key), u, culprit.get(key), u, money(culprit["spend"]), sw))
    return "\n".join(out)

def analyze(acct, cur_rows, prev_rows, statuses=None):
    rows = [metric(r) for r in cur_rows]
    for c in rows: c["status"] = (statuses or {}).get(c["ad_id"])
    prev = {m["ad_id"]: m for m in (metric(r) for r in prev_rows)}
    B = benchmarks(rows)
    cold = [c for c in rows if c["aud"] == "COLD"]
    cold_s = sum(c["spend"] for c in cold); cold_r = sum(c["rev"] for c in cold)
    cold_roas = round(cold_r / cold_s, 2) if cold_s else 0
    # Pareto 80/20: mark the vital few that make up 80% of spend + a hard significance floor
    tot_spend = sum(c["spend"] for c in rows) or 1
    cum = 0
    for c in sorted(rows, key=lambda c: c["spend"], reverse=True):
        cum += c["spend"]; c["vital"] = cum <= 0.8 * tot_spend + 0.01
        c["significant"] = c["spend"] >= max(MIN_SPEND, 0.03 * tot_spend)
    _acc = agg(rows); acc_cpmr = _acc["cpmr"] or 1; acc_cvr = _acc["cvr"] or 1; acc_roas = _acc["roas"] or 1
    _imp = sum(c["impr"] for c in rows) or 1; _rch = sum(c["reach"] for c in rows) or 1
    _acc_octr = round(sum(c["octr"] * c["impr"] for c in rows) / _imp, 2)
    _acc_freq = round(_imp / _rch, 2)
    tot_sp = _acc["spend"] or 1
    _vids = [c for c in rows if c["type"] == "VIDEO" and c["hook"] and c["spend"] >= 500]
    acc_hook = round(st.mean([c["hook"] for c in _vids]), 1) if _vids else 0
    for c in rows:
        c["acc_hook"] = acc_hook; c["spend_share"] = round(c["spend"] / tot_sp * 100, 1)
        c["avg_share"] = round(100.0 / len(rows), 1) if rows else 0
        c["label"] = label(c, B, prev, cold_roas)
        b = B.get("%s/%s" % (c["aud"], c["type"]))
        c["gap"] = round((c["cpa"] - b["cpa_med"]) / b["cpa_med"] * 100) if (b and c["cpa"]) else None
        c["seg_cpa_med"] = b["cpa_med"] if b else None
        # store account reference values so every block can show raw side-by-side (never percentage points)
        c["acc_cpmr"] = round(acc_cpmr); c["acc_cvr"] = round(acc_cvr, 2); c["acc_roas"] = round(acc_roas, 2)
        c["acc_cpc"] = _acc["cpc"]; c["acc_cpm"] = _acc["cpm"]; c["acc_octr"] = _acc_octr; c["acc_freq"] = _acc_freq
        c["cpmr_vs_acc"] = round(pct(c["cpmr"], acc_cpmr)) if c["cpmr"] else None
        c["cvr_vs_acc"] = round(pct(c["cvr"], acc_cvr)) if c["cvr"] else None
        # only a genuine bleeder: cold, real spend, CPA above group AND ROAS below the cold bar (good ROAS is never waste)
        c["waste"] = round(c["purch"] * (c["cpa"] - b["cpa_med"])) if (
            c["aud"] == "COLD" and b and c["cpa"] and c["cpa"] > b["cpa_med"]
            and c["spend"] >= MIN_SPEND and c["roas"] and c["roas"] < cold_roas) else 0
        # most scalable = best blend of low CPMR, low CPA, high ROAS, high CVR (each vs its benchmark), with real spend behind it
        if c["label"] == "SCALE OPPORTUNITY" and b:
            r_roas = c["roas"] / (cold_roas or 1)
            r_cvr = c["cvr"] / (acc_cvr or 1)
            r_cpa = (b["cpa_med"] or 1) / (c["cpa"] or 1)
            r_cpmr = (acc_cpmr or 1) / (c["cpmr"] or 1)
            c["scale_score"] = round((r_roas + r_cvr + r_cpa + r_cpmr) * (c["spend"] ** 0.5), 1)
        else:
            c["scale_score"] = 0
    cur_a = agg(rows); prev_a = agg(list(prev.values())) if prev else None
    cat = sum(c["spend"] for c in rows if c["type"] == "CATALOGUE")
    summary = dict(cur_a)
    summary.update({"revenue": cur_a["rev"], "purchases": cur_a["purch"],
                    "cat_pct": round(cat / cur_a["spend"] * 100) if cur_a["spend"] else 0,
                    "cold_roas": cold_roas, "prev": prev_a,
                    "d_spend": pct(cur_a["spend"], prev_a["spend"]) if prev_a else None,
                    "d_roas": pct(cur_a["roas"], prev_a["roas"]) if prev_a else None,
                    "d_cpa": pct(cur_a["cpa"] or 0, (prev_a or {}).get("cpa") or 0) if prev_a else None,
                    "d_cpm": pct(cur_a["cpm"], prev_a["cpm"]) if prev_a else None,
                    "d_cpmr": pct(cur_a["cpmr"], prev_a["cpmr"]) if prev_a else None,
                    "d_cvr": pct(cur_a["cvr"], prev_a["cvr"]) if prev_a else None,
                    "d_atc": pct(cur_a["atc_rate"], prev_a["atc_rate"]) if prev_a else None,
                    "diagnosis": diagnose(cur_a, prev_a, rows, prev)})
    winners = [c for c in rows if c["label"] == "SCALE OPPORTUNITY"]
    offenders = [c for c in rows if c["waste"] > 0]
    offender = max(offenders, key=lambda c: c["waste"]) if offenders else None
    best = max(winners, key=lambda c: c["scale_score"]) if winners else None
    off_id = offender["ad_id"] if offender else None
    # the reallocation target must be a DIFFERENT ad than the one being cut
    if not best or best["ad_id"] == off_id:
        cand = [c for c in rows if c["aud"] == "COLD" and c["spend"] >= MIN_SPEND and c["roas"]
                and c["ad_id"] != off_id and c["roas"] >= cold_roas]
        best = max(cand, key=lambda c: c["roas"]) if cand else best
    return {"account": acct, "summary": summary, "benchmarks": B,
            "creatives": sorted(rows, key=lambda c: c["spend"], reverse=True),
            "offender": offender,
            "opportunity": (max(winners, key=lambda c: c["scale_score"]) if winners else None),
            "best_target": best if (best and best["ad_id"] != off_id) else None}


# ----------------------- Slack -----------------------
def slack(channel, text):
    if not channel: return
    if not SLACK_TOKEN:
        print("[slack:%s] %s\n" % (channel, text[:80])); return
    body = json.dumps({"channel": channel, "text": text, "unfurl_links": False, "mrkdwn": True}).encode()
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=body,
        headers={"Authorization": "Bearer %s" % SLACK_TOKEN, "Content-Type": "application/json; charset=utf-8"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if not r.get("ok"): sys.stderr.write("[slack] %s: %s\n" % (channel, r.get("error")))
    except Exception as e:
        sys.stderr.write("[slack] %s\n" % e)

def cur(A): return A["account"].get("currency", "")
def tgt(A):
    t = A.get("best_target")
    return nm(t) if t else "the lowest-CPA cold winner"
BAR = "━━━━━━━━━━━━━━━━━━"
def cpmrword(c):
    """Reach cost shown raw next to the account average. Never percentage points."""
    if c.get("acc_cpmr") is None: return ""
    return " vs %s avg" % money(c["acc_cpmr"])
def cmet(c):
    """Two clean metric lines for one creative. CVR and CPMR shown next to the account average."""
    return ("     Spend %s · Rev %s · ROAS %s · CPA %s · AOV %s\n"
            "     CVR %s%%%s · ATC %s%% · *CPMR %s*%s · CPM %s · Freq %s") % (
        money(c["spend"]), money(c["rev"]), c["roas"], money(c["cpa"]), money(c["aov"]),
        c["cvr"], vs_avg(c.get("acc_cvr"), pct=True), c["atc_rate"], money(c["cpmr"]), cpmrword(c), money(c["cpm"]), c["freq"])
def gapw(g):
    return "n/a" if g is None else ("%d%% below" % abs(g) if g <= 0 else "%d%% above" % g)
IND = "\n     "  # indent used to break after every period inside a Why block
def why_scale(c, cold):
    reach = ("reaches people cheaper than the rest" if (c.get("acc_cpmr") and c["cpmr"] < c["acc_cpmr"])
             else "reach is more expensive than the account, so watch CPM as you scale")
    return ("Best value of the winners." + IND +
            "CPA %s vs group median %s." % (money(c["cpa"]), money(c["seg_cpa_med"])) + IND +
            "ROAS %s vs cold bar %s." % (c["roas"], cold) + IND +
            "CVR %s%% vs %s%% account avg." % (c["cvr"], c.get("acc_cvr")) + IND +
            "CPMR %s vs %s account avg, %s." % (money(c["cpmr"]), money(c.get("acc_cpmr")), reach))
def why_cut(c):
    return ("CPA %s vs group median %s at ROAS %s." % (money(c["cpa"]), money(c["seg_cpa_med"]), c["roas"]) + IND +
            "CPMR %s vs %s account avg." % (money(c["cpmr"]), money(c.get("acc_cpmr"))) + IND +
            "Wasting about %s this week versus reallocating that spend." % money(c["waste"]))
def fatigue_line(c):
    """One-line fatigue evidence with the exact numbers and window."""
    p = c.get("prev") or {}
    d1, d2 = DATES["label"]; q1, q2 = DATES["p_label"]
    cpm = "CPM steady" if (c.get("d_cpm") is not None and abs(c["d_cpm"]) < 12) else "CPM %s" % sp(c.get("d_cpm"))
    return "Freq %s→%s (%s) · Outbound CTR %s%%→%s%% (%s) · %s · Spend %s   _(%s → %s vs %s → %s)_" % (
        p.get("freq"), c["freq"], sp(c["d_freq"]), p.get("octr"), c["octr"], sp(c["d_octr"]), cpm, money(c["spend"]),
        d1, d2, q1, q2)
def why_fatigue(c):
    return ("Frequency up, Outbound CTR down means the same people seeing it and fewer clicking." + IND +
            "Creative fatigue, not the auction." + IND + "Refresh the hook." + IND + fatigue_line(c))
def why_lowroas(c, cold):
    return ("Cheap CPA %s vs group median %s, but ROAS %s is under the cold bar of %s." % (
            money(c["cpa"]), money(c["seg_cpa_med"]), c["roas"], cold) + IND +
            "AOV is only %s." % money(c["aov"]) + IND + "Efficient reach, low value.")

def aud_block(rows):
    """Spend split by real audience: New / Engaged / Existing, % of spend + what it returned."""
    tot = sum(c["spend"] for c in rows) or 1
    L = [":busts_in_silhouette: *AUDIENCE SPLIT (% of spend)*"]
    for sg in ("NEW", "ENGAGED", "EXISTING"):
        g = [c for c in rows if c["seg"] == sg]
        sp = sum(c["spend"] for c in g); rv = sum(c["rev"] for c in g); pu = sum(c["purch"] for c in g)
        if not sp:
            L.append("• *%s* - 0%% of spend, nothing running." % SEGN[sg]); continue
        L.append("• *%s* - *%d%%* of spend (%s) · ROAS %s · CPA %s · %d purch" % (
            SEGN[sg], round(sp / tot * 100), money(sp), round(rv / sp, 2),
            money(round(sp / pu)) if pu else "n/a", int(pu)))
    return "\n".join(L)

def block(icon, title, name, status, do, metrics, why):
    return "\n".join([
        "%s  *%s* - %s   `%s`" % (icon, title, name, status),
        "     :arrow_right: _%s_" % do,
        metrics,
        "     *Why:* %s" % why, ""])

def msg_digest(A):
    s = A["summary"]; cc = cur(A); rows = A["creatives"]
    d1, d2 = DATES["label"]; p1, p2 = DATES["p_label"]
    L = ["%s  :bar_chart:  *%s - LAST 3 DAYS*  _(refreshed every morning)_" % (MENTION, A["account"]["name"].upper()),
         ":date: *%s → %s*   vs   *%s → %s*   (all values in %s)" % (d1, d2, p1, p2, cc), BAR,
         "*ACCOUNT - all ads*",
         "Spend *%s* (%s) · Revenue *%s* · %d purchases" % (money(s["spend"]), sp(s["d_spend"]), money(s["revenue"]), s["purchases"]),
         "ROAS *%s* (%s) · CPA *%s* (%s) · AOV %s" % (s["roas"], sp(s["d_roas"]), money(s["cpa"]), sp(s["d_cpa"]), money(s["aov"])),
         "CVR %s%% (%s) · ATC %s%% (%s) · CPMR *%s* (%s) · CPM %s (%s)" % (s["cvr"], sp(s["d_cvr"]), s["atc_rate"], sp(s["d_atc"]), money(s["cpmr"]), sp(s["d_cpmr"]), money(s["cpm"]), sp(s["d_cpm"])),
         "Cold prospecting ROAS *%s* = the bar to beat.%s" % (s["cold_roas"], (" Catalogue %d%% of spend inflates blended ROAS." % s["cat_pct"]) if s["cat_pct"] >= 15 else ""),
         "", ":mag: *WHY REVENUE MOVED*  (Revenue = clicks × CVR × AOV)", s["diagnosis"],
         "", aud_block(rows), BAR, "*DO NOW*", ""]
    did = False
    op = A["opportunity"]
    if op:
        L.append(block(":rocket:", "SCALE", nm(op), statuslabel(op), "Raise budget 20-30%.", cmet(op), why_scale(op, s["cold_roas"]))); did = True
    off = A["offender"]
    if off:
        t = A.get("best_target")
        act = ("Drop 30-40%%, move budget to %s." % nm(t)) if t else "Drop 30-40%%. Park the freed budget until a winner proves out."
        L.append(block(":rotating_light:", "CUT", nm(off), statuslabel(off), act, cmet(off), why_cut(off))); did = True
    # only significant (80/20) fatiguing ads, never trivial-spend ads
    fat = [c for c in rows if c["label"] == "FATIGUE" and c.get("significant")]
    if fat:
        c = fat[0]
        L.append(block(":recycle:", "REFRESH", nm(c), statuslabel(c), "New first 3 seconds, keep the concept.", cmet(c), why_fatigue(c))); did = True
    low = [c for c in rows if c["label"] == "EFFICIENT-LOW-ROAS" and c.get("significant")]
    if low:
        c = low[0]
        L.append(block(":test_tube:", "TEST HIGHER AOV", nm(low[0]), statuslabel(low[0]), "Same hook, pricier product.", cmet(low[0]), why_lowroas(low[0], s["cold_roas"]))); did = True
    if not did:
        L.append("_Nothing crossed an action threshold. Everything is inside its guardrails._\n")

    # ---- DO MORE OF: every ad beating the account with frequency headroom, not just one ----
    domore = [c for c in rows if c.get("significant") and c["roas"] and c["roas"] >= s["roas"]
              and c["freq"] < freq_ceiling(c) and (c["purch"] or 0) >= 2]
    domore.sort(key=lambda c: (c["roas"] or 0) * (c["spend"] ** 0.4), reverse=True)
    L += [BAR, ":arrow_up: *DO MORE OF*  _(beating account ROAS %s with room to grow)_" % s["roas"]]
    if domore:
        for c in domore[:4]:
            inc = "30%" if c["freq"] < 1.5 else ("20%" if c["freq"] < 1.8 else "15%")
            cheap = "cheaper reach" if (c.get("acc_cpmr") and c["cpmr"] < c["acc_cpmr"]) else "reach at account cost"
            L.append("• %s   `%s`  -  raise budget +%s" % (nm(c), statuslabel(c), inc))
            L.append("     ROAS %s vs %s account · CPA %s · CVR %s%% · CPMR %s vs %s avg (%s) · Freq %s" % (
                c["roas"], s["roas"], money(c["cpa"]), c["cvr"], money(c["cpmr"]), money(c.get("acc_cpmr")), cheap, c["freq"]))
        L.append("")
    else:
        L.append("_No ad clears account ROAS with frequency headroom right now. Nothing clean to scale, find a winner first._\n")

    # ---- FATIGUING: any significant ad where the same people see it more and fewer click ----
    fatig = [c for c in rows if c.get("significant") and c.get("d_freq") is not None and c["d_freq"] > 10
             and c.get("d_octr") is not None and c["d_octr"] < -8]
    fatig.sort(key=lambda c: (c.get("d_freq") or 0) - (c.get("d_octr") or 0), reverse=True)
    L += [BAR, ":chart_with_downwards_trend: *FATIGUING*  _(frequency rising, Outbound CTR falling, refresh the hook)_"]
    if fatig:
        for c in fatig[:6]:
            sev = "hard" if (c["d_freq"] > 30 and c["d_octr"] < -20) else ("building" if (c["d_freq"] > 18 or c["d_octr"] < -15) else "early")
            L.append("• %s   `%s`  -  %s fatigue" % (nm(c), statuslabel(c), sev))
            L.append("     %s" % fatigue_line(c))
        L.append("")
    else:
        L.append("_No significant ad is fatiguing. Frequency and Outbound CTR are holding across the board._\n")

    L += [BAR, "_Window: rolling last 3 days vs the 3 before. Runs automatically 9 AM Cairo, every day._"]
    return "\n".join(L)


# ----------------------- 3-day pulse (80/20) -----------------------
def prow(r, key):
    spend = f(r.get("spend")); purch = pick(r.get("actions"), PURCH); reach = f(r.get("reach"))
    lc = pick(r.get("actions"), ["link_click"])
    rev = pick(r.get("action_values"), PURCH) or f(r.get("purchase_roas")) * spend
    return {"name": r.get(key, "(unnamed)"), "id": r.get("ad_id") or r.get("adset_id"),
            "spend": round(spend), "rev": round(rev), "purch": round(purch),
            "roas": round(rev / spend, 2) if spend else 0, "cpa": round(spend / purch) if purch else None,
            "cpmr": round(spend / reach * 1000) if reach else None,
            "cvr": round(purch / lc * 100, 2) if lc else None}

def pareto(rows):
    rows = [x for x in rows if x["spend"] > 0]
    total = sum(x["spend"] for x in rows) or 1
    vital, cum = [], 0
    for x in sorted(rows, key=lambda x: x["spend"], reverse=True):
        vital.append(x); cum += x["spend"]
        if cum >= 0.8 * total: break
    real = [x for x in rows if x["spend"] >= total * 0.03 and x["purch"]]
    best = sorted(real, key=lambda x: x["roas"], reverse=True)[:3]
    worst = sorted(real, key=lambda x: x["roas"])[:3]
    return vital, best, worst, total

def linkrow(x, cc, is_ad, acc_roas=None, acc_cpmr=None, acc_cvr=None):
    label = nm({"ad_name": x["name"], "ad_id": x["id"]}) if is_ad else aset_link(x["name"], x["id"])
    st = ""
    if is_ad and STATUS.get(x["id"]):
        st = "   `%s`" % STAT.get(STATUS[x["id"]], STATUS[x["id"]])
    roasv = vs_avg(acc_roas)
    cvr = ("  ·  CVR %s%%%s" % (x["cvr"], vs_avg(acc_cvr, pct=True))) if x.get("cvr") is not None else ""
    cpmr = ("  ·  CPMR %s%s" % (money(x["cpmr"]), vs_avg(acc_cpmr, is_money=True))) if x.get("cpmr") is not None else ""
    return "   • %s%s\n        Spend %s · Rev %s · ROAS %s%s · CPA %s%s%s" % (
        label, st, money(x["spend"]), money(x["rev"]), x["roas"], roasv, money(x["cpa"]), cvr, cpmr)

def pulse_3day(acct, ad_rows, set_rows, m3, p3m):
    cc = acct.get("currency", "")
    ads = [prow(r, "ad_name") for r in ad_rows]
    sets = [prow(r, "adset_name") for r in set_rows]
    tot = sum(x["spend"] for x in ads)
    if tot <= 0: return None
    rev = sum(x["roas"] * x["spend"] for x in ads); purch = sum(x["purch"] for x in ads)
    d1, d2 = DATES["l3"]; q1, q2 = DATES["p3"]
    _, bset, wset, tset = pareto(sets)
    _, bad, wad, _ = pareto(ads)
    L = ["%s  :zap:  *%s - 3-Day Pulse*" % (MENTION, acct["name"]),
         ":date: *%s → %s*   (vs prior 3 days %s → %s)" % (d1, d2, q1, q2), "",
         "Spend *%s %s*  ·  ROAS *%s*  ·  %d purch" % (money(tot), cc, round(rev / tot, 2), int(purch))]
    if m3:
        dd = lambda k: sp(pct(m3.get(k), (p3m or {}).get(k))) if p3m else "n/a"
        L.append("ROAS %s (%s)  ·  CPA %s (%s)  ·  *CPMR %s* (%s)  ·  CPM %s (%s)" %
                 (m3["roas"], dd("roas"), money(m3["cpa"]), dd("cpa"), money(m3["cpmr"]), dd("cpmr"), money(m3["cpm"]), dd("cpm")))
        L.append("CVR %s%% (%s)  ·  ATC %s%% (%s)  ·  AOV %s %s (%s)" %
                 (m3["cvr"], dd("cvr"), m3["atc_rate"], dd("atc_rate"), money(m3["aov"]), cc, dd("aov")))
        tf3 = "%s→%s vs %s→%s" % (DATES["l3"][0], DATES["l3"][1], DATES["p3"][0], DATES["p3"][1])
        L.append(":mag: *WHY REVENUE MOVED*  (Revenue = clicks × CVR × AOV)")
        L.append(diagnose(m3, p3m, tf=tf3))
    ar = (m3 or {}).get("roas"); ac = (m3 or {}).get("cpmr"); av = (m3 or {}).get("cvr")
    lr = lambda x, isad: linkrow(x, cc, isad, ar, ac, av)
    L += ["", "_Verdicts below are vs this account's 3-day average._",
         ":large_green_circle: *Best adsets*"] + [lr(x, False) for x in bset] + \
        ["", ":red_circle: *Worst adsets*"] + [lr(x, False) for x in wset] + \
        ["", ":large_green_circle: *Best ads*"] + [lr(x, True) for x in bad] + \
        ["", ":red_circle: *Worst ads*"] + [lr(x, True) for x in wad] + \
        ["", "_Rolling 3-day read. Runs 9 AM Cairo daily._"]
    return "\n".join(L)


# ----------------------- New launches -----------------------
def msg_launches(acct, creatives, prev_ids, acc_roas=None, acc_cpmr=None, acc_cvr=None):
    cc = acct.get("currency", "")
    launched = [c for c in creatives if c["ad_id"] not in prev_ids and c["spend"] > 0]
    d1, d2 = DATES["label"]
    head = ["%s  :rocket:  *%s - New Launches*" % (MENTION, acct["name"]),
            ":date: new this week (no spend in the prior 7 days)  ·  performance *%s → %s*" % (d1, d2),
            "_Verdicts are vs the account average._", ""]
    if not launched:
        return "\n".join(head + ["_No new ads started spending in the last 7 days._"])
    groups = {}
    for c in launched:
        g = groups.setdefault(c["adset"] or "(no adset)", {"spend": 0, "rev": 0, "purch": 0, "reach": 0, "lc": 0, "sid": c.get("adset_id"), "ads": []})
        g["spend"] += c["spend"]; g["rev"] += c["rev"]; g["purch"] += c["purch"]
        g["reach"] += c.get("reach", 0); g["lc"] += c.get("lc", 0); g["ads"].append(c)
    ordered = sorted(groups.items(), key=lambda kv: kv[1]["spend"], reverse=True)[:8]
    L = list(head)
    for name, g in ordered:
        roas = round(g["rev"] / g["spend"], 2) if g["spend"] else 0
        cpa = money(round(g["spend"] / g["purch"])) if g["purch"] else "n/a"
        gcpmr = round(g["spend"] / g["reach"] * 1000) if g["reach"] else None
        gcvr = round(g["purch"] / g["lc"] * 100, 2) if g["lc"] else None
        best = max([a for a in g["ads"] if a["spend"] > 0], key=lambda a: a["roas"], default=None)
        L.append("• %s" % aset_link(name, g["sid"]))
        L.append("     spend %s %s  ·  ROAS %s%s  ·  CPA %s %s  ·  %d purch" % (
            money(g["spend"]), cc, roas, vs_avg(acc_roas), cpa, cc, int(g["purch"])))
        L.append("     CVR %s%%%s  ·  CPMR %s%s" % (
            gcvr if gcvr is not None else "n/a", vs_avg(acc_cvr, pct=True),
            money(gcpmr) if gcpmr is not None else "n/a", vs_avg(acc_cpmr, is_money=True)))
        if best:
            L.append("     best ad: %s  `%s`  (ROAS %s%s, CPA %s %s, CVR %s%%%s, CPMR %s%s)" % (
                nm(best), statuslabel(best), best["roas"], vs_avg(acc_roas), money(best["cpa"]), cc,
                best["cvr"], vs_avg(acc_cvr, pct=True), money(best["cpmr"]), vs_avg(acc_cpmr, is_money=True)))
        L.append("")
    L.append("_New launches only. Runs 9 AM Cairo daily._")
    return "\n".join(L)


# ----------------------- 3-second hook report (video only) -----------------------
def msg_3sec(A):
    """Rank video ads by the honest thumbstop: share who actually stayed to 25% watched.
    Raw 3-sec plays are inflated by autoplay, so they are shown but not ranked on."""
    cc = cur(A); rows = A["creatives"]; s = A["summary"]
    d1, d2 = DATES["label"]; p1, p2 = DATES["p_label"]
    acct = A["account"]
    vids = [c for c in rows if c["type"] == "VIDEO" and c["impr"] >= 1000 and c.get("hold")]
    L = ["%s  :clapper:  *%s - 3-SECOND HOOK REPORT*" % (MENTION, acct["name"].upper()),
         ":date: *%s → %s*   (all values in %s)" % (d1, d2, cc),
         "_The hook is the first 3 seconds. Ranked by the share of people who stayed to 25% watched, the honest thumbstop._",
         "_Raw 3-sec plays are inflated by autoplay, so shown for reference but not ranked on._", BAR]
    if not vids:
        L.append("_No video ad had enough impressions to read a hook this window. Most spend ran through image or catalogue ads._")
        return "\n".join(L)
    avg = round(st.mean([c["hold"] for c in vids]), 1)
    ranked = sorted(vids, key=lambda c: c["hold"], reverse=True)
    good = [c for c in ranked if hold_band(c["hold"]) == "good"]
    bad = [c for c in ranked if hold_band(c["hold"]) == "bad"]
    def vline(c):
        return ("• %s   `%s`\n"
                "     3-sec hold *%s%%* (account avg %s%%) · raw 3-sec %s%% · halfway %s%% · to end %s%%\n"
                "     Spend %s · ROAS %s · CPA %s · Freq %s") % (
            nm(c), statuslabel(c), c["hold"], avg, c["hook"], c["r50"], c["r100"],
            money(c["spend"]), c["roas"], money(c["cpa"]), c["freq"])
    L.append(":white_check_mark: *GOOD HOOKS (do more of these)*")
    if good:
        for c in good[:5]: L.append(vline(c))
        dm = [c for c in good if c["roas"] and c["roas"] >= s["cold_roas"]]
        if dm:
            L.append("     :arrow_right: _Do more of: %s hold attention AND convert above the cold bar %s.  Cut new variations from these openings and raise their budget._" % (
                ", ".join(nm(c) for c in dm[:3]), s["cold_roas"]))
        else:
            L.append("     :arrow_right: _These hold attention but none clears the cold bar %s yet.  The hook works, the offer or landing is the leak._" % s["cold_roas"])
    else:
        L.append("_No video cleared the good band (25% hold at or above 35%) this window._")
    L += ["", ":x: *WEAK HOOKS (re-cut the first 3 seconds)*"]
    if bad:
        for c in bad[:5]: L.append(vline(c))
        L.append("     :arrow_right: _People leave in the first seconds.  Re-cut the opening frame, keep only concepts that hold.  Do not scale these._")
    else:
        L.append("_No video is in the weak band.  Hooks are holding._")
    L += [BAR, "_3-second hook report.  Ranked by 25% watched, the reliable thumbstop.  Runs 9 AM Cairo daily._"]
    return "\n".join(L)


# ----------------------- Strategic Advisor (weekly) -----------------------
def gappct(val, ref):
    return None if not ref else round((val - ref) / ref * 100)
def adref(c, cold=None):
    # headline: spend + share of the account, and whether that share is bigger than the average ad
    share = c.get("spend_share"); avg = c.get("avg_share")
    sh = ""
    if share is not None:
        big = "above the average ad" if (avg and share > avg) else "below the average ad"
        sh = "  (%s%% of account spend, %s)" % (share, big)
    head = "%s  ·  Spend %s%s" % (nm(c), money(c["spend"]), sh)
    parts = []
    if cold and c["roas"]:
        egp = round(c["spend"] * (c["roas"] - cold))
        parts.append("ROAS %s vs cold %s (%s, %s EGP rev)" % (c["roas"], cold, sp(gappct(c["roas"], cold)), money(egp)))
    if c.get("acc_cvr"):
        cegp = round(c["lc"] * (c["cvr"] - c["acc_cvr"]) / 100 * (c["aov"] or 0))
        parts.append("CVR %s%% vs %s%% avg (%s, %s EGP rev)" % (c["cvr"], c["acc_cvr"], sp(gappct(c["cvr"], c["acc_cvr"])), money(cegp)))
    if c.get("acc_cpmr"):
        regp = round(c["reach"] / 1000 * (c["cpmr"] - c["acc_cpmr"]))
        cheap = "cheaper reach" if c["cpmr"] < c["acc_cpmr"] else "more expensive reach"
        parts.append("CPMR %s vs %s avg (%s, %s EGP)" % (money(c["cpmr"]), money(c["acc_cpmr"]), cheap, money(regp)))
    # full auction/engagement line vs the account
    if c.get("acc_octr") is not None:
        parts.append("CTR %s%% vs %s%% avg · CPC %s vs %s avg · CPM %s vs %s avg · Freq %s vs %s avg" % (
            c["octr"], c["acc_octr"], c.get("cpc"), c.get("acc_cpc"), money(c["cpm"]), money(c.get("acc_cpm")), c["freq"], c.get("acc_freq")))
    # capital verdict
    if cold and c.get("roas"):
        if c["roas"] >= cold and c["freq"] < freq_ceiling(c): v, w = "MORE", "beats cold bar %s with frequency headroom" % cold
        elif c["roas"] >= cold: v, w = "WAIT", "profitable but frequency %s is saturating, broaden first" % c["freq"]
        else: v, w = "NO", "under the cold bar %s" % cold
        parts.append("*Deserves more money? %s* (%s)." % (v, w))
    return head + "\n     " + "\n     ".join(parts)
# agreed BSD bands (from the Meta Ads Playbook in the vault)
def hook_band(h): return "good" if h >= 20 else ("average" if h >= 12 else "bad")
def hold_band(h): return "good" if h >= 35 else ("average" if h >= 22 else "bad")
def freq_ceiling(c):
    # DPA/catalogue read on a higher ceiling: bench 2.5, fatigue 3.0. Everything else: cold ceiling 2.0.
    return 3.0 if c.get("type") == "CATALOGUE" else 2.0
def is_saturating(c):
    return c["aud"] == "COLD" and c.get("significant") and c["freq"] > freq_ceiling(c)
def video_read(c, avg, rank=None, ntot=None):
    """Rank a video against the account, quantify where it leaks vs average, and give a decision."""
    if not c.get("hook"): return None
    rk = " (ranks #%d of %d by hook)" % (rank, ntot) if rank and ntot else ""
    hg = round((c["hook"] - avg["hook"]) / avg["hook"] * 100) if avg["hook"] else 0
    L = [nm(c) + rk,
         "Hook %s%% (%s vs account avg)." % (c["hook"], sp(hg)),
         "Retention vs avg: 25%% %s%% (avg %s%%), halfway %s%% (avg %s%%), 75%% %s%% (avg %s%%), end %s%% (avg %s%%)." % (
             c["r25"], avg["r25"], c["r50"], avg["r50"], c["r75"], avg["r75"], c["r100"], avg["r100"])]
    d_ad = c["r25"] - c["r50"]; d_avg = avg["r25"] - avg["r50"]
    rate = round(d_ad / d_avg, 1) if d_avg else None
    if rate and rate >= 1.3:
        L.append("It sheds viewers %sx faster between 25%% and halfway than your average video, the body is the leak." % rate)
    gaps = [("25%", c["r25"] - avg["r25"]), ("halfway", c["r50"] - avg["r50"]),
            ("75%", c["r75"] - avg["r75"]), ("the end", c["r100"] - avg["r100"])]
    worst = min(gaps, key=lambda x: x[1])
    hook_ok = hg >= -5  # hook is at or above the account
    smap = {"25%": (c["r25"], avg["r25"]), "halfway": (c["r50"], avg["r50"]), "75%": (c["r75"], avg["r75"]), "the end": (c["r100"], avg["r100"])}
    wv = smap[worst[0]]
    if c["r50"] >= avg["r50"] and c["r100"] >= avg["r100"]:
        v = "Above-average retention the whole way. *Decision: increase spend, this is a proven format.*"
    elif hook_ok and worst[1] <= -2:
        v = "Hook holds but retention at %s runs %s%% versus the account %s%%. Opening lands, body leaks. *Decision: re-cut the body around %s, keep the open.*" % (worst[0], wv[0], wv[1], worst[0])
    elif hg < -5 and worst[1] <= -2:
        v = "Hook is %s below the account and retention at %s is %s%% versus %s%% average. Weak from the first frame. *Decision: retire this format, do not just re-cut.*" % (sp(hg), worst[0], wv[0], wv[1])
    else:
        v = "Tracks your average, no retention edge or leak. *Decision: hold, no creative change justified.*"
    L.append(v)
    return "\n     ".join(L)

def msg_advisor(A, overlaps=None):
    """A weekly strategic brief: read the numbers, then tell Shavi exactly what to do and why."""
    s = A["summary"]; cc = cur(A); rows = A["creatives"]
    d1, d2 = DATES["label"]; p1, p2 = DATES["p_label"]
    prev = s.get("prev") or {}
    dcpc = pct(s.get("cpc"), prev.get("cpc")); dcvr = pct(s["cvr"], prev.get("cvr"))
    daov = pct(s["aov"] or 0, prev.get("aov") or 0)
    cold = s["cold_roas"]; blended = s["roas"]
    # winner quality: ROAS above the cold bar first, cheaper reach breaks ties. Never a below-bar ad.
    qual = lambda c: (c["roas"] or 0) + (0.3 if (c.get("acc_cpmr") and c["cpmr"] < c["acc_cpmr"]) else 0)
    winners = sorted([c for c in rows if c["label"] == "SCALE OPPORTUNITY"], key=lambda c: c.get("scale_score", 0), reverse=True)[:3]
    if not winners:
        winners = sorted([c for c in rows if c["aud"] == "COLD" and c.get("significant") and c["roas"] and c["roas"] >= cold],
                         key=qual, reverse=True)[:3]
    # bleeders: genuinely below the cold bar with real spend (good ROAS is never a bleeder)
    bleeders = sorted([c for c in rows if c.get("waste", 0) > 0], key=lambda c: c["waste"], reverse=True)[:3]
    if not bleeders:
        bleeders = sorted([c for c in rows if c.get("significant") and c["aud"] == "COLD" and c["roas"] and c["roas"] < cold * 0.85],
                          key=lambda c: c["roas"])[:3]
    bleed_ids = {b["ad_id"] for b in bleeders}
    winners = [w for w in winners if w["ad_id"] not in bleed_ids]
    # reallocation target must beat the bleeder on ROAS, else there is no valid move
    realloc_to = next((w for w in winners if bleeders and w["roas"] and w["roas"] > bleeders[0]["roas"]), None)
    fatiguing = [c for c in rows if c["label"] == "FATIGUE" and c.get("significant")]
    # cold-test candidates are WARM static creatives only, never catalogue/DPA (they are dynamic, nothing to cold-test)
    assisted = sorted([c for c in rows if c["aud"] == "WARM" and c["type"] != "CATALOGUE" and c["roas"] and c["spend"] >= MIN_SPEND],
                      key=lambda c: c["roas"], reverse=True)[:2]
    saturating = sorted([c for c in rows if is_saturating(c)], key=lambda c: c["spend"], reverse=True)[:3]
    # clean cold videos only: real 3-sec plays, not catalogue, not the anomalous catalogue-enabled high-freq ones
    videos = sorted([c for c in rows if c["type"] == "VIDEO" and c["aud"] == "COLD" and c.get("significant")
                     and c.get("v3", 0) >= 500 and c["freq"] < 4 and c["hook"] <= 100],
                    key=lambda c: c["spend"], reverse=True)
    vavg = None
    if videos:
        vavg = {k: round(st.mean([c[k] for c in videos]), 1) for k in ("hook", "r25", "r50", "r75", "r100")}
    # ---- audience & reach allocation ----
    tot = s["spend"] or 1
    cold_sp = sum(c["spend"] for c in rows if c["aud"] == "COLD" and c["type"] != "CATALOGUE")
    warm_sp = sum(c["spend"] for c in rows if c["aud"] == "WARM" and c["type"] != "CATALOGUE")
    cat_sp = sum(c["spend"] for c in rows if c["type"] == "CATALOGUE")
    _coldads = [c for c in rows if c["aud"] == "COLD" and c["type"] != "CATALOGUE" and c["spend"] >= 500]
    cold_freq = round(st.mean([c["freq"] for c in _coldads]), 2) if _coldads else 0
    # ---- best at each thing: cold creatives only, so warm/catalogue don't win on inflated numbers ----
    sig = [c for c in rows if c.get("significant")]
    sig_cold = [c for c in sig if c["aud"] == "COLD" and c["type"] != "CATALOGUE"] or sig
    def _best(key, hi=True):
        pool = [c for c in sig_cold if c.get(key)]
        return (max if hi else min)(pool, key=lambda c: c[key]) if pool else None
    best_ctr = _best("octr"); best_aov = _best("aov"); best_cvr = _best("cvr"); cheap_cpmr = _best("cpmr", hi=False)
    top_spender = max(sig, key=lambda c: c["spend"], default=None)
    below_bar = [c for c in sig if c["aud"] == "COLD" and c["type"] != "CATALOGUE" and c["roas"] and c["roas"] < cold]

    # a lever only needs "fixing" if it moved AGAINST you. cheaper CPC or higher CVR/AOV are tailwinds, not problems.
    _levs = [("CVR", dcvr, True), ("AOV", daov, True), ("CPC", dcpc, False)]
    _levs = [x for x in _levs if x[1] is not None]
    _hurt = [(n, d, hg, (-d if hg else d)) for n, d, hg in _levs]         # 4th = how much it hurt (positive = hurt)
    _dragging = [x for x in _hurt if x[3] > 5]
    if _dragging:
        lever, _ld, _lhg, _ = max(_dragging, key=lambda x: x[3]); dragging = True
    else:
        lever = max(_hurt, key=lambda x: -x[3])[0] if _hurt else "CVR"; dragging = False
        _ld = dict((n, d) for n, d, *_ in _levs).get(lever)
    fix = {
        "CVR": ("Conversion is dragging you, that is landing page, offer and checkout, not media." + IND +
                "Audit the PDP, price and shipping presentation, COD friction and social proof."),
        "AOV": ("Order value is dragging you." + IND +
                "Pull offer mechanics: bundles, gift-with-purchase, volume tiers, a higher-price hero."),
        "CPC": ("Click cost rose, that is creative and auction." + IND +
                "Where a video's retention proves the body is weak, re-cut it; where reach is saturating, broaden the audience." + IND +
                "Do not touch ads whose retention and CTR are still holding."),
    }[lever]

    # money impact of each lever, in EGP
    e_cvr = round(s["lc"] * ((s["cvr"] or 0) - (prev.get("cvr") or 0)) / 100 * (s["aov"] or 0))
    e_cpc = round(-s["lc"] * ((s.get("cpc") or 0) - (prev.get("cpc") or 0)))
    e_aov = round(s["purch"] * ((s["aov"] or 0) - (prev.get("aov") or 0)))
    e_cpmr = round(s["reach"] / 1000 * ((s["cpmr"] or 0) - (prev.get("cpmr") or 0)))
    d_rev = pct(s["revenue"], prev.get("rev"))
    # ================= INVESTMENT MEMO =================
    r2 = lambda x: round(x or 0, 2)
    SEGN = {"NEW": "New audience", "ENGAGED": "Engaged", "EXISTING": "Existing customers"}
    segs = ("NEW", "ENGAGED", "EXISTING")
    _in = lambda sg: [c for c in rows if c["seg"] == sg]
    csp = {sg: sum(c["spend"] for c in _in(sg)) for sg in segs}
    crev = {sg: sum(c["rev"] for c in _in(sg)) for sg in segs}
    crch = {sg: sum(c["reach"] for c in _in(sg)) for sg in segs}
    cimp = {sg: sum(c["impr"] for c in _in(sg)) for sg in segs}
    psp = {sg: sum((c["prev"]["spend"] or 0) for c in _in(sg) if c.get("prev")) for sg in segs}
    prev_rev = {sg: sum((c["prev"]["rev"] or 0) for c in _in(sg) if c.get("prev")) for sg in segs}
    tC = sum(csp.values()) or 1; tP = sum(psp.values()) or 1
    mixC = {sg: csp[sg] / tC * 100 for sg in segs}; mixP = {sg: psp[sg] / tP * 100 for sg in segs}
    roC = {sg: (crev[sg] / csp[sg] if csp[sg] else 0) for sg in segs}
    roP = {sg: (prev_rev[sg] / psp[sg] if psp[sg] else 0) for sg in segs}
    frq = {sg: (cimp[sg] / crch[sg] if crch[sg] else 0) for sg in segs}
    shift = {sg: mixC[sg] - mixP[sg] for sg in segs}
    gained = max(segs, key=lambda sg: shift[sg]); lost = min(segs, key=lambda sg: shift[sg])
    # ---- THE THESIS: one story, with conviction ----
    if abs(shift[lost]) >= 4 and psp[lost] and csp[gained]:
        helped = roC[gained] >= roC[lost]
        newdir = "shrinking" if csp["NEW"] < psp["NEW"] * 0.95 else "holding"
        thesis = ("This week is a *capital-allocation* story, not a ROAS story.  Meta cut %s spend share from %d%% to %d%% and lifted %s from %d%% to %d%% (ROAS %s vs %s).  "
                  "Blended ROAS %s %s.  The catch: New-audience spend is *%s*, so next week's Engaged and Existing pools %s.") % (
                  SEGN[lost], round(mixP[lost]), round(mixC[lost]), SEGN[gained], round(mixP[gained]), round(mixC[gained]), r2(roC[gained]), r2(roP[lost]),
                  sp(s.get("d_roas")), "because it leaned into your more efficient audience" if helped else "held only because it leaned into a weaker audience, that is Meta misallocating",
                  newdir, "refill" if newdir == "holding" else "starve")
        conv = min(90, 60 + int(abs(shift[lost]) * 3))
    elif dragging and abs(e_cvr if lever == "CVR" else e_aov if lever == "AOV" else e_cpc) >= tC * 0.05:
        thesis = ("This week is a *%s deterioration* story.  It moved %s and cost roughly *%s EGP*, larger than any budget shift.  "
                  "Everything else, including the ROAS headline, is noise against that.") % (
                  lever, sp(_ld if dragging else 0), money(abs(e_cvr if lever == "CVR" else e_aov if lever == "AOV" else e_cpc)))
        conv = 75
    elif _coldads and cold_freq >= 2.0 and pct(sum(c["reach"] for c in _in("NEW")), sum((c["prev"]["reach"] or 0) for c in _in("NEW") if c.get("prev")) or 1) is not None and (sum(c["reach"] for c in _in("NEW")) <= (sum((c["prev"]["reach"] or 0) for c in _in("NEW") if c.get("prev")) or 1) * 1.05):
        thesis = ("This week is a *saturation* story.  New-audience frequency is %s while reach is flat, so more spend is buying the same people, not new ones.  "
                  "Scaling headroom is the constraint, not creative or ROAS.") % cold_freq
        conv = 70
    else:
        thesis = ("No structural problem this week.  Audience mix, efficiency and frequency all held, so this is purely a *scaling* question: "
                  "where does the next EGP buy the most incremental revenue, covered below.")
        conv = 65
    L = ["%s  :compass:  *%s - %s*" % (MENTION, A["account"]["name"].upper(),
                                       WIN_TITLE.get(DATES.get("win", "7day"), "MEMO")),
         ":date: *%s → %s*   vs   *%s → %s*   (all values in %s)" % (d1, d2, p1, p2, cc), BAR,
         "*THE THESIS*", thesis, "_Conviction: %d%%._" % conv,
         "", "*Portfolio:* Spend %s (%s) · Revenue %s (%s) · Blended ROAS %s (%s).  Attributed, reconcile vs MER before big cuts." % (
             money(s["spend"]), sp(s.get("d_spend")), money(s["revenue"]), sp(d_rev), blended, sp(s.get("d_roas")))]
    # ---- ACCOUNT SCORECARD (this week vs last week, % change) ----
    _ai = sum(c["impr"] for c in rows); _ar = sum(c["reach"] for c in rows) or 1
    _pi = sum((c["prev"]["impr"] or 0) for c in rows if c.get("prev")); _pr = sum((c["prev"]["reach"] or 0) for c in rows if c.get("prev")) or 1
    cur_octr = round(sum(c["octr"] * c["impr"] for c in rows) / (_ai or 1), 2); prv_octr = round(sum((c["prev"]["octr"] or 0) * (c["prev"]["impr"] or 0) for c in rows if c.get("prev")) / (_pi or 1), 2)
    cur_freq = round(_ai / _ar, 2); prv_freq = round(_pi / _pr, 2)
    def sc(lbl, cu, pv, m=False, pc=False):
        chg = pct(cu, pv)
        cus = money(cu) if m else ("%s%%" % cu if pc else str(cu))
        pvs = money(pv) if m else ("%s%%" % pv if pc else str(pv))
        return "%-13s %12s %12s   %s" % (lbl, cus, pvs, sp(chg))
    p_ = prev
    L += ["", BAR, "*ACCOUNT SCORECARD*", "```",
          "%-13s %12s %12s   %s" % ("Metric", "This wk", "Last wk", "Chg"),
          sc("Spend", s["spend"], p_.get("spend") or 0, m=True),
          sc("Revenue", s["revenue"], p_.get("rev") or 0, m=True),
          sc("ROAS", blended, r2(p_.get("roas"))),
          sc("Purchases", s["purchases"], int(p_.get("purch") or 0)),
          sc("CPA", s["cpa"] or 0, round(p_.get("cpa") or 0), m=True),
          sc("AOV", s["aov"] or 0, round(p_.get("aov") or 0), m=True),
          sc("CPM", s["cpm"], r2(p_.get("cpm")), m=True),
          sc("CPMR", s["cpmr"], r2(p_.get("cpmr")), m=True),
          sc("CTR", s["ctr"], r2(p_.get("ctr")), pc=True),
          sc("Outbound CTR", cur_octr, prv_octr, pc=True),
          sc("CPC", s.get("cpc") or 0, r2(p_.get("cpc")), m=True),
          sc("CVR", s["cvr"], r2(p_.get("cvr")), pc=True),
          sc("Frequency", cur_freq, prv_freq),
          sc("Reach", _ar, _pr, m=True),
          sc("Impressions", _ai, _pi, m=True), "```"]
    # ---- AUDIENCE: New / Engaged / Existing, week over week ----
    L += ["", BAR, "*AUDIENCE - where the money actually went*", ""]
    for sg in sorted(segs, key=lambda sg: csp[sg], reverse=True):
        if csp[sg] < tot * 0.01 and psp[sg] < tP * 0.01: continue
        pu = sum(c["purch"] for c in _in(sg)); lcs = sum(c["lc"] for c in _in(sg))
        L.append("*%s* - spend %s, share %d%% (was %d%%)" % (SEGN[sg], money(csp[sg]), round(mixC[sg]), round(mixP[sg])))
        L.append("     ROAS %s (was %s) · CPA %s · AOV %s · CVR %s%% · reach %s · frequency %s" % (
            r2(roC[sg]), r2(roP[sg]),
            money(round(csp[sg] / pu)) if pu else "n/a", money(round(crev[sg] / pu)) if pu else "n/a",
            round(pu / lcs * 100, 2) if lcs else 0, money(crch[sg]), r2(frq[sg])))
        L.append("")
    L.append("*Read:* budget moved into %s and out of %s, a move %s efficiency.  %s" % (
        SEGN[gained], SEGN[lost], "toward" if roC[gained] >= roC[lost] else "away from",
        "Watch New-audience spend, it is falling and it is what refills Engaged and Existing next week." if csp["NEW"] < psp["NEW"] else "New-audience spend held, the pipeline is intact."))
    # ---- CAPITAL ALLOCATION ----
    L += ["", BAR, "*CAPITAL ALLOCATION - did Meta allocate right*", "", "*Top spenders:*"]
    top3 = sorted(sig, key=lambda c: c["spend"], reverse=True)[:3]
    for c in top3:
        tag = "earning it" if (c["roas"] and c["roas"] >= s["roas"]) else "*overfunded* (ROAS below account)"
        L.append("• %s - %s (%s%% of account), ROAS %s vs %s account, %s" % (
            nm(c), money(c["spend"]), c["spend_share"], r2(c["roas"]), s["roas"], tag))
    over = [c for c in sig if c["roas"] and c["roas"] < s["roas"] * 0.8 and c["spend_share"] and c["spend_share"] >= (100.0 / max(len(rows), 1))]
    over.sort(key=lambda c: c["spend"], reverse=True)
    under = [c for c in sig if c["roas"] and c["roas"] >= s["roas"] * 1.2 and c["spend_share"] and c["spend_share"] < (100.0 / max(len(rows), 1))]
    under.sort(key=lambda c: c["roas"], reverse=True)
    L.append("")
    if over:
        c = over[0]; L.append("*Overfunded loser:* %s takes %s%% of spend at ROAS %s, well under the account %s.  Cut it first." % (nm(c), c["spend_share"], r2(c["roas"]), s["roas"]))
    if under:
        c = under[0]; L.append("*Underfunded winner:* %s ranks top on ROAS %s but takes only %s%% of spend.  Budget should follow performance." % (nm(c), r2(c["roas"]), c["spend_share"]))
    dest = under[0] if under else (winners[0] if winners else None)
    if dest:
        L += ["", "*Next 10,000 EGP → %s* (ROAS %s, frequency %s has room), pulled from %s." % (
            nm(dest), r2(dest["roas"]), dest["freq"], nm(over[0]) if over else (nm(bleeders[0]) if bleeders else "the lowest-ROAS spend"))]
    # ---- SCALING RANKING (ROAS + CVR + CPM + CPMR + frequency + audience-segment direction) ----
    accm = s["cpm"] or 1
    over_seg = gained if abs(shift[gained]) >= 4 else None      # segment Meta already over-rotated into this week
    starved_seg = lost if abs(shift[lost]) >= 4 else None       # segment losing share, needs refilling
    def scal_score(c):
        rf = (c["roas"] or 0) / (s["roas"] or 1)
        vf = (c["cvr"] or 0) / (s["cvr"] or 1)
        mf = accm / (c["cpm"] or 1)
        rmf = (c.get("acc_cpmr") or accm) / (c["cpmr"] or 1)     # cheaper reach scales further
        hf = max((freq_ceiling(c) - c["freq"]) / freq_ceiling(c), 0.05)
        seg_adj = 1.3 if c["seg"] == starved_seg else (0.7 if c["seg"] == over_seg else 1.0)
        return round(rf * vf * mf * rmf * hf * seg_adj * (c["spend"] ** 0.3), 1)
    scalable = [c for c in sig if c["roas"] and c["roas"] >= s["roas"] and c["freq"] < freq_ceiling(c) and c["purch"] >= 3]
    scalable.sort(key=scal_score, reverse=True)
    L += ["", BAR, "*MOST SCALABLE WINNERS*  _(ROAS, CVR, CPM, CPMR, frequency and audience direction together)_", ""]
    if scalable:
        for i, c in enumerate(scalable[:3], 1):
            inc = "30%" if c["freq"] < 1.5 else ("20%" if c["freq"] < 1.8 else "15%")
            segnote = ""
            if c["seg"] == over_seg:
                segnote = "  Caution: this is %s, the segment Meta already over-weighted this week, so scaling it deepens the tilt away from New." % SEGN[c["seg"]]
            elif c["seg"] == starved_seg:
                segnote = "  Bonus: this is %s, the segment losing share, feeding it also refills the pipeline." % SEGN[c["seg"]]
            L.append("*%d. %s*  (%s)" % (i, nm(c), SEGN[c["seg"]]))
            L.append("     ROAS %s · Rev %s · %d purch · Spend %s (%s%% of account)" % (r2(c["roas"]), money(c["rev"]), int(c["purch"]), money(c["spend"]), c["spend_share"]))
            L.append("     CPM %s vs %s avg · CPMR %s vs %s avg · CVR %s%% vs %s%% · Outbound CTR %s%% · frequency %s" % (
                money(c["cpm"]), money(round(accm)), money(c["cpmr"]), money(c.get("acc_cpmr")), c["cvr"], s["cvr"], c["octr"], c["freq"]))
            L.append("     *Recommended: +%s.*%s" % (inc, segnote))
            L.append("")
    else:
        L.append("_No ad clears account ROAS with frequency headroom. Nothing is cleanly scalable, priority is finding a winner._")
    # ---- CHEAP TRAFFIC ----
    L += ["", BAR, "*CHEAP TRAFFIC (cheap clicks, do they convert?)*"]
    cheap = sorted([c for c in sig if c.get("cpc")], key=lambda c: c["cpc"])[:5]
    cw = [c for c in cheap if c["roas"] and c["roas"] >= s["roas"]]
    cp = [c for c in cheap if not (c["roas"] and c["roas"] >= s["roas"])]
    if cw:
        L.append("*Winners (cheap and converting):* " + "; ".join("%s (CPC %s, CVR %s%%, ROAS %s)" % (nm(c), money(c["cpc"]), c["cvr"], r2(c["roas"])) for c in cw[:3]))
    if cp:
        L.append("*Problems (cheap clicks, weak purchases):* " + "; ".join("%s (CPC %s, CVR %s%%, ROAS %s)" % (nm(c), money(c["cpc"]), c["cvr"], r2(c["roas"])) for c in cp[:3]))
        L.append("_Cheap traffic that does not convert is not cheap. The click is cheap, the purchase is not. Cannot tell page vs offer from this data._")
    # ---- AOV OPPORTUNITY ----
    L += ["", BAR, "*AOV OPPORTUNITY*"]
    aovsort = sorted([c for c in sig if c.get("aov") and c["purch"] >= 3], key=lambda c: c["aov"], reverse=True)[:3]
    for c in aovsort:
        flag = "  <- high AOV, clears ROAS, underfunded, expand" if (c["roas"] and c["roas"] >= cold and c.get("spend_share") and c["spend_share"] < 5) else ""
        L.append("• %s: AOV %s · ROAS %s · Spend %s (share %s%%) · %d purch%s" % (
            nm(c), money(c["aov"]), r2(c["roas"]), money(c["spend"]), c["spend_share"], int(c["purch"]), flag))
    # ---- CREATIVE (by format, then video retention) ----
    L += ["", BAR, "*CREATIVE PERFORMANCE (by format)*"]
    for fmt in ("VIDEO", "IMAGE", "CATALOGUE"):
        fp = [c for c in rows if c["type"] == fmt]
        fsp = sum(c["spend"] for c in fp)
        if fsp < tot * 0.02: continue
        frev = sum(c["rev"] for c in fp); fpur = sum(c["purch"] for c in fp); flc = sum(c["lc"] for c in fp); fimp = sum(c["impr"] for c in fp)
        L.append("• %s: spend %s (share %d%%) · ROAS %s · %d purch · CPA %s · Outbound CTR %s%%" % (
            fmt.title(), money(fsp), round(fsp / tot * 100), r2(frev / fsp if fsp else 0), int(fpur),
            money(round(fsp / fpur) if fpur else 0), round(sum(c["octr"] * c["impr"] for c in fp) / fimp, 2) if fimp else 0))
    L.append("*Video retention* (cold video only, ranked vs your average, catalogue anomalies removed):")
    if videos and vavg:
        ranked = sorted(videos, key=lambda c: c["hook"], reverse=True)
        rankmap = {c["ad_id"]: i + 1 for i, c in enumerate(ranked)}
        for c in videos[:3]:
            vr = video_read(c, vavg, rankmap.get(c["ad_id"]), len(videos))
            if vr: L.append("• " + vr)
    else:
        L.append("_No clean cold video with enough real 3-second plays to read. Most spend runs through catalogue or dialogue ads, whose creative quality is unknown from this data._")
    # ---- FATIGUE (proven, with confidence) ----
    if fatiguing:
        L += ["", BAR, "*FATIGUE (evidenced)*"]
        for c in fatiguing[:3]:
            p = c.get("prev") or {}
            conf = "High" if (c.get("d_freq") and c["d_freq"] > 40 and c.get("d_octr") and c["d_octr"] < -30) else "Medium"
            L.append("• %s: frequency %s→%s (%s), Outbound CTR %s%%→%s%% (%s), CPM %s.  Same people, fewer clicks.  Confidence: %s.  Refresh the creative, do not just add budget." % (
                nm(c), p.get("freq"), c["freq"], sp(c.get("d_freq")), p.get("octr"), c["octr"], sp(c.get("d_octr")), sp(c.get("d_cpm")), conf))
    # ---- ANOMALIES ----
    L += ["", BAR, "*ANOMALIES, this should not happen*"]
    an = []
    if best_ctr and best_ctr.get("roas") and best_ctr["roas"] < s["roas"]:
        an.append("%s has the highest Outbound CTR (%s%%) yet ROAS %s is below the account %s.  It buys attention cheaply but does not convert it. The constraint is downstream of the click, unknown from this data whether page, offer or audience." % (nm(best_ctr), best_ctr["octr"], r2(best_ctr["roas"]), s["roas"]))
    if best_aov and best_aov.get("spend_share") and best_aov["spend_share"] < 5 and best_aov.get("roas") and best_aov["roas"] >= cold:
        an.append("%s has the biggest baskets (AOV %s) and clears the cold bar, but takes only %s%% of spend.  Underfunded, expand it in controlled steps." % (nm(best_aov), money(best_aov["aov"]), best_aov["spend_share"]))
    if videos:
        bh = max(videos, key=lambda c: c["hook"]);
        if bh["r50"] < vavg["r50"] * 0.8:
            an.append("%s has the best hook of your videos but its halfway retention is well below average.  Strong open, the body is throwing away the attention it buys." % nm(bh))
    if over and under:
        an.append("Budget is inverted: %s (ROAS %s) gets more spend than %s (ROAS %s).  Meta is funding the wrong ad." % (nm(over[0]), r2(over[0]["roas"]), nm(under[0]), r2(under[0]["roas"])))
    if not an: an.append("_Nothing contradictory in the data this week._")
    for a in an[:4]: L.append("• " + a)
    # ---- SCENARIO SIMULATIONS ----
    L += ["", BAR, "*SIMULATIONS (this week's numbers)*"]
    sims = []
    sims.append("CPM +20%% with CTR flat: CPC ~%s, blended ROAS ~%s.  Only ads currently above ROAS %s survive that; the rest go underwater." % (
        money(round((s.get("cpc") or 0) * 1.2)), r2(blended / 1.2), r2(blended / 1.2)))
    sims.append("Outbound CTR -15%% (creative fatigue): CPC ~%s, blended ROAS ~%s.  That is roughly %s EGP of revenue gone at current spend." % (
        money(round((s.get("cpc") or 0) / 0.85)), r2(blended * 0.85), money(round(s["revenue"] * 0.15))))
    if winners:
        w = winners[0]; nf = r2(w["freq"] * 1.4)
        sims.append("Double %s: reach only grows while frequency is under 2.0.  Projected frequency ~%s; it breaks when frequency crosses ~2.5 and CPM climbs, expect efficiency to hold to about +%s spend." % (
            nm(w), nf, money(round(w["spend"] * (2.0 / max(w["freq"], 0.1) - 1)))))
    if csp["NEW"] and roC["NEW"]:
        shift10 = round(tot * 0.10)
        sims.append("Raise New-audience spend share by 10%% (+%s): at New ROAS %s that is ~%s revenue, and it refills next week's retargeting pool.  Risk: New ROAS %s is %s the blended %s." % (
            money(shift10), r2(roC["NEW"]), money(round(shift10 * roC["NEW"])), r2(roC["NEW"]), "below" if roC["NEW"] < blended else "above", blended))
    for sm in sims[:4]: L.append("• " + sm)
    # ---- BUDGET MOVE PLAN (explicit) ----
    L += ["", BAR, "*BUDGET MOVE PLAN*"]
    rm = (over[:1] or bleeders[:1] or (cp[:1] if 'cp' in dir() else []))
    L.append("*Remove from:*")
    if rm:
        for c in rm:
            cutamt = round(c["spend"] * (0.5 if (c["roas"] and c["roas"] < s["roas"] * 0.6) else 0.3))
            L.append("• %s: now %s, cut %s.  ROAS %s vs account %s." % (nm(c), money(c["spend"]), money(cutamt), r2(c["roas"]), s["roas"]))
    else:
        L.append("• Nothing is clearly wasting. Hold.")
    L.append("*Add to:*")
    rm_ids = {c["ad_id"] for c in rm}
    addto = [c for c in scalable if c["ad_id"] not in rm_ids][:2]
    if addto:
        for c in addto:
            inc = "30%" if c["freq"] < 1.5 else ("20%" if c["freq"] < 1.8 else "15%")
            seg_note = ""
            if c["seg"] == over_seg: seg_note = "  Note: %s is already over-weighted, size this small and prioritise a New-audience winner." % SEGN[c["seg"]]
            elif c["seg"] == starved_seg: seg_note = "  This also refills %s, which is losing share." % SEGN[c["seg"]]
            L.append("• %s (%s): now %s, increase %s.  ROAS %s, CVR %s%%, CPMR %s vs %s avg, frequency %s has room.%s" % (
                nm(c), SEGN[c["seg"]], money(c["spend"]), inc, r2(c["roas"]), c["cvr"], money(c["cpmr"]), money(c.get("acc_cpmr")), c["freq"], seg_note))
    else:
        strong_sat = [c for c in sig if c["roas"] and c["roas"] >= s["roas"] and c["freq"] >= freq_ceiling(c) and c["ad_id"] not in rm_ids]
        if strong_sat:
            L.append("• No ad has strong ROAS *and* frequency headroom. %s are strong but saturated (frequency over ceiling), broaden the audience before adding budget, do not just raise spend." % ", ".join(nm(c) for c in strong_sat[:2]))
        else:
            L.append("• No clean winner to feed. Hold the freed budget until one proves out.")
    # ---- IF I HAD ONE HOUR IN ADS MANAGER ----
    L += ["", BAR, ":dart: *IF I HAD ONE HOUR MONDAY*"]
    kill = over[0] if over else (bleeders[0] if bleeders else None)
    fund = dest
    L.append("*09:00 Pause:* %s" % (("%s.  ROAS %s under the account, it is bleeding budget." % (nm(kill), r2(kill["roas"]))) if kill else "nothing, guardrails are holding."))
    L.append("*09:15 More budget:* %s" % (
        ("%s, +15%%.  ROAS %s with frequency headroom, this is where freed spend goes." % (nm(fund), r2(fund["roas"]))) if fund else "hold, no clear winner to feed."))
    if csp["NEW"] < psp["NEW"]:
        L.append("*09:30 Audiences:* raise New-audience budget back toward last week's mix; Meta over-rotated into %s and is starving acquisition." % SEGN[gained])
    else:
        L.append("*09:30 Audiences:* mix is fine, hold the New/Engaged/Existing split.")
    if overlaps:
        L.append("*09:45 Merge:* %s and %s are bidding against each other (combined %s), merge or mutually exclude." % (overlaps[0][1], overlaps[0][2], money(overlaps[0][0])))
    else:
        L.append("*09:45 Merge:* no overlap to fix.")
    watch = "New-audience reach growing, %s holding ROAS above %s at higher spend, and %s's CPM after any merge." % (
        nm(fund) if fund else "the top winner", blended, overlaps[0][1] if overlaps else "the top adset")
    L.append("*10:00 Check tomorrow:* %s" % watch)
    L.append("_%s_" % WIN_FOOT.get(DATES.get("win", "7day"), "Numbers are this period vs the one before."))
    return "\n".join(L)


# ----------------------- main -----------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    global SLACK_TOKEN, NUMID, STATUS
    if a.dry_run: SLACK_TOKEN = ""
    if not TOKEN: sys.stderr.write("META_ACCESS_TOKEN missing\n"); sys.exit(1)

    try:
        from zoneinfo import ZoneInfo; z = ZoneInfo(TZ)
    except Exception:
        z = datetime.timezone.utc
    now = datetime.datetime.now(z); y = (now - datetime.timedelta(days=1)).date()
    last = {"since": str(y - datetime.timedelta(days=6)), "until": str(y)}
    prev = {"since": str(y - datetime.timedelta(days=13)), "until": str(y - datetime.timedelta(days=7))}
    l3 = {"since": str(y - datetime.timedelta(days=2)), "until": str(y)}
    prev3 = {"since": str(y - datetime.timedelta(days=5)), "until": str(y - datetime.timedelta(days=3))}
    l1 = {"since": str(y), "until": str(y)}
    prev1 = {"since": str(y - datetime.timedelta(days=1)), "until": str(y - datetime.timedelta(days=1))}
    DATES["label"] = (fmt_day(y - datetime.timedelta(days=6)), fmt_day(y))
    DATES["p_label"] = (fmt_day(y - datetime.timedelta(days=13)), fmt_day(y - datetime.timedelta(days=7)))
    DATES["l3"] = (fmt_day(y - datetime.timedelta(days=2)), fmt_day(y))
    DATES["p3"] = (fmt_day(y - datetime.timedelta(days=5)), fmt_day(y - datetime.timedelta(days=3)))
    ACC = "spend,impressions,reach,frequency,cpm,ctr,actions,action_values"
    # the three reads the memo is written for, each to its own channel per brand
    WINDOWS = [
        ("daily", l1, prev1, (fmt_day(y), fmt_day(y)), (fmt_day(y - datetime.timedelta(days=1)),) * 2),
        ("3day",  l3, prev3, DATES["l3"], DATES["p3"]),
        ("7day",  last, prev, DATES["label"], DATES["p_label"]),
    ]

    report = {"generated_at": now.isoformat(), "timezone": TZ, "sample": False, "accounts": []}
    global SEGMAP
    for acct in get_accounts():
        cur_rows = get_insights(acct["id"], last)
        if not cur_rows: continue
        prev_rows = get_insights(acct["id"], prev)
        STATUS = get_ad_statuses(acct["id"])
        # audience truth: read every ad set's real targeting before any metric is built
        SEGMAP = build_segmap(acct["id"])
        sys.stderr.write("[seg] %s: %d adsets mapped\n" % (acct["name"], len(SEGMAP)))
        A = analyze(acct, cur_rows, prev_rows, STATUS)
        if A["summary"]["spend"] <= 0: continue
        report["accounts"].append(A)
        NUMID = acct["id"].replace("act_", "")
        ch = channel_for(acct["name"]); lch = channel_launch_for(acct["name"])
        ach = channel_advisor_for(acct["name"])
        sm = A["summary"]
        if a.daily or a.dry_run:
            # ---- THE MEMO, written three times: 1 day, 3 days, 7 days. Each to its own channel. ----
            save7 = (DATES["label"], DATES["p_label"])
            for win, cw, pw, lab, plab in WINDOWS:
                wch = channel_window_for(acct["name"], win)
                if not wch: continue
                cr = get_insights(acct["id"], cw)
                if not cr: continue
                pr = get_insights(acct["id"], pw)
                AW = analyze(acct, cr, pr, STATUS)
                if AW["summary"]["spend"] <= 0: continue
                DATES["label"], DATES["p_label"], DATES["win"] = lab, plab, win
                slack(wch, msg_advisor(AW))
                time.sleep(1)
            DATES["label"], DATES["p_label"] = save7
            DATES["win"] = "7day"

            # DAILY = rolling 3 days, not L7D. Build a 3-day analysis for the action digest + hook report.
            c3rows = get_insights(acct["id"], l3); p3rows = get_insights(acct["id"], prev3)
            A3 = analyze(acct, c3rows, p3rows, STATUS)
            DATES["label"], DATES["p_label"] = DATES["l3"], DATES["p3"]
            if A3["summary"]["spend"] > 0:
                slack(ch, msg_digest(A3))
                t3 = channel_3sec_for(acct["name"])
                if t3: slack(t3, msg_3sec(A3))
            a3 = get_insights(acct["id"], l3, level="account", fields=ACC)
            ap3 = get_insights(acct["id"], prev3, level="account", fields=ACC)
            m3 = metric(a3[0]) if a3 else None; p3m = metric(ap3[0]) if ap3 else None
            p3 = pulse_3day(acct, get_insights(acct["id"], l3, level="ad", fields=LITE_FIELDS, extra="ad_name,ad_id"),
                            get_insights(acct["id"], l3, level="adset", fields=LITE_FIELDS, extra="adset_name,adset_id"), m3, p3m)
            if p3: slack(ch, p3)
            DATES["label"], DATES["p_label"] = save7
            if lch:
                prev_ids = set(r.get("ad_id") for r in prev_rows)
                slack(lch, msg_launches(acct, A["creatives"], prev_ids, sm["roas"], sm["cpmr"], sm["cvr"]))
        if a.weekly or a.dry_run:
            aset_ins = get_insights(acct["id"], last, level="adset", fields=LITE_FIELDS, extra="adset_id,adset_name")
            spend_by = {}
            for r in aset_ins:
                sid = r.get("adset_id")
                if sid: spend_by[sid] = spend_by.get(sid, 0) + f(r.get("spend"))
            overlaps = audience_overlap(get_adset_targeting(acct["id"]), spend_by)
            slack(ach, msg_advisor(A, overlaps))
        time.sleep(1)

    for A in report["accounts"]:
        for c in A["creatives"]:
            c.pop("prev", None)
    save_json(DATA_PATH, report)
    sys.stderr.write("[done] %d accounts\n" % len(report["accounts"]))

if __name__ == "__main__":
    main()
