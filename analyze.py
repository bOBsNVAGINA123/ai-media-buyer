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
import os, sys, io, json, time, argparse, datetime, math, statistics as st
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
def _safe_pct(new, old, floor=50.0):
    """A percent change off a near zero base is a lie. Say what it is instead."""
    if not old or abs(old) < floor: return None
    return (new / old - 1) * 100.0

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
    "video_3_sec_watched_actions", "video_continuous_2_sec_watched_actions",
    "video_p25_watched_actions", "video_p50_watched_actions", "video_p75_watched_actions",
    "video_p95_watched_actions", "video_p100_watched_actions", "video_thruplay_watched_actions"])
LITE_FIELDS = "spend,reach,purchase_roas,actions,action_values"

HAS_3SEC = [True]     # flipped off if Meta rejects the field for this token

def _strip3(f):
    return ",".join(x for x in f.split(",") if x != "video_3_sec_watched_actions")


def get_insights(acct, tr, level="ad", fields=AD_FIELDS, extra=""):
    """If Meta will not give up the 3 second field for this token, drop it and carry on.
    One deprecated field must never take the whole report down."""
    out, after = [], None
    ff = fields + ("," + extra if extra else "")
    if not HAS_3SEC[0]:
        ff = _strip3(ff)
    while True:
        p = {"level": level, "fields": ff, "time_range": json.dumps(tr), "limit": 400}
        if after: p["after"] = after
        d = api_get("%s/insights" % acct, p)
        if "error" in d:
            msg = str(d.get("error"))
            if HAS_3SEC[0] and "video_3_sec_watched_actions" in ff and (
                    "video_3_sec" in msg or "nonexistent" in msg.lower()
                    or "invalid" in msg.lower() or "(#100)" in msg):
                HAS_3SEC[0] = False
                sys.stderr.write("[video] Meta rejected video_3_sec_watched_actions, "
                                 "falling back to 2 second continuous plays\n")
                ff = _strip3(ff); after = None; out = []
                continue
            sys.stderr.write("[insights] %s\n" % msg[:160])
            break
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

# ---------- Meta's OWN audience segment breakdown. The ground truth. No inference. ----------
# user_segment_key returns: prospecting | engaged | existing | unknown
SEGKEY = {"prospecting": "NEW", "engaged": "ENGAGED", "existing": "EXISTING", "unknown": "UNKNOWN"}
SEG_FIELDS = ",".join(["spend", "impressions", "reach", "frequency", "cpm", "ctr", "inline_link_click_ctr",
                       "outbound_clicks_ctr", "actions", "action_values"])

def get_seg_insights(acct, tr, level, extra=""):
    """Insights broken down by Meta's real audience segment, at campaign / adset / ad level."""
    out, after = [], None
    ff = SEG_FIELDS + ("," + extra if extra else "")
    while True:
        p = {"level": level, "fields": ff, "time_range": json.dumps(tr), "limit": 400,
             "breakdowns": "user_segment_key"}
        if after: p["after"] = after
        d = api_get("%s/insights" % acct, p)
        if "error" in d:
            sys.stderr.write("[seg] breakdown failed at %s: %s\n" % (level, str(d)[:120])); break
        out += d.get("data", [])
        after = d.get("paging", {}).get("cursors", {}).get("after")
        if not after: break
    return out

def seg_rollup(rows, keyfield, namefield):
    """{entity_key: {name, total_metrics, segments:{NEW/ENGAGED/EXISTING/UNKNOWN: metrics}}}"""
    ents = {}
    for r in rows:
        k = r.get(keyfield)
        if not k: continue
        sg = SEGKEY.get((r.get("user_segment_key") or "unknown").lower(), "UNKNOWN")
        m = metric(r)
        e = ents.setdefault(k, {"name": r.get(namefield) or "(unnamed)", "rows": [], "seg_rows": {}})
        e["rows"].append(m)
        e["seg_rows"].setdefault(sg, []).append(m)
    out = {}
    for k, e in ents.items():
        tot = full_metrics(e["rows"])
        segs = {}
        for sg, g in e["seg_rows"].items():
            if sum(c["spend"] for c in g) <= 0: continue
            segs[sg] = full_metrics(g)
        # the segment this entity actually leans on
        lead = max(segs, key=lambda s: segs[s]["spend"]) if segs else "UNKNOWN"
        # carry the id so we can deep link straight into Ads Manager
        out[k] = {"name": e["name"], "total": tot, "segments": segs, "lead": lead, "id": k}
    return out

def account_segments(rows):
    """Account-level spend + full metrics per REAL audience segment."""
    buckets = {}
    for r in rows:
        sg = SEGKEY.get((r.get("user_segment_key") or "unknown").lower(), "UNKNOWN")
        buckets.setdefault(sg, []).append(metric(r))
    tot = sum(sum(c["spend"] for c in g) for g in buckets.values()) or 1
    out = {}
    for sg in ("NEW", "ENGAGED", "EXISTING", "UNKNOWN"):
        g = buckets.get(sg, [])
        m = full_metrics(g)
        m["name"] = SEGN.get(sg, "Unknown")
        m["share"] = round(m["spend"] / tot * 100, 1) if tot else 0
        out[sg] = m
    return out


ADSEG = {}   # ad_id -> segment, taken straight from Meta's breakdown

def segmap_from_rows(seg_rows):
    """Per-ad segment, from Meta's own numbers. Whichever segment the ad actually spent in."""
    agg = {}
    for r in seg_rows:
        aid = str(r.get("ad_id") or "")
        if not aid: continue
        sg = SEGKEY.get((r.get("user_segment_key") or "unknown").lower(), "UNKNOWN")
        agg.setdefault(aid, {})
        agg[aid][sg] = agg[aid].get(sg, 0.0) + f(r.get("spend"))
    out = {}
    for aid, d in agg.items():
        real = {k: v for k, v in d.items() if k != "UNKNOWN"} or d
        if real: out[aid] = max(real, key=real.get)
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
    # HOOK RATE = 3 SECOND VIDEO VIEWS / IMPRESSIONS. video_play_actions is video PLAYS, not
    # 3 second views, and using it overstates the hook. Fall back to 2 second continuous only
    # if Meta will not give up the 3 second field, and say so on the card.
    # 3 SECOND VIDEO VIEWS. Meta exposes these at ad level as the "video_view" action type.
    v3 = pick(r.get("actions"), ["video_view"])
    v3_src = "3s"
    if not v3:
        v3 = first(r.get("video_continuous_2_sec_watched_actions"))
        v3_src = "2s" if v3 else "play"
    if not v3:
        v3 = first(r.get("video_play_actions")); v3_src = "play"
    p25 = first(r.get("video_p25_watched_actions"))
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
    # audience comes from META'S OWN breakdown by audience segment (user_segment_key).
    # targeting, then names, are only fallbacks if the breakdown returns nothing.
    seg = (ADSEG.get(str(r.get("ad_id")))
           or SEGMAP.get(str(r.get("adset_id")))
           or segment(blob))
    return {"ad_id": r.get("ad_id"), "ad_name": name, "campaign": camp, "adset": adset, "adset_id": r.get("adset_id"),
            "aud": "WARM" if warm else "COLD", "type": typ, "seg": seg,
            "spend": round(spend, 2), "impr": int(impr), "reach": int(reach),
            "freq": round(f(r.get("frequency")) or (impr / reach if reach else 0), 2),
            "cpm": round(f(r.get("cpm")), 2), "cpmr": round(spend / reach * 1000, 2) if reach else 0.0,
            "ctr": round(f(r.get("ctr")), 2), "octr": round(octr, 2),
            "lctr": round(f(r.get("inline_link_click_ctr")), 2),
            "purch": round(purch, 1), "rev": round(rev, 2), "lc": round(lc, 1), "atc": round(atc, 1),
            "cpa": round(spend / purch, 2) if purch else None,
            "roas": round(rev / spend, 2) if spend else 0.0,
            "aov": round(rev / purch, 2) if purch else None,
            "cvr": round(purch / lc * 100, 2) if lc else 0.0,
            "cpc": round(spend / lc, 2) if lc else 0.0,
            "atc_rate": round(atc / lc * 100, 1) if lc else 0.0,
            "hook": round(v3 / impr * 100, 1) if impr else 0.0,          # 3 sec views / impressions
            "hook_src": v3_src,
            "hold": round(thru / v3 * 100, 1) if v3 else 0.0,             # thruplays / 3 sec views
            "v3": round(v3), "thru": round(thru), "p25": round(p25), "p50": round(p50),
            "p75": round(p75), "p95": round(p95), "p100": round(p100),
            # retention as % of impressions (reliable), how far people actually watch
            "r25": round(p25 / v3 * 100, 1) if v3 else 0, "r50": round(p50 / v3 * 100, 1) if v3 else 0,
            "r75": round(p75 / v3 * 100, 1) if v3 else 0, "r95": round(p95 / v3 * 100, 1) if v3 else 0,
            "r100": round(p100 / v3 * 100, 1) if v3 else 0}

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
    c["prev"] = {k: p.get(k) for k in ("cpa", "roas", "freq", "octr", "spend", "cpm", "cpmr",
                                       "rev", "reach", "purch", "cvr", "aov", "impr", "lc",
                                       "ctr", "lctr", "atc", "atc_rate", "hook", "hold")} if p else None
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
            "ctr": round(sum(c["ctr"] * c["impr"] for c in ms) / impr, 2) if impr else 0,
            "octr": round(sum(c["octr"] * c["impr"] for c in ms) / impr, 2) if impr else 0,
            "lctr": round(sum(c.get("lctr", 0) * c["impr"] for c in ms) / impr, 2) if impr else 0,
            "impr": int(impr), "atc": round(atc),
            "freq": round(impr / reach, 2) if reach else 0,
            "hook": round(sum(c.get("v3", 0) for c in ms) / impr * 100, 1) if impr else 0,
            "hold": round(sum(c.get("thru", 0) for c in ms) / (sum(c.get("v3", 0) for c in ms) or 1) * 100, 1)}

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


# ===================== VISUAL BRIEFING CARD =====================
# Taste palette: warm bone canvas, ink, hairlines, muted pastels. No gradients, no emoji, no chartjunk.
BONE, INK, MUTED, FAINT, LINE = "#F7F6F3", "#1A1A18", "#787774", "#9B9A96", "#E4E2DE"
BLUE, AMBER, GREEN, RED, PAPER = "#8FB6D4", "#D9BC72", "#8CB392", "#D08C86", "#FFFFFF"
SEGC = {"NEW": BLUE, "ENGAGED": AMBER, "EXISTING": GREEN, "MIXED": FAINT}

def r2(x): return round(x or 0, 2)

def _clip(s, n=34):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n - 1] + "…"

def render_card(A, win):
    """One PNG that says: what moved, exactly WHERE it moved, and what to do about it."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
    except Exception as e:
        sys.stderr.write("[card] matplotlib unavailable: %s\n" % e); return None

    s = A["summary"]; rows = A["creatives"]; prev = s.get("prev") or {}
    name = A["account"]["name"]; d1, d2 = DATES["label"]; p1, p2 = DATES["p_label"]
    con = contrib(s, prev) or []
    mv = movers(rows, 5)
    sc = scenario(A)
    tot = sum(c["spend"] for c in rows) or 1

    plt.rcParams["font.family"] = ["DejaVu Sans"]
    fig = plt.figure(figsize=(11.2, 13.0), dpi=110, facecolor=BONE)

    def panel(x, y, w, h):
        fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.012",
                                          transform=fig.transFigure, facecolor=PAPER, edgecolor=LINE,
                                          linewidth=1.1, zorder=0))
    def txt(x, y, t, size=10, color=INK, weight="normal", ha="left", style="normal", mono=False):
        fig.text(x, y, t, size=size, color=color, weight=weight, ha=ha, style=style,
                 family=("DejaVu Sans Mono" if mono else "DejaVu Sans"), zorder=3)

    # ---------- header ----------
    txt(.062, .967, "%s  ·  %s" % (name.upper(), WIN_TITLE.get(win, "MEMO")), 10.5, FAINT, "bold", mono=True)
    txt(.062, .941, "Where the money moved.", 27, INK)
    txt(.062, .921, "%s to %s   vs   %s to %s   ·  all values EGP" % (d1, d2, p1, p2), 9.5, MUTED, mono=True)

    # ---------- KPI strip ----------
    kp = [("SPEND", money(s["spend"]), s.get("d_spend"), False),
          ("REVENUE", money(s["revenue"]), pct(s["revenue"], prev.get("rev")), False),
          ("ROAS", str(s["roas"]), s.get("d_roas"), False),
          ("CPA", money(s["cpa"]), s.get("d_cpa"), True),
          ("CVR", "%s%%" % s["cvr"], s.get("d_cvr"), False),
          ("CPMR", money(s["cpmr"]), s.get("d_cpmr"), True)]
    y0, ph = .838, .058
    panel(.055, y0, .89, ph)
    for i, (k, v, d, inv) in enumerate(kp):
        x = .075 + i * .148
        txt(x, y0 + ph - .019, k, 7.6, FAINT, "bold", mono=True)
        txt(x, y0 + .013, v, 15, INK)
        if d is None:
            txt(x + .085, y0 + .017, "-", 8, FAINT, mono=True)
        else:
            good = (d < 0) if inv else (d > 0)
            c = GREEN if (good and abs(d) > 2) else (RED if (not good and abs(d) > 2) else FAINT)
            txt(x + .085, y0 + .017, sp(d), 8.4, c, "bold", mono=True)

    # ---------- WHO moved ROAS ----------
    y0, ph = .690, .140
    panel(.055, y0, .43, ph)
    txt(.075, y0 + ph - .022, "WHO MOVED ROAS", 8, FAINT, "bold", mono=True)
    if con:
        for i, c in enumerate(con[:3]):
            yy = y0 + ph - .055 - i * .027
            txt(.075, yy, c["k"], 9.6, INK, "bold")
            bw = .21 * (c["pct"] / 100.0)
            fig.patches.append(Rectangle((.125, yy - .003), .21, .010, transform=fig.transFigure,
                                         facecolor="#EFEDE9", edgecolor="none", zorder=2))
            fig.patches.append(Rectangle((.125, yy - .003), bw, .010, transform=fig.transFigure,
                                         facecolor=(GREEN if c["help"] else RED), edgecolor="none", zorder=3))
            txt(.345, yy, "%d%%" % c["pct"], 9, INK, "bold", mono=True)
            txt(.378, yy, "%s%s" % ("+" if c["egp"] >= 0 else "-", money(abs(c["egp"]))),
                8.2, (GREEN if c["help"] else RED), mono=True)
        txt(.075, y0 + .009, "ROAS = CVR x AOV / CPC, so shares add to 100%.", 7.4, FAINT, style="italic")
    else:
        txt(.075, y0 + .06, "No prior period to attribute.", 9, MUTED)

    # ---------- AUDIENCE ----------
    panel(.515, y0, .43, ph)
    txt(.535, y0 + ph - .022, "SPEND BY AUDIENCE", 8, FAINT, "bold", mono=True)
    aud = [(sg, [c for c in rows if c["seg"] == sg]) for sg in ("NEW", "ENGAGED", "EXISTING")]
    for i, (sg, g) in enumerate(aud):
        yy = y0 + ph - .055 - i * .027
        spd = sum(c["spend"] for c in g); rv = sum(c["rev"] for c in g)
        sh = spd / tot * 100
        txt(.535, yy, SEGN[sg].split()[0], 9.4, INK, "bold")
        fig.patches.append(Rectangle((.625, yy - .003), .18, .010, transform=fig.transFigure,
                                     facecolor="#EFEDE9", edgecolor="none", zorder=2))
        fig.patches.append(Rectangle((.625, yy - .003), .18 * (sh / 100.0), .010, transform=fig.transFigure,
                                     facecolor=SEGC[sg], edgecolor="none", zorder=3))
        txt(.815, yy, "%.0f%%" % sh, 9, INK, "bold", mono=True)
        txt(.855, yy, ("%sx" % round(rv / spd, 2)) if spd else "nothing", 8.2, MUTED, mono=True)
    live = [sg for sg, g in aud if sum(c["spend"] for c in g) > 0]
    txt(.535, y0 + .009,
        ("Only %s is running. Nothing re-engages paid traffic." % SEGN[live[0]]) if len(live) <= 1
        else "Read from real ad set targeting, not names.", 7.4, (RED if len(live) <= 1 else FAINT), style="italic")

    # ---------- WHERE it happened (named) ----------
    y0, ph = .400, .265
    panel(.055, y0, .89, ph)
    txt(.075, y0 + ph - .024, "EXACTLY WHERE IT MOVED", 8, FAINT, "bold", mono=True)
    txt(.075, y0 + ph - .048, "Revenue change per ad vs the period before, biggest swing first.", 8.4, MUTED)
    if mv:
        mx = max(abs(m["d_rev"]) for m in mv) or 1
        cx = .50   # zero line
        for i, m in enumerate(mv):
            yy = y0 + ph - .086 - i * .036
            up = m["d_rev"] >= 0
            txt(.075, yy, _clip(m["name"], 30), 9.2, INK)
            fig.patches.append(Rectangle((.40, yy - .0035), .006, .011, transform=fig.transFigure,
                                         facecolor=SEGC.get(m["seg"], FAINT), edgecolor="none", zorder=3))
            w = .195 * (abs(m["d_rev"]) / mx)
            fig.patches.append(Rectangle((cx if up else cx - w, yy - .004), w, .012, transform=fig.transFigure,
                                         facecolor=(GREEN if up else RED), edgecolor="none", zorder=3))
            fig.patches.append(Rectangle((cx - .0006, yy - .009), .0012, .022, transform=fig.transFigure,
                                         facecolor=LINE, edgecolor="none", zorder=4))
            txt(.71, yy, "%s%s" % ("+" if up else "-", money(abs(m["d_rev"]))), 9, (GREEN if up else RED), "bold", mono=True)
            txt(.775, yy, "%s to %s ROAS" % (r2(m["prev_roas"]), r2(m["roas"])), 8, MUTED, mono=True)
            txt(.075, yy - .0125, "in %s" % _clip(m["adset"], 40), 7.4, FAINT, style="italic")
    else:
        txt(.075, y0 + .10, "No comparable prior period.", 9, MUTED)

    # ---------- DO THIS (the visual scenario) ----------
    y0, ph = .075, .295
    panel(.055, y0, .89, ph)
    txt(.075, y0 + ph - .026, "DO THIS ON MONDAY", 8, FAINT, "bold", mono=True)
    if sc:
        cut, fund = sc["cut"], sc["fund"]
        txt(.075, y0 + ph - .062, "Move %s from one ad to the other." % money(sc["freed"]), 16, INK)

        by = y0 + .150   # box row
        # CUT box
        fig.patches.append(FancyBboxPatch((.075, by), .38, .072, boxstyle="round,pad=0.003,rounding_size=0.008",
                                          transform=fig.transFigure, facecolor="#FDEBEC", edgecolor="#E9C9C6", linewidth=1, zorder=2))
        txt(.09, by + .052, "CUT 30%", 7.6, "#9F2F2D", "bold", mono=True)
        txt(.09, by + .030, _clip(cut["name"], 26), 10.5, INK, "bold")
        txt(.09, by + .012, "ROAS %s  ·  CPA %s  ·  CVR %s%%  ·  %s spend" % (
            r2(cut["roas"]), money(cut["cpa"]), cut["cvr"], money(cut["spend"])), 7.6, MUTED, mono=True)

        # arrow
        fig.text(.478, by + .034, "→", size=24, color=INK, ha="center", zorder=3)
        fig.text(.478, by + .014, money(sc["freed"]), size=8, color=MUTED, ha="center",
                 family="DejaVu Sans Mono", zorder=3)

        # FUND box
        fig.patches.append(FancyBboxPatch((.545, by), .38, .072, boxstyle="round,pad=0.003,rounding_size=0.008",
                                          transform=fig.transFigure, facecolor="#EDF3EC", edgecolor="#C7D9C6", linewidth=1, zorder=2))
        txt(.56, by + .052, "FUND IT", 7.6, "#346538", "bold", mono=True)
        txt(.56, by + .030, _clip(fund["name"], 26), 10.5, INK, "bold")
        txt(.56, by + .012, "ROAS %s  ·  CPA %s  ·  freq %s has room" % (
            r2(fund["roas"]), money(fund["cpa"]), fund["freq"]), 7.6, MUTED, mono=True)

        # projected revenue bars
        base, proj = sc["rev_now"], sc["rev_then"]
        mx = max(base, proj) or 1
        txt(.075, y0 + .112, "REVENUE IF YOU DO IT", 7.6, FAINT, "bold", mono=True)
        for i, (lbl, val, col) in enumerate([("now", base, "#CFCCC6"), ("after the move", proj, GREEN)]):
            yy = y0 + .080 - i * .030
            fig.patches.append(Rectangle((.21, yy - .005), .55 * (val / mx), .017, transform=fig.transFigure,
                                         facecolor=col, edgecolor="none", zorder=3))
            txt(.075, yy, lbl, 8.8, MUTED)
            txt(.79, yy, money(val), 9.4, INK, "bold", mono=True)
        txt(.075, y0 + .016, "Worth about %s more revenue at the same spend." % money(sc["gain"]),
            9, "#346538", "bold", style="italic")
    else:
        txt(.075, y0 + .16, "Nothing is clearly wasting and nothing is clearly scalable.", 13, INK)
        txt(.075, y0 + .13, "Hold the budget where it is.", 9.5, MUTED)

    txt(.062, .035, "AI Media Buyer  ·  audience from real ad set targeting  ·  lookalikes count as New",
        7.4, FAINT, mono=True)

    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BONE, bbox_inches=None)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


IMG_DIR = os.path.join(DOCS, "img")

# ===================== HISTORICAL BASELINE (anomaly or normal?) =====================
WIN_DAYS = {"daily": 1, "3day": 3, "7day": 7}


def get_daily_series(acct, days=30):
    """This account's own daily history. It is the only honest definition of normal."""
    p = {"level": "account", "time_increment": 1, "date_preset": "last_30d", "limit": 60,
         "fields": "spend,impressions,reach,frequency,cpm,ctr,inline_link_click_ctr,"
                   "outbound_clicks_ctr,video_play_actions,video_thruplay_watched_actions,"
                   "actions,action_values"}
    d = api_get("%s/insights" % acct, p)
    out = []
    for r in d.get("data", []) or []:
        spend = f(r.get("spend")); rev = pick(r.get("action_values"), PURCH)
        purch = pick(r.get("actions"), PURCH); lc = pick(r.get("actions"), ["link_click"])
        impr = f(r.get("impressions")); reach = f(r.get("reach"))
        octr = first(r.get("outbound_clicks_ctr")) or f(r.get("inline_link_click_ctr"))
        atc = pick(r.get("actions"), ATC)
        v3 = pick(r.get("actions"), ["video_view"]) or first(r.get("video_play_actions"))
        thru = first(r.get("video_thruplay_watched_actions"))
        out.append({"date": r.get("date_start"), "spend": spend, "rev": rev,
                    "atc": atc, "atc_rate": atc / lc * 100 if lc else 0,
                    "ctr": f(r.get("ctr")), "lctr": f(r.get("inline_link_click_ctr")),
                    "hook": v3 / impr * 100 if impr else 0,
                    "hold": thru / v3 * 100 if v3 else 0,
                    "roas": rev / spend if spend else 0, "cpc": spend / lc if lc else 0,
                    "cvr": purch / lc * 100 if lc else 0, "aov": rev / purch if purch else 0,
                    "cpa": spend / purch if purch else 0,
                    "cpm": spend / impr * 1000 if impr else 0,
                    "cpmr": spend / reach * 1000 if reach else 0,
                    "freq": f(r.get("frequency")) or (impr / reach if reach else 0),
                    "octr": octr, "reach": reach, "impr": impr, "purch": purch, "lc": lc})
    out.sort(key=lambda d: d.get("date") or "")
    return out


# ---------- what normal looks like, in percent, never in standard deviations ----------
def pctile(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return None
    i = (len(xs) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    if lo == hi: return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def normal_band(series, key, val, exclude_last=True):
    """Where does today sit against this account's own recent days, expressed the only way
    a media buyer thinks: percent. Never standard deviations."""
    xs = [d.get(key) for d in series if d.get(key)]
    if exclude_last and len(xs) > 8: xs = xs[:-1]
    if len(xs) < 7 or not val: return None
    mid = pctile(xs, 0.5)
    lo, hi = pctile(xs, 0.10), pctile(xs, 0.90)
    if not mid: return None
    vs = (val / mid - 1) * 100.0
    lo_p, hi_p = (lo / mid - 1) * 100.0, (hi / mid - 1) * 100.0
    inside = lo <= val <= hi
    if inside:
        verdict = "WITHIN THE NORMAL RANGE"
    elif val < lo:
        verdict = "BELOW THE NORMAL RANGE"
    else:
        verdict = "ABOVE THE NORMAL RANGE"
    return {"val": val, "mid": mid, "lo": lo, "hi": hi, "vs": vs,
            "lo_p": lo_p, "hi_p": hi_p, "inside": inside, "verdict": verdict, "xs": xs}


def rolling(series, key, n=7):
    """A rolling average. Frequency and CPMR mean nothing on one day."""
    xs = [d.get(key) or 0 for d in series]
    out = []
    for i in range(len(xs)):
        w = xs[max(0, i - n + 1):i + 1]
        out.append(sum(w) / len(w) if w else 0)
    return out


def wow(series, key, n=7):
    """This 7 days against the 7 before, in percent. That is how you judge a trend."""
    xs = [d.get(key) or 0 for d in series if d.get(key) is not None]
    if len(xs) < n * 2: return None
    cur = sum(xs[-n:]) / n
    pre = sum(xs[-2 * n:-n]) / n
    if not pre: return None
    return {"cur": cur, "prev": pre, "chg": (cur / pre - 1) * 100.0}


def zscore(series, key, val):
    """How far outside normal is this. Returns (z, verdict, mean)."""
    xs = [d[key] for d in series if d.get(key)]
    if len(xs) < 7 or val in (None, 0): return None
    m = st.mean(xs)
    try: s = st.pstdev(xs)
    except Exception: s = 0
    if not s: return None
    z = (val - m) / s
    if abs(z) >= 2.0: v = "ANOMALY"
    elif abs(z) >= 1.2: v = "UNUSUAL"
    else: v = "NORMAL"
    return {"z": round(z, 1), "verdict": v, "mean": m, "val": val, "key": key}


def fatiguing(rows, min_spend=800):
    """Fatigue is not one number. Name the exact mechanism: the audience ran out,
    the creative stopped earning the click, or the auction got more expensive."""
    out = []
    for c in rows:
        p = c.get("prev")
        if not p or c["spend"] < min_spend: continue
        d = {"freq": pct(c["freq"], p.get("freq") or 0),
             "octr": pct(c["octr"], p.get("octr") or 0),
             "reach": pct(c["reach"], p.get("reach") or 0),
             "cpm": pct(c["cpm"], p.get("cpm") or 0),
             "cpc": pct(c["cpc"], (p.get("spend") or 0) / (p.get("lc") or 1) if p.get("lc") else 0),
             "cvr": pct(c["cvr"], p.get("cvr") or 0),
             "roas": pct(c["roas"], p.get("roas") or 0)}
        if d["freq"] is None or d["octr"] is None: continue
        if not (d["freq"] >= 10 and d["octr"] <= -10): continue

        # the exact reasons, in plain language, only the ones that are actually true
        why = []
        if d["reach"] is not None and d["reach"] <= -10:
            why.append("reach fell %s to %s while frequency rose %s: Meta ran out of new people to show it to, so it is re-serving the same ones"
                       % (_dd(d["reach"]), "{:,.0f}".format(c["reach"]), _dd(d["freq"])))
        elif c["freq"] >= 3.0:
            why.append("frequency is %.1f, up %s: the same people are seeing it over and over"
                       % (c["freq"], _dd(d["freq"])))
        else:
            why.append("frequency up %s to %.1f" % (_dd(d["freq"]), c["freq"]))
        why.append("outbound CTR fell %s, from %s to %s: they have seen the creative and stopped clicking it"
                   % (_dd(d["octr"]), _pctv(p.get("octr")), _pctv(c["octr"])))
        if d["cpm"] is not None and d["cpm"] >= 8:
            why.append("CPM rose %s to %s: the auction is charging more for the same impression"
                       % (_dd(d["cpm"]), "{:,.0f}".format(c["cpm"])))
        if d["cpc"] is not None and d["cpc"] >= 8:
            why.append("CPC rose %s to %.2f: fewer clicks per impression means each click costs more"
                       % (_dd(d["cpc"]), c["cpc"]))
        if d["cvr"] is not None and d["cvr"] <= -10:
            why.append("CVR fell %s to %.2f%%: the clicks that are left are worse quality"
                       % (_dd(d["cvr"]), c["cvr"]))
        else:
            why.append("CVR held at %.2f%% (%s): the landing page is not the problem, the ad is"
                       % (c["cvr"], _dd(d["cvr"])))

        # the single dominant mechanism
        if d["reach"] is not None and d["reach"] <= -10 and c["freq"] >= 2.5:
            verdict = "AUDIENCE EXHAUSTED"
        elif d["cvr"] is not None and d["cvr"] <= -20:
            verdict = "CREATIVE AND INTENT BOTH DECAYING"
        elif d["cpm"] is not None and d["cpm"] >= 15:
            verdict = "AUCTION GOT EXPENSIVE"
        else:
            verdict = "CREATIVE WORN OUT"
        out.append({"name": safe(c["ad_name"]), "seg": c["seg"], "freq": c["freq"], "pfreq": p.get("freq"),
                    "octr": c["octr"], "poctr": p.get("octr"), "spend": c["spend"], "roas": c["roas"],
                    "reach": c["reach"], "preach": p.get("reach"), "cpm": c["cpm"], "cvr": c["cvr"],
                    "d": d, "why": why, "verdict": verdict,
                    "score": abs(d["octr"]) + d["freq"] + (c["spend"] / 1000.0)})
    return sorted(out, key=lambda x: -x["score"])[:6]


def _dd(v):
    if v is None: return "n/a"
    return ("+%.0f%%" % v) if v >= 0 else ("%.0f%%" % v)


# ===================== VISUAL CARDS (multi image) =====================
# Type is deliberately large. If it cannot be read on a phone it is not a briefing.
F_TITLE, F_SUB, F_H, F_KPI, F_KL, F_ROW, F_HD, F_NOTE = 30, 15, 16, 26, 12, 14, 12, 13

plt = None
def _mpl():
    global plt
    if plt is None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as _p
        plt = _p
    return plt

def _fig(h=13.0):
    fig = plt.figure(figsize=(13.0, h), dpi=110, facecolor=BONE)
    return fig

def _png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BONE, bbox_inches=None)
    plt.close(fig)
    return buf.getvalue()

def _head(fig, A, win, title, sub):
    # The figure is 13in wide with .055/.945 margins, so a subtitle longer than ~115 chars
    # runs off the canvas. Clip it rather than let it bleed off the edge.
    h = fig.get_size_inches()[1]
    def y(px): return 1.0 - (px / (h * 110.0))     # px from the top, at 110 dpi
    fig.text(.055, y(38), "%s  ·  %s" % (A["account"]["name"].upper(), WIN_TITLE.get(win, "MEMO")),
             fontsize=F_SUB, color=MUTED, family="DejaVu Sans")
    fig.text(.055, y(76), title, fontsize=F_TITLE, color=INK, family="DejaVu Serif",
             weight="bold")
    fig.text(.055, y(103), _clip(sub, 113), fontsize=F_NOTE, color=MUTED, family="DejaVu Sans")
    fig.lines.append(plt.Line2D([.055, .945], [y(115), y(115)], color=LINE, lw=1,
                                transform=fig.transFigure, figure=fig))

def _foot(fig, win):
    fig.text(.055, .022, WIN_FOOT.get(win, ""), fontsize=10.5, color=FAINT,
             family="DejaVu Sans", style="italic")
    fig.text(.945, .022, "Audience is Meta's own breakdown by audience segment.", fontsize=10.5,
             color=FAINT, family="DejaVu Sans", style="italic", ha="right")

# A panel's title owns the top band. NOTHING else may be drawn above PTOP. Every overlap
# bug in this file came from a caller writing a row at the same y as the panel title.
PTOP = 84.0

def _panel(fig, y0, h, title, x0=.055, w=.89):  # x0/w let a panel sit beside a chart
    ax = fig.add_axes([x0, y0, w, h]); ax.set_axis_off()
    ax.set_xlim(0, 100); ax.set_ylim(0, 100)
    ax.add_patch(plt.Rectangle((0, 0), 100, 100, facecolor=PAPER, edgecolor=LINE, lw=1))
    if title:
        ax.text(1.6, 94.5, title.upper(), fontsize=F_H, color=INK, family="DejaVu Sans",
                weight="bold", va="center")
        ax.plot([1.6, 98.4], [89.5, 89.5], color=LINE, lw=1, zorder=1)
    return ax

def _d(v):
    if v is None: return ""
    return ("+%.0f%%" % v) if v >= 0 else ("%.0f%%" % v)

# Business impact, not mathematical direction. Down is not automatically good.
LOWER_IS_BETTER = {"cpc", "cpm", "cpmr", "cpa", "cpp"}
NEUTRAL = {"spend", "impr", "reach", "lc"}          # inputs, not outcomes
FREQ_HEALTHY = 3.0

def impact(key, chg, val=None):
    """Colour by what it does to the business."""
    if chg is None or abs(chg) < 2: return MUTED
    if key in NEUTRAL: return BLUE                   # you chose this, it is not good or bad
    if key == "freq":
        if val is not None and val >= FREQ_HEALTHY and chg > 0: return RED
        return MUTED if abs(chg) < 8 else (RED if chg > 0 else GREEN)
    if key in LOWER_IS_BETTER: return GREEN if chg < 0 else RED
    return GREEN if chg > 0 else RED                 # revenue, roas, cvr, aov, ctr, hook, hold, atc

def _dc(v, up_good=True):
    if v is None or abs(v) < 1: return MUTED
    good = (v > 0) if up_good else (v < 0)
    return GREEN if good else RED

def _n(v, dp=0):
    if v is None: return "n/a"
    return ("{:,.%df}" % dp).format(v)

def _pctv(v):
    """A zero here means Meta did not return it, not that nobody clicked. Say so."""
    return "n/a" if not v else "%.2f%%" % round(v, 2)

def _k(v):
    """Compact money. A briefing is read, not audited."""
    if v is None: return "n/a"
    v = float(v)
    if abs(v) >= 1e6: return "%.2fM" % (v / 1e6)
    if abs(v) >= 10000: return "%.1fk" % (v / 1000.0)
    return "{:,.0f}".format(v)


# ---------- shared ----------
def dpc(m, p, k):
    if not p: return None
    return pct(m.get(k) or 0, p.get(k) or 0)

def m_cpc(m):
    if m.get("cpc"): return m["cpc"]
    lc = m.get("lc") or 0
    return (m.get("spend") or 0) / lc if lc else 0

def _wrap(t, n):
    words = (t or "").split(); out, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n: out.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    if cur: out.append(cur)
    return out


def waterfall(s, p):
    """Revenue = spend / CPC x CVR x AOV. An identity, so the move splits across the four
    levers with nothing left over."""
    if not p: return None
    def g(d, k):
        try: return float(d.get(k) or 0)
        except Exception: return 0.0
    cur = {"spend": g(s, "spend"), "cpc": m_cpc(s), "cvr": g(s, "cvr"), "aov": g(s, "aov")}
    pre = {"spend": g(p, "spend"), "cpc": m_cpc(p), "cvr": g(p, "cvr"), "aov": g(p, "aov")}
    for k in cur:
        if cur[k] <= 0 or pre[k] <= 0: return None
    rev, prev_rev = g(s, "rev"), g(p, "rev")
    if rev <= 0 or prev_rev <= 0: return None
    w = {"SPEND": math.log(cur["spend"] / pre["spend"]), "CPC": -math.log(cur["cpc"] / pre["cpc"]),
         "CVR": math.log(cur["cvr"] / pre["cvr"]), "AOV": math.log(cur["aov"] / pre["aov"])}
    tot = sum(w.values()); d_rev = rev - prev_rev
    if abs(tot) < 1e-9: return None
    LK = {"SPEND": "spend", "CPC": "cpc", "CVR": "cvr", "AOV": "aov"}
    steps = [{"k": k, "egp": d_rev * w[k] / tot,
              "pct": round(abs(w[k]) / sum(abs(v) for v in w.values()) * 100),
              "rev_pct": (d_rev * w[k] / tot) / prev_rev * 100.0,
              "chg": pct(cur[LK[k]], pre[LK[k]])} for k in ("SPEND", "CPC", "CVR", "AOV")]
    return {"prev": prev_rev, "now": rev, "steps": steps, "d_rev": d_rev,
            "d_pct": (rev / prev_rev - 1) * 100.0}


def plan(A):
    """Cut what is under the account, fund what is over it, at the same total spend."""
    s = A["summary"]; rows = A["creatives"]; acc = s["roas"] or 0
    sig = [c for c in rows if (c["spend"] or 0) >= 500 and c["lc"] >= 10]
    cut = sorted([c for c in sig if c["roas"] and c["roas"] < acc * 0.8],
                 key=lambda c: (c["roas"] or 0))[:5]
    fund = sorted([c for c in sig if c["roas"] and c["roas"] >= acc
                   and c["freq"] < freq_ceiling(c) and (c["purch"] or 0) >= 2],
                  key=lambda c: -((c["roas"] or 0) * (c["spend"] ** 0.3)))[:5]
    cut = [c for c in cut if c["ad_id"] not in {x["ad_id"] for x in fund}]
    if not cut or not fund: return None
    freed = sum(c["spend"] * 0.30 for c in cut)
    fspend = sum(c["spend"] for c in fund) or 1
    gain = sum(freed * (c["spend"] / fspend) * (c["roas"] or 0) for c in fund) \
         - sum(c["spend"] * 0.30 * (c["roas"] or 0) for c in cut)
    rev = s["rev"]
    return {"cut": cut, "fund": fund, "freed": round(freed), "gain": round(gain),
            "rev_now": round(rev), "rev_then": round(rev + gain),
            "gain_pct": (gain / rev * 100.0) if rev else 0}


def mix_split(A):
    """Did the audience mix move the account, or did the audiences themselves get better?
    Blended ROAS = sum(share_i x roas_i). Hold one side still and you isolate the other."""
    segs = A.get("segs") or {}; prev = A.get("segs_prev") or {}
    ks = [k for k in ("NEW", "ENGAGED", "EXISTING") if segs.get(k) and prev.get(k)]
    if len(ks) < 2: return None
    sn = sum(segs[k]["spend"] for k in ks) or 1
    sp_ = sum(prev[k]["spend"] for k in ks) or 1
    w_now = {k: segs[k]["spend"] / sn for k in ks}
    w_pre = {k: prev[k]["spend"] / sp_ for k in ks}
    r_now = {k: segs[k]["roas"] or 0 for k in ks}
    r_pre = {k: prev[k]["roas"] or 0 for k in ks}
    b_now = sum(w_now[k] * r_now[k] for k in ks)
    b_pre = sum(w_pre[k] * r_pre[k] for k in ks)
    if not b_pre: return None
    b_mix = sum(w_now[k] * r_pre[k] for k in ks)     # mix moved, performance held
    mix_e = (b_mix - b_pre) / b_pre * 100.0
    perf_e = (b_now - b_mix) / b_pre * 100.0
    return {"blended_now": b_now, "blended_prev": b_pre,
            "total": (b_now / b_pre - 1) * 100.0, "mix": mix_e, "perf": perf_e,
            "w_now": w_now, "w_pre": w_pre, "r_now": r_now, "r_pre": r_pre, "ks": ks}


FUNNEL = [("hook", "HOOK RATE", "%.1f%%", True, "3 second views over impressions"),
          ("hold", "HOLD RATE", "%.1f%%", True, "thruplays over 3 second views"),
          ("ctr", "CTR (ALL)", "%.2f%%", True, "all clicks over impressions"),
          ("lctr", "LINK CTR", "%.2f%%", True, "link clicks over impressions"),
          ("octr", "OUTBOUND CTR", "%.2f%%", True, "clicks that actually left Meta"),
          ("atc_rate", "ATC RATE", "%.1f%%", True, "add to carts over link clicks"),
          ("cvr", "CVR", "%.2f%%", True, "purchases over link clicks"),
          ("aov", "AOV", "%s", True, "revenue per purchase"),
          ("roas", "ROAS", "%.2fx", True, "revenue over spend")]

COST = [("cpm", "CPM", False), ("cpmr", "CPMR", False), ("cpc", "CPC", False),
        ("freq", "FREQUENCY", False)]


def funnel_read(m, p):
    """Walk the funnel top to bottom and find the first stage that broke.
    Then say what that means, because a metric on its own says nothing."""
    if not p: return None
    st = []
    for k, lab, fmt, ug, expl in FUNNEL:
        now = m_cpc(m) if k == "cpc" else m.get(k)
        pre = m_cpc(p) if k == "cpc" else p.get(k)
        st.append({"k": k, "lab": lab, "now": now or 0, "prev": pre or 0,
                   "chg": pct(now or 0, pre or 0), "expl": expl})
    d = {x["k"]: (x["chg"] if x["chg"] is not None else 0) for x in st}

    # where did it break. First stage down more than 5 percent wins, top of funnel first.
    broke = next((x for x in st if (x["chg"] or 0) <= -5), None)
    lines = []
    if d.get("cvr", 0) <= -5 and d.get("atc_rate", 0) <= -5:
        lines.append("CVR and ATC rate fell together, so the break is *above* checkout: the product page, the price, the offer, or the quality of the traffic you are sending.")
    elif d.get("cvr", 0) <= -5 and d.get("atc_rate", 0) > -5:
        lines.append("CVR fell but ATC rate held, so people still added to cart and then did not buy. The break is *at checkout*: payment, shipping cost, or purchase intent.")
    elif d.get("cvr", 0) > 5 and d.get("atc_rate", 0) > 5:
        lines.append("ATC rate and CVR both rose. The page and the offer are converting better than they were.")
    if d.get("lctr", 0) <= -5 or d.get("octr", 0) <= -5:
        lines.append("Link CTR fell, so the creative or the audience is no longer producing qualified traffic. Fewer of the right people are clicking through.")
    if d.get("hook", 0) <= -5:
        lines.append("Hook rate fell, so the first 3 seconds are losing people. That is a thumbstop problem, not a targeting problem.")
    elif d.get("hold", 0) <= -5:
        lines.append("Hook held but hold rate fell, so the opening works and the body does not. Re-cut the middle, keep the hook.")
    if not lines:
        lines.append("No stage of the funnel broke by more than 5 percent. Whatever moved revenue did not come from the funnel.")
    return {"stages": st, "broke": broke, "lines": lines, "d": d}


def decay_watch(A):
    """A creative that used to beat the account and now does not. Compare each one against
    its own past, and name the metric that is responsible. Never make him hunt for it."""
    acc = A["summary"]["roas"] or 0
    out = []
    for c in A["creatives"]:
        p = c.get("prev")
        if not p or (c["spend"] or 0) < 400: continue
        was_good = (p.get("roas") or 0) >= acc
        now_bad = (c["roas"] or 0) < acc
        if not (was_good and now_bad): continue
        checks = [("hook", "Hook rate", c.get("hook"), p.get("hook"), True),
                  ("hold", "Hold rate", c.get("hold"), p.get("hold"), True),
                  ("ctr", "CTR (all)", c.get("ctr"), p.get("ctr"), True),
                  ("lctr", "Link CTR", c.get("lctr"), p.get("lctr"), True),
                  ("octr", "Outbound CTR", c.get("octr"), p.get("octr"), True),
                  ("atc_rate", "ATC rate", c.get("atc_rate"), p.get("atc_rate"), True),
                  ("cvr", "CVR", c.get("cvr"), p.get("cvr"), True),
                  ("aov", "AOV", c.get("aov"), p.get("aov"), True),
                  ("cpm", "CPM", c.get("cpm"), p.get("cpm"), False),
                  ("cpmr", "CPMR", c.get("cpmr"), p.get("cpmr"), False),
                  ("freq", "Frequency", c.get("freq"), p.get("freq"), False)]
        rows = []
        for k, lab, now, pre, ug in checks:
            ch = pct(now or 0, pre or 0)
            if ch is None: continue
            hurt = (-ch) if ug else ch          # how much this metric hurt, in percent
            rows.append({"k": k, "lab": lab, "now": now or 0, "prev": pre or 0,
                         "chg": ch, "hurt": hurt, "up_good": ug})
        if not rows: continue
        culprit = max(rows, key=lambda r: r["hurt"])
        if culprit["hurt"] < 5: continue
        out.append({"name": safe(c["ad_name"]), "ad_id": c["ad_id"], "seg": c["seg"], "spend": c["spend"],
                    "roas": c["roas"], "proas": p.get("roas"),
                    "d_roas": pct(c["roas"], p.get("roas") or 0),
                    "rows": rows, "culprit": culprit,
                    # both periods, so the card can name the stage that broke without guessing
                    "now": c, "pre": p, "dx": dx_stage(c, p),
                    "d_rev": (c.get("rev") or 0) - (p.get("rev") or 0),
                    "loss": (p.get("roas") or 0) - (c["roas"] or 0)})
    return sorted(out, key=lambda x: -(x["loss"] * x["spend"]))[:3]


def allocate(A):
    """An actual allocation, in percent. A segment earns budget when it returns above the
    account AND still has room (frequency under the healthy ceiling). Nothing else."""
    segs = A.get("segs") or {}
    ks = [k for k in ("NEW", "ENGAGED", "EXISTING") if segs.get(k) and segs[k].get("share", 0) >= 1]
    if len(ks) < 2: return None
    acc = A["summary"]["roas"] or 0
    tot = sum(segs[k]["spend"] for k in ks) or 1
    now = {k: segs[k]["spend"] / tot for k in ks}
    score = {}
    for k in ks:
        m = segs[k]
        r = (m["roas"] or 0) / (acc or 1)                     # how much better than the account
        fq = m.get("freq") or 0
        room = max(0.0, 1.0 - fq / FREQ_HEALTHY)              # 0 when saturated, 1 when empty
        score[k] = max(0.05, r * (0.35 + 0.65 * room))        # returns, weighted by whether it can take it
    ssum = sum(score.values()) or 1
    raw = {k: score[k] / ssum for k in ks}
    # never move more than 10 points of the account in one go
    target = {k: now[k] + max(-0.10, min(0.10, raw[k] - now[k])) for k in ks}
    tsum = sum(target.values()) or 1
    target = {k: target[k] / tsum for k in ks}
    up = sorted(ks, key=lambda k: -(target[k] - now[k]))[0]
    dn = sorted(ks, key=lambda k: (target[k] - now[k]))[0]
    m_up, m_dn = segs[up], segs[dn]
    why = ("%s earns more budget: %.2fx against the account's %.2fx at frequency %.1f, so it still has room. "
           "%s gives budget up: %.2fx at frequency %.1f." % (
               SEGN[up], r2(m_up["roas"]), r2(acc), r2(m_up.get("freq")),
               SEGN[dn], r2(m_dn["roas"]), r2(m_dn.get("freq"))))
    return {"now": now, "target": target, "order": ks, "why": why, "up": up, "dn": dn}


# ---------- what a winner actually is. Printed on the card, never assumed. ----------
EVIDENCE = {"spend": 1500, "purch": 5, "lc": 60}     # over the last 7 days, not one day
DEF_WINNER = ("WINNER = last 7 days: spend over %s, at least %d purchases, ROAS at or above the "
              "account, CPMR at or below the account, and frequency under %.1f. "
              "Top quartile of the account by a composite of ROAS, CPP, CPMR, CVR and outbound CTR."
              % (money(EVIDENCE["spend"]), EVIDENCE["purch"], FREQ_HEALTHY))


def proven(b):
    """Enough money and enough purchases, over 7 days, to trust the number at all."""
    if not b: return False
    return ((b.get("spend") or 0) >= EVIDENCE["spend"]
            and (b.get("purch") or 0) >= EVIDENCE["purch"]
            and (b.get("lc") or 0) >= EVIDENCE["lc"])


def card_of(c, b, acc):
    """The whole card for one ad, every metric against the account. Never one metric alone."""
    def rel(v, ref, lower_better=False):
        if not v or not ref: return None
        return (ref / v - 1) * 100.0 if lower_better else (v / ref - 1) * 100.0
    return {
        "spend": b.get("spend") or 0, "rev": b.get("rev") or 0, "purch": b.get("purch") or 0,
        "roas": b.get("roas") or 0, "cpp": b.get("cpa"), "cpmr": b.get("cpmr") or 0,
        "cvr": b.get("cvr") or 0, "octr": b.get("octr") or 0, "cpc": m_cpc(b),
        "aov": b.get("aov") or 0, "freq": b.get("freq") or 0, "hook": b.get("hook") or 0,
        "hold": b.get("hold") or 0, "ctr": b.get("ctr") or 0,
        "v_roas": rel(b.get("roas"), acc.get("roas")),
        "v_cpp": rel(b.get("cpa"), acc.get("cpa"), True),
        "v_cpmr": rel(b.get("cpmr"), acc.get("cpmr"), True),
        "v_cvr": rel(b.get("cvr"), acc.get("cvr")),
        "v_octr": rel(b.get("octr"), acc.get("octr")),
        "v_cpc": rel(m_cpc(b), m_cpc(acc), True),
        "v_hook": rel(b.get("hook"), acc.get("hook")),
    }


# ======================= WHY DID IT MOVE. NAME THE CAUSE. =======================
# A number tells you WHAT. It does not tell you WHERE TO GO. Every drop is one of five
# things, and each one sends you to a different place. The funnel decides which.
#
#   AD  -> hook, hold, CTR      : they are not clicking. The creative is the problem.
#   AUCTION -> CPM, CPC, freq   : the same people cost more. Fatigue or competition.
#   SITE -> ATC rate            : they clicked and did not add to cart. The page.
#   CHECKOUT -> CVR with ATC ok : they added to cart and did not buy. Price, shipping, checkout.
#   BASKET -> AOV               : same buyers, smaller order. Mix or discounting.

def _ch(now, pre, k, lower_better=False):
    a, b = now.get(k), (pre or {}).get(k)
    if not a or not b: return None
    d = (a / b - 1) * 100.0
    return -d if lower_better else d

def dx_stage(now, pre):
    """Return (CAUSE, headline, what to actually go and do). Ordered by where in the funnel
    the break happens: earliest break wins, because it explains everything after it."""
    if not pre: return None
    hook = _ch(now, pre, "hook"); hold = _ch(now, pre, "hold")
    octr = _ch(now, pre, "octr"); atcr = _ch(now, pre, "atc_rate")
    cvr = _ch(now, pre, "cvr");   aov = _ch(now, pre, "aov")
    cpm = _ch(now, pre, "cpm");   freq = _ch(now, pre, "freq")
    cpc = _ch(now, pre, "cpc")
    f_now = now.get("freq") or 0

    # FATIGUE has a signature: the same people, seen more often, costing more, clicking less.
    if (freq is not None and freq >= 8 and f_now >= FREQ_HEALTHY
            and ((cpm is not None and cpm >= 8) or (octr is not None and octr <= -8))):
        return ("FATIGUE",
                "Frequency %+.0f%% to %.1f, CPM %s, outbound CTR %s. Same people, more often, clicking less."
                % (freq, f_now, _d(cpm), _d(octr)),
                "This is saturation, not a bad ad. Refresh the creative or widen the audience. "
                "Do not cut the budget of a winner that has simply run out of new people.")

    if octr is not None and octr <= -10 and (hook is not None and hook <= -8):
        return ("CREATIVE — HOOK",
                "Hook rate %s and outbound CTR %s. The first 3 seconds stopped earning the view."
                % (_d(hook), _d(octr)),
                "The thumbnail and the opening line are the problem. Re-cut the first 3 seconds, "
                "or test a new hook against the same body.")

    if octr is not None and octr <= -10:
        return ("CREATIVE — THE CLICK",
                "Outbound CTR %s while the hook held (%s). They watch and do not click."
                % (_d(octr), _d(hook)),
                "The promise is not landing. Sharpen the offer in the copy and the CTA.")

    # They clicked. So from here the ad is doing its job and the SITE is not.
    if atcr is not None and atcr <= -12:
        return ("LANDING PAGE",
                "Add to cart rate %s while outbound CTR held (%s). They click the ad and do not add to cart."
                % (_d(atcr), _d(octr)),
                "The ad is fine, the page is losing them. Check the landing page the ad points to: "
                "the product or collection page, price shown, sizes in stock, and load speed on mobile.")

    if cvr is not None and cvr <= -12:
        return ("CHECKOUT",
                "CVR %s but add to cart held (%s). They add to cart and do not buy."
                % (_d(cvr), _d(atcr)),
                "The problem is after the cart. Check shipping cost and delivery time at checkout, "
                "payment options, and whether a discount code that was running has expired.")

    if aov is not None and aov <= -12:
        return ("BASKET",
                "AOV %s while CVR held (%s). Same number of buyers, smaller orders." % (_d(aov), _d(cvr)),
                "Mix moved to cheaper items, or a discount is deeper than you think. "
                "Check which products these orders contain and what is discounted.")

    if cpc is not None and cpc >= 12:
        return ("AUCTION",
                "CPC %s and CPM %s with the funnel intact. You are paying more for the same click."
                % (_d(cpc), _d(cpm)),
                "Competition or delivery, not your creative. Check whether a competitor entered, "
                "or you narrowed the audience and drove the price up.")

    return ("MIXED",
            "No single stage broke. The move is spread across the funnel.",
            "Nothing is clearly broken. Leave it alone and let it collect more data.")


# ======================= WHO IS ACCOUNTABLE FOR THE REVENUE MOVE =======================
def attribution(A, level="ad", n=8):
    """Every entity's share of the ACCOUNT'S TOTAL REVENUE CHANGE, in EGP and in percent.
    The shares sum to the whole move, so nothing hides. This answers the only question that
    matters: who did this to me, and by how much."""
    LV = {"campaign": "seg_campaigns", "adset": "seg_adsets", "ad": "seg_ads"}
    roll_ = A.get(LV[level]) or {}
    proll = A.get(LV[level] + "_prev") or {}
    if not roll_: return None
    # match on the real Meta ID, never on the name. Names get edited; IDs do not.
    prevmap = {k: e["total"] for k, e in proll.items()}

    rows, d_tot = [], 0.0
    for k_, e in roll_.items():
        m = e["total"]
        q = prevmap.get(k_)
        pre_rev = (q or {}).get("rev") or 0
        d = (m.get("rev") or 0) - pre_rev
        d_tot += d
        rows.append({"name": e["name"], "ent_id": k_, "d_rev": d,
                     "rev": m.get("rev") or 0, "prev_rev": pre_rev,
                     "spend": m.get("spend") or 0, "roas": m.get("roas") or 0,
                     "now": m, "pre": q, "new": not q})
    if not rows: return None

    # Share of the TOTAL MOVE. Signed, so a mover fighting the tide reads as negative share.
    denom = abs(d_tot) if abs(d_tot) > 1 else sum(abs(r["d_rev"]) for r in rows) or 1
    for r in rows:
        r["share"] = r["d_rev"] / denom * 100.0
        r["dx"] = dx_stage(r["now"], r["pre"]) if r["pre"] else None
    rows.sort(key=lambda r: -abs(r["d_rev"]))
    return {"rows": rows[:n], "d_tot": d_tot, "all": rows,
            "gain": sum(r["d_rev"] for r in rows if r["d_rev"] > 0),
            "loss": sum(r["d_rev"] for r in rows if r["d_rev"] < 0)}


def quality(k):
    """One composite score. ROAS is the biggest voice but it is not the only one."""
    def w(v, cap=60.0):
        if v is None: return 0.0
        return max(-cap, min(cap, v))
    return (w(k["v_roas"]) * 0.40 + w(k["v_cpp"]) * 0.20 + w(k["v_cpmr"]) * 0.15
            + w(k["v_cvr"]) * 0.15 + w(k["v_octr"]) * 0.10)


def classify(A):
    """Every ad gets a verdict from its OWN LAST 7 DAYS plus today, never from today alone.
    A single bad day cannot kill anything."""
    b7 = A.get("b7") or {}
    acc7 = A.get("b7_acc") or A["summary"]
    acc_roas = acc7.get("roas") or 0
    out = []
    today_by_id = {str(c["ad_id"]): c for c in A["creatives"]}
    ids = set(b7.keys()) | set(today_by_id.keys())
    scored = []
    for aid in ids:
        b = b7.get(aid) or today_by_id.get(aid)
        if not b or (b.get("spend") or 0) < 300: continue
        k = card_of(b, b, acc7)
        scored.append((aid, b, k, quality(k)))
    if not scored: return []
    qs = sorted(q for _, _, _, q in scored)
    top_decile = pctile(qs, 0.75) or 0   # top quartile of the account, printed on the card
    for aid, b, k, q in scored:
        t = today_by_id.get(aid)                       # today, only as context
        t_roas = (t or {}).get("roas") or 0
        pv = proven(b)
        fq = k["freq"]
        # the trend, so nothing is judged on a spike
        trend = None
        if t and (t.get("spend") or 0) >= 200 and k["roas"]:
            trend = (t_roas / k["roas"] - 1) * 100.0
        volatile = trend is not None and trend <= -35 and k["roas"] >= acc_roas
        if not pv:
            v = "MONITOR"
            why = "Not proven yet. %s spend and %d purchases over 7 days, under the %s / %d bar." % (
                _k(k["spend"]), int(k["purch"]), _k(EVIDENCE["spend"]), EVIDENCE["purch"])
            trig = "Verdict at %s spend and %d purchases." % (_k(EVIDENCE["spend"]), EVIDENCE["purch"])
        elif volatile:
            v = "MONITOR"
            why = "Today reads %.2fx but the 7 day is %.2fx. Single day volatility, not decay." % (
                r2(t_roas), r2(k["roas"]))
            trig = "Act only if the 7 day ROAS drops below %.2fx." % r2(acc_roas * 0.8)
        elif q >= top_decile and fq < FREQ_HEALTHY and k["roas"] >= acc_roas:
            v = "SCALE"
            why = "Top quartile on the whole card. ROAS %s the account, CPMR %s, CVR %s, frequency %.1f so it has room." % (
                _sv(k["v_roas"]), _sv(k["v_cpmr"]), _sv(k["v_cvr"]), fq)
            trig = "Raise 15 to 20 percent. Stop if frequency clears %.1f or ROAS falls under %.2fx." % (
                FREQ_HEALTHY, r2(acc_roas))
        elif k["roas"] >= acc_roas and fq < FREQ_HEALTHY:
            v = "SCALE CAREFULLY"
            why = "Beats the account (%s) but not top quartile. CPMR %s, CVR %s." % (
                _sv(k["v_roas"]), _sv(k["v_cpmr"]), _sv(k["v_cvr"]))
            trig = "Raise 10 percent. Review in 3 days."
        elif k["roas"] >= acc_roas and fq >= FREQ_HEALTHY:
            v = "MONITOR"
            why = "Returns %.2fx but frequency is %.1f on a finite pool. More budget buys repeats." % (
                r2(k["roas"]), fq)
            trig = "Hold budget. Refresh the creative or widen the audience first."
        elif k["roas"] >= acc_roas * 0.6:
            v = "ITERATE"
            why = "ROAS %s the account. %s" % (_sv(k["v_roas"]), _weakest(k))
            trig = "Re-cut it. Kill if the 7 day ROAS drops under %.2fx." % r2(acc_roas * 0.6)
        else:
            waste = k["spend"] - (k["rev"] / (acc_roas or 1))
            v = "KILL"
            why = "7 day ROAS %.2fx against the account's %.2fx on %s spend. %s" % (
                r2(k["roas"]), r2(acc_roas), _k(k["spend"]), _weakest(k))
            trig = "Wasting about %s a week against the account average." % _k(max(0, waste))
        out.append({"id": aid, "c": t or b, "b": b, "k": k, "q": q, "verdict": v,
                    "why": why, "trigger": trig, "proven": pv, "trend": trend,
                    "top": q >= top_decile})
    ORDER = {"SCALE": 0, "SCALE CAREFULLY": 1, "ITERATE": 2, "MONITOR": 3, "KILL": 4}
    return sorted(out, key=lambda e: (ORDER[e["verdict"]], -e["k"]["spend"]))


def _sv(v):
    if v is None: return "n/a"
    return "%+.0f%% vs" % v


def _weakest(k):
    """Name the metric that is actually failing, not just the ROAS."""
    cand = [("CPMR", k["v_cpmr"], "reach is expensive"),
            ("CVR", k["v_cvr"], "the clicks do not convert"),
            ("Outbound CTR", k["v_octr"], "it is not earning the click"),
            ("CPP", k["v_cpp"], "each purchase costs too much"),
            ("Hook rate", k["v_hook"], "the first 3 seconds lose them")]
    cand = [c for c in cand if c[1] is not None]
    if not cand: return ""
    w = min(cand, key=lambda c: c[1])
    if w[1] >= -5: return "Nothing is badly broken, it is just not efficient enough."
    return "%s is %.0f%% worse than the account: %s." % (w[0], abs(w[1]), w[2])


VCOL = {"SCALE": GREEN, "SCALE CAREFULLY": GREEN, "MONITOR": BLUE, "ITERATE": AMBER, "KILL": RED}


def hit_rate(A):
    """Of the ads that got a real chance, how many became winners. That number, and only that
    number, tells you how many creatives you must launch."""
    V = classify(A)
    tested = [e for e in V if e["proven"]]
    if len(tested) < 4: return None
    winners = [e for e in tested if e["verdict"] == "SCALE"]
    hr = max(0.03, min(0.9, len(winners) / len(tested)))
    return {"tested": len(tested), "winners": len(winners), "hr": hr * 100,
            "need_1": math.ceil(1 / hr), "need_3": math.ceil(3 / hr), "definition": DEF_WINNER}


def hook_bench(A):
    """A hook rate on its own is a number. Against the account's own video average it is a verdict."""
    vids = [c for c in A["creatives"] if c.get("type") == "VIDEO" and (c.get("impr") or 0) >= 2000]
    if len(vids) < 3: return None
    impr = sum(c["impr"] for c in vids) or 1
    avg_hook = sum((c.get("hook") or 0) * c["impr"] for c in vids) / impr
    avg_hold = sum((c.get("hold") or 0) * c["impr"] for c in vids) / impr
    rows = []
    for c in vids:
        h = c.get("hook") or 0
        if (c["impr"] or 0) < 5000:
            v = "INCONCLUSIVE"
        elif h >= avg_hook * 1.10:
            v = "STRONG HOOK"
        elif h <= avg_hook * 0.90:
            v = "WEAK HOOK"
        else:
            v = "AVERAGE"
        rows.append({"name": safe(c["ad_name"]), "hook": h, "hold": c.get("hold") or 0,
                     "impr": c["impr"], "spend": c["spend"], "roas": c["roas"], "verdict": v,
                     "vs": (h / avg_hook - 1) * 100 if avg_hook else 0})
    rows.sort(key=lambda r: -r["hook"])
    return {"avg_hook": avg_hook, "avg_hold": avg_hold, "rows": rows, "n": len(vids)}


def scenarios(A):
    """Not 'scale winners'. THIS ad, THIS budget change, THIS expected revenue, priced at its
    own 7 day numbers."""
    V = classify(A)
    acc7 = A.get("b7_acc") or A["summary"]
    acc_roas = acc7.get("roas") or 0
    ups = [e for e in V if e["verdict"] in ("SCALE", "SCALE CAREFULLY")][:4]
    kills = [e for e in V if e["verdict"] == "KILL"][:4]
    if not ups and not kills: return None
    moves = []
    for e in ups:
        step = 0.20 if e["verdict"] == "SCALE" else 0.10
        add = e["k"]["spend"] * step / 7.0             # per day
        moves.append({"kind": "SCALE", "name": safe(e["c"]["ad_name"]), "e": e,
                      "delta_spend": add, "delta_rev": add * (e["k"]["roas"] or 0),
                      "action": "raise budget %d%%" % (step * 100),
                      "at": "%.2fx, frequency %.1f" % (r2(e["k"]["roas"]), e["k"]["freq"])})
    for e in kills:
        cut = e["k"]["spend"] / 7.0                     # per day
        lost = cut * (e["k"]["roas"] or 0)
        moves.append({"kind": "KILL", "name": safe(e["c"]["ad_name"]), "e": e,
                      "delta_spend": -cut, "delta_rev": -lost,
                      "action": "pause",
                      "at": "%.2fx against the account's %.2fx" % (r2(e["k"]["roas"]), r2(acc_roas))})
    freed = sum(-m["delta_spend"] for m in moves if m["kind"] == "KILL")
    added = sum(m["delta_spend"] for m in moves if m["kind"] == "SCALE")
    # the freed money does not vanish, it goes to the scale list at THEIR roas
    redeploy = 0.0
    if freed > 0 and ups:
        wsum = sum(e["k"]["spend"] for e in ups) or 1
        redeploy = sum(freed * (e["k"]["spend"] / wsum) * (e["k"]["roas"] or 0) for e in ups)
    lost_rev = sum(-m["delta_rev"] for m in moves if m["kind"] == "KILL")
    gained = sum(m["delta_rev"] for m in moves if m["kind"] == "SCALE")
    net_rev = gained + redeploy - lost_rev
    day_rev = (A["summary"]["rev"] or 0)
    return {"moves": moves, "freed": freed, "added": added, "redeploy": redeploy,
            "lost": lost_rev, "gained": gained, "net": net_rev,
            "net_pct": (net_rev / day_rev * 100) if day_rev else 0,
            "net_spend": added - freed, "acc_roas": acc_roas}


# ===================== CHART PRIMITIVES =====================
def _tile(fig, x, y, w, h):
    ax = fig.add_axes([x, y, w, h])
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_facecolor(PAPER)
    return ax


def spark(fig, x, y, w, h, series, key, title, fmt, up_good, invert=False):
    """One metric, its own 30 day history, the normal band, and where today lands.
    The point of the chart is the answer, not the line."""
    xs = [d.get(key) or 0 for d in series]
    if len(xs) < 5: return
    ax = _tile(fig, x, y, w, h - .064)
    lo, hi = pctile(xs[:-1], .10), pctile(xs[:-1], .90)
    mid = pctile(xs[:-1], .50)
    n = len(xs)
    ax.plot(range(n), xs, color=FAINT, lw=1.2, zorder=2)
    if lo is not None and hi is not None:
        ax.axhspan(lo, hi, color=LINE, alpha=.55, zorder=1)
        ax.axhline(mid, color=MUTED, lw=.8, ls=(0, (3, 3)), zorder=2)
    # annotate the days that actually explain the line
    if key == "rev" and len(series) >= 10:
        sp_ = [d.get("spend") or 0 for d in series]
        for i in range(1, len(sp_)):
            if not sp_[i - 1]: continue
            ch = (sp_[i] / sp_[i - 1] - 1) * 100
            if abs(ch) >= 35:
                ax.annotate("budget %+.0f%%" % ch, (i, xs[i]), textcoords="offset points",
                            xytext=(0, 11), fontsize=8.5, color=MUTED, family="DejaVu Sans",
                            ha="center",
                            arrowprops=dict(arrowstyle="-", color=FAINT, lw=.7))
        for i, v in enumerate(xs):
            if lo is not None and (v < lo or v > hi) and i < len(xs) - 1:
                ax.plot([i], [v], "o", color=(RED if v < lo else GREEN), ms=4, zorder=3)
    now = xs[-1]
    outside = (lo is not None and (now < lo or now > hi))
    good = (now >= mid) if up_good else (now <= mid)
    col = (GREEN if good else RED) if outside else INK
    ax.plot([n - 1], [now], "o", color=col, ms=9, zorder=4)
    pad = (max(xs) - min(xs)) * .18 or 1
    ax.set_ylim(min(xs) - pad, max(xs) + pad)
    ax.set_xlim(-.5, n - .5 + n * .02)
    vs = ((now / mid - 1) * 100.0) if mid else None
    fig.text(x, y + h - .012, title, fontsize=F_HD, color=MUTED,
             family="DejaVu Sans", weight="bold")
    fig.text(x, y + h - .044, fmt(now), fontsize=20, color=INK,
             family="DejaVu Sans", weight="bold")
    if vs is not None:
        fig.text(x + w, y + h - .040, ("%+.0f%%" % vs), fontsize=15, color=col,
                 family="DejaVu Sans", weight="bold", ha="right")
        fig.text(x + w, y + h - .058, "vs its usual day", fontsize=9.5, color=FAINT,
                 family="DejaVu Sans", ha="right")


def trendplot(fig, x, y, w, h, series, key, title, fmt, up_good, n=7):
    """The rolling 7 day line. Frequency and CPMR are meaningless on a single day, so
    they are never judged on one."""
    xs = [d.get(key) or 0 for d in series]
    if len(xs) < 10: return
    r = rolling(series, key, n)
    ax = _tile(fig, x, y, w, h - .062)
    ax.plot(range(len(xs)), xs, color=LINE, lw=1.0, zorder=1)
    ax.plot(range(len(r)), r, color=INK, lw=2.4, zorder=3)
    ax.plot([len(r) - 1], [r[-1]], "o", color=INK, ms=7, zorder=4)
    allv = xs + r; pad = (max(allv) - min(allv)) * .18 or 1
    ax.set_ylim(min(allv) - pad, max(allv) + pad)
    ax.set_xlim(-.5, len(xs) - .5)
    W = wow(series, key, n)
    fig.text(x, y + h - .010, title, fontsize=F_HD, color=MUTED, family="DejaVu Sans", weight="bold")
    fig.text(x, y + h - .040, fmt(r[-1]), fontsize=20, color=INK, family="DejaVu Sans", weight="bold")
    if W:
        good = (W["chg"] >= 0) if up_good else (W["chg"] <= 0)
        col = MUTED if abs(W["chg"]) < 3 else (GREEN if good else RED)
        fig.text(x + w, y + h - .036, "%+.0f%%" % W["chg"], fontsize=15, color=col,
                 family="DejaVu Sans", weight="bold", ha="right")
        fig.text(x + w, y + h - .053, "7 days vs the 7 before", fontsize=9.5, color=FAINT,
                 family="DejaVu Sans", ha="right")


# ---------- CARD 1: is this normal, and what moved it ----------
def card_pulse(A, win):
    s = A["summary"]; p = s.get("prev") or {}
    H = A.get("hist") or []
    nd = WIN_DAYS.get(win, 1)
    fig = _fig(13.8)
    _head(fig, A, win, "Is this normal, and what moved it",
          "TWO DIFFERENT COMPARISONS. Top: against a typical day over 30 days. Bottom: against the period immediately before. They will not match, and they are not meant to.")

    # THE VERDICT. Not a z score. A percentage, and whether it is inside the usual swing.
    ax = _panel(fig, .775, .105, "the verdict  ·  vs a typical day over the last 30 days")
    B = normal_band(H, "rev", (s["rev"] or 0) / nd)
    if B:
        col = GREEN if (B["inside"] and B["vs"] >= 0) else (RED if not B["inside"] and B["vs"] < 0
                                                            else (AMBER if not B["inside"] else MUTED))
        if B["inside"] and B["vs"] < -8: col = AMBER
        ax.text(1.6, 66, "REVENUE %+.0f%%  vs a typical day" % B["vs"], fontsize=F_H + 9,
                color=col, family="DejaVu Sans", weight="bold")
        ax.text(98, 66, B["verdict"], fontsize=F_H + 2, color=col, family="DejaVu Sans",
                weight="bold", ha="right")
        ax.text(1.6, 34, "%s a day now, against %s on a typical day. This account normally swings %+.0f%% to %+.0f%% day to day." % (
            _k(B["val"]), _k(B["mid"]), B["lo_p"], B["hi_p"]), fontsize=F_ROW + 1,
            color=MUTED, family="DejaVu Sans")
        call = ("Inside the usual swing. Do not react to it as a trend."
                if B["inside"] else
                "Outside the usual swing. This is a real move, not noise. Act on it.")
        ax.text(1.6, 9, call, fontsize=F_ROW + 2, color=INK, family="DejaVu Sans", weight="bold")
    else:
        ax.text(1.6, 45, "Not enough history yet to say what normal is.", fontsize=F_ROW + 2,
                color=MUTED, family="DejaVu Sans")

    # SIX SPARKLINES. Each one says: is this metric normal for us, right now.
    fig.text(.055, .742, "EVERY METRIC AGAINST ITS OWN NORMAL BAND  ·  the shaded band is where this account lives 8 days out of 10",
             fontsize=F_HD, color=FAINT, family="DejaVu Sans", weight="bold")
    SP = [("rev", "REVENUE PER DAY", _k, True), ("roas", "ROAS", lambda v: "%.2f" % r2(v), True),
          ("cpc", "CPC", lambda v: "%.2f" % r2(v), False), ("cvr", "CVR", lambda v: "%.2f%%" % r2(v), True),
          ("aov", "AOV", _k, True), ("octr", "OUTBOUND CTR", lambda v: "%.2f%%" % r2(v), True)]
    for i, (k, t, fmt, ug) in enumerate(SP):
        cx = .055 + (i % 3) * .308
        cy = .565 - (i // 3) * .142
        spark(fig, cx, cy, .268, .125, H, k, t, fmt, ug)

    # THE WATERFALL, in percent of last period's revenue.
    ax = _panel(fig, .075, .270, "what moved it  ·  vs the period immediately before, not vs the 30 day average")
    W = waterfall(s, p)
    if W:
        down = W["d_pct"] < 0
        out_col = RED if down else GREEN
        t = max(W["steps"], key=lambda z: abs(z["rev_pct"]))
        # the headline is the OUTCOME, not the lever. Red when revenue is down, always.
        ax.text(1.6, 82, "REVENUE %+.1f%%" % W["d_pct"], fontsize=F_H + 8, color=out_col,
                family="DejaVu Sans", weight="bold")
        # the causal sentence, written out, so nothing has to be joined up by hand
        hurt = sorted([x for x in W["steps"] if x["rev_pct"] < 0], key=lambda x: x["rev_pct"])
        helped = sorted([x for x in W["steps"] if x["rev_pct"] >= 0], key=lambda x: -x["rev_pct"])
        SAY = {"SPEND": "spend", "CPC": "CPC", "CVR": "CVR", "AOV": "AOV"}
        bits = []
        for x in hurt[:2]:
            bits.append("%s %+.0f%% took %.1f%% off revenue" % (SAY[x["k"]], x["chg"] or 0, abs(x["rev_pct"])))
        for x in helped[:2]:
            bits.append("%s %+.0f%% added %.1f%%" % (SAY[x["k"]], x["chg"] or 0, x["rev_pct"]))
        sent = ".  ".join(bits) + "."
        for j, ln in enumerate(_wrap(sent, 92)[:2]):
            ax.text(1.6, 71 - j * 7, ln, fontsize=F_ROW + 2, color=INK, family="DejaVu Sans")
        base_y, height = 12.0, 40.0
        top = max(abs(x["rev_pct"]) for x in W["steps"]) * 1.5 or 1
        mid_y = base_y + height / 2
        ax.plot([2, 98], [mid_y, mid_y], color=LINE, lw=1)
        bw = 15.0; x0 = 8.0
        for i, st_ in enumerate(W["steps"]):
            x = x0 + i * 22.0
            hgt = abs(st_["rev_pct"]) / top * (height / 2)
            up = st_["rev_pct"] >= 0
            col = GREEN if up else RED
            ax.add_patch(plt.Rectangle((x, mid_y if up else mid_y - hgt), bw, hgt,
                                       facecolor=col, edgecolor="none"))
            ax.text(x + bw / 2, mid_y + (hgt + 2.5 if up else -hgt - 5.5), "%+.1f%%" % st_["rev_pct"],
                    fontsize=F_ROW + 1, color=col, family="DejaVu Sans", weight="bold", ha="center")
            kk = {"SPEND": "spend", "CPC": "cpc", "CVR": "cvr", "AOV": "aov"}[st_["k"]]
            ax.text(x + bw / 2, 6, "%s %+.0f%%" % (st_["k"], st_["chg"] or 0), fontsize=F_ROW + 1,
                    color=impact(kk, st_["chg"]), family="DejaVu Sans", weight="bold", ha="center")
            ax.text(x + bw / 2, 1, "%d%% of the move" % st_["pct"], fontsize=10, color=FAINT,
                    family="DejaVu Sans", ha="center")
    else:
        ax.text(1.6, 45, "No comparable prior period.", fontsize=F_ROW + 2, color=MUTED,
                family="DejaVu Sans")
    _foot(fig, win)
    return _png(fig)


# ---------- CARD 2: the weekly trend. Nothing here is judged on one day. ----------
def card_trend(A, win):
    H = A.get("hist") or []
    if len(H) < 12: return None
    fig = _fig(13.2)
    _head(fig, A, win, "The trend, not the day",
          "Rolling 7 day average. Frequency, CPMR and CPM are noise on a single day, so they are never judged on one.")
    TR = [("freq", "FREQUENCY", lambda v: "%.2f" % r2(v), False),
          ("cpmr", "CPMR  ·  cost per 1,000 people reached", _k, False),
          ("cpm", "CPM", _k, False),
          ("octr", "OUTBOUND CTR", lambda v: "%.2f%%" % r2(v), True),
          ("cpc", "CPC", lambda v: "%.2f" % r2(v), False),
          ("cvr", "CVR", lambda v: "%.2f%%" % r2(v), True)]
    bad = []
    for i, (k, t, fmt, ug) in enumerate(TR):
        cx = .055 + (i % 3) * .308
        cy = .560 - (i // 3) * .245
        trendplot(fig, cx, cy, .268, .225, H, k, t, fmt, ug)
        W = wow(H, k, 7)
        if W and abs(W["chg"]) >= 5 and not ((W["chg"] >= 0) if ug else (W["chg"] <= 0)):
            bad.append((t.split("  ")[0], W["chg"]))
    ax = _panel(fig, .075, .215, "what the week is telling you")
    Wm = {k: wow(H, k, 7) for k in ("freq", "cpmr", "cpm", "octr", "cpc", "cvr", "rev", "roas")}
    def g(k):
        w = Wm.get(k)
        return w["chg"] if w else None
    said = []
    # every sentence below is built from the numbers, never assumed
    if (g("cpmr") or 0) >= 5 and (g("freq") or 0) >= 3:
        said.append(("SATURATION", RED, "CPMR %+.0f%% with frequency %+.0f%%. You are paying more to reach the same people." % (g("cpmr"), g("freq"))))
    elif (g("cpmr") or 0) <= -5:
        said.append(("REACH IS GETTING CHEAPER", GREEN, "CPMR %+.0f%% over the week. Each new person costs less than it did, so there is room to buy more of them." % g("cpmr")))
    if (g("octr") or 0) <= -5 and (g("cvr") or 0) > -5:
        said.append(("CREATIVE, NOT LANDING PAGE", RED, "Outbound CTR %+.0f%% while CVR held. Fewer people click, the ones who do still buy." % g("octr")))
    elif (g("cvr") or 0) <= -5 and (g("octr") or 0) > -5:
        said.append(("LANDING PAGE OR OFFER", RED, "CVR %+.0f%% while outbound CTR held. They still click, they stop buying. The break is after the click." % g("cvr")))
    elif (g("cvr") or 0) <= -5 and (g("octr") or 0) <= -5:
        said.append(("TRAFFIC QUALITY", RED, "Outbound CTR %+.0f%% and CVR %+.0f%% together. The audience is worse, not just the creative." % (g("octr"), g("cvr"))))
    if (g("cpc") or 0) <= -5:
        said.append(("CLICKS GOT CHEAPER", GREEN, "CPC %+.0f%% over the week." % g("cpc")))
    if not said:
        said.append(("THE WEEK IS HOLDING", GREEN, "Nothing moved more than 5 percent on a 7 day view."))
    for i, (hd, col, body) in enumerate(said[:3]):
        y = 82 - i * 29
        ax.text(1.6, y, hd, fontsize=F_H + 2, color=col, family="DejaVu Sans", weight="bold")
        for j, ln in enumerate(_wrap(body, 96)[:2]):
            ax.text(1.6, y - 11 - j * 7.5, ln, fontsize=F_ROW + 1, color=INK, family="DejaVu Sans")
    _foot(fig, win)
    return _png(fig)


# ---------- CARD 3: audience allocation. Did the mix move the account, or did the audiences? ----------
def card_audience(A, win):
    M = mix_split(A)
    segs = A.get("segs") or {}; prev = A.get("segs_prev") or {}
    ks = [k for k in ("NEW", "ENGAGED", "EXISTING") if segs.get(k) and segs[k].get("share", 0) >= 1]
    if not ks: return None
    fig = _fig(12.4)
    _head(fig, A, win, "Where the money went",
          "Meta's own audience segments. Spend allocation, how it shifted, and whether that shift explains the account.")

    # 1. THE ALLOCATION SHIFT
    ax = _panel(fig, .620, .240, "spend allocation  ·  before and now")
    rows = [("BEFORE", prev, 60), ("NOW", segs, 26)]
    tot = {"BEFORE": sum((prev.get(k) or {}).get("spend", 0) for k in ks) or 1,
           "NOW": sum(segs[k]["spend"] for k in ks) or 1}
    for lab, src, y in rows:
        x = 14.0
        ax.text(1.6, y + 5, lab, fontsize=F_ROW + 1, color=MUTED, family="DejaVu Sans", weight="bold")
        for k in ks:
            m = src.get(k) or {}
            w = (m.get("spend", 0) / tot[lab]) * 82.0
            if w <= 0: continue
            ax.add_patch(plt.Rectangle((x, y), w, 13, facecolor=SEGC.get(k, FAINT), edgecolor=PAPER, lw=2))
            if w > 7:
                ax.text(x + w / 2, y + 6.5, "%.0f%%" % (w / 82.0 * 100), fontsize=F_ROW + 2,
                        color=INK, family="DejaVu Sans", weight="bold", ha="center", va="center")
            x += w
    lx = 1.6
    for k in ks:
        m = segs[k]; q = prev.get(k) or {}
        sh_now = m["spend"] / tot["NOW"] * 100
        sh_pre = (q.get("spend", 0) / tot["BEFORE"] * 100) if q else 0
        d_spend = pct(m["spend"], q.get("spend") or 0) if q else None
        ax.add_patch(plt.Rectangle((lx, 5), 1.4, 6, facecolor=SEGC.get(k, FAINT), edgecolor="none"))
        ax.text(lx + 2.6, 8, "%s  %.0f%% to %.0f%% of spend  ·  spend %s" % (
            SEGN.get(k, k), sh_pre, sh_now, _d(d_spend)), fontsize=11.5, color=MUTED,
            family="DejaVu Sans", va="center")
        lx += 33.0

    # 2. WAS IT MIX OR WAS IT PERFORMANCE
    ax = _panel(fig, .350, .245, "did the mix move the account, or did the audiences get better")
    if M:
        ax.text(1.6, 79, "Blended ROAS %.2f to %.2f  (%+.0f%%)" % (
            r2(M["blended_prev"]), r2(M["blended_now"]), M["total"]),
            fontsize=F_H + 3, color=INK, family="DejaVu Sans", weight="bold")
        parts = [("MIX", M["mix"], "you moved money between audiences"),
                 ("PERFORMANCE", M["perf"], "the audiences themselves changed")]
        for i, (nm_, v, expl) in enumerate(parts):
            y = 54 - i * 24
            col = GREEN if v >= 0 else RED
            ax.text(1.6, y, nm_, fontsize=F_H + 2, color=INK, family="DejaVu Sans", weight="bold")
            ax.add_patch(plt.Rectangle((22, y - 4), 40, 9, facecolor=BONE, edgecolor="none"))
            span = max(abs(M["mix"]), abs(M["perf"]), 1)
            ax.add_patch(plt.Rectangle((42, y - 4), (v / span) * 20.0, 9, facecolor=col, edgecolor="none"))
            ax.plot([42, 42], [y - 6, y + 7], color=MUTED, lw=1)
            ax.text(64, y, "%+.0f%%" % v, fontsize=F_H + 2, color=col, family="DejaVu Sans",
                    weight="bold", va="center")
            ax.text(72, y, expl, fontsize=F_ROW, color=MUTED, family="DejaVu Sans", va="center")
        dom = "MIX" if abs(M["mix"]) >= abs(M["perf"]) else "PERFORMANCE"
        msg = ("Most of the ROAS move is *mix*. You did not get better, you just shifted money "
               "toward a cheaper audience. That flatters ROAS and starves the top of the funnel."
               if dom == "MIX" else
               "Most of the ROAS move is real *performance*. The audiences themselves changed, "
               "so the account genuinely got better or worse.")
        for j, ln in enumerate(_wrap(msg.replace("*", ""), 92)[:2]):
            ax.text(1.6, 14 - j * 7, ln, fontsize=F_ROW + 1, color=INK, family="DejaVu Sans", weight="bold")

    # 3. HEADROOM. Who can still take money.
    ax = _panel(fig, .075, .245, "who can still take money")
    room_ks = [k for k in ks if (segs[k].get("freq") or 9) < 3.0]
    for i, k in enumerate(ks):
        m = segs[k]; q = prev.get(k) or {}
        x = 1.6 + i * 33.0
        fq = m.get("freq") or 0
        room = fq < 3.0
        ax.add_patch(plt.Rectangle((x, 74), 1.6, 10, facecolor=SEGC.get(k, FAINT), edgecolor="none"))
        ax.text(x + 3.4, 79, SEGN.get(k, k), fontsize=F_H + 1, color=INK, family="DejaVu Sans",
                weight="bold", va="center")
        ax.text(x, 62, "ROAS %.2f  (%s)" % (r2(m["roas"]), _d(pct(m["roas"], q.get("roas") or 0) if q else None)),
                fontsize=F_ROW + 1, color=MUTED, family="DejaVu Sans")
        ax.text(x, 50, "frequency %.2f  ·  reach %s (%s)" % (
            r2(fq), _k(m.get("reach")), _d(pct(m.get("reach") or 0, q.get("reach") or 0) if q else None)),
            fontsize=F_ROW, color=MUTED, family="DejaVu Sans")
        ax.text(x, 34, "HEADROOM" if room else "SATURATED",
                fontsize=F_H + 2, color=(GREEN if room else RED), family="DejaVu Sans", weight="bold")
        ax.text(x, 22, ("frequency under 3, it can absorb more budget" if room
                        else "frequency is high on a finite pool, more budget buys repeats"),
                fontsize=10.5, color=FAINT, family="DejaVu Sans")
    REC = allocate(A)
    if REC:
        txt = "TARGET ALLOCATION:   " + "    ".join(
            "%s %.0f%% to %.0f%%" % (SEGN[k].split()[0], REC["now"][k] * 100, REC["target"][k] * 100)
            for k in REC["order"])
        ax.text(1.6, 12, txt, fontsize=F_ROW + 2, color=INK, family="DejaVu Sans", weight="bold")
        for j, ln in enumerate(_wrap(REC["why"], 100)[:1]):
            ax.text(1.6, 3, ln, fontsize=F_NOTE, color=MUTED, family="DejaVu Sans")
    _foot(fig, win)
    return _png(fig)


# ---------- CARD 4: the decision. Cut, scale, monitor. ----------
def card_decide(A, win):
    s = A["summary"]; acc = s["roas"] or 0
    rows = [c for c in A["creatives"] if (c["spend"] or 0) >= 300]
    if not rows: return None
    fig = _fig(13.4)
    _head(fig, A, win, "Cut, scale, monitor",
          "Left of the line is losing to the account average. Right of it is beating it. Bubble size is spend.")

    ax = fig.add_axes([.075, .470, .88, .365])
    ax.set_facecolor(PAPER)
    for sp in ax.spines.values(): sp.set_color(LINE)
    mx = max(c["roas"] or 0 for c in rows) * 1.12 or 1
    ms = max(c["spend"] for c in rows) or 1
    ax.axvspan(0, acc * .8, color=RED, alpha=.06)
    ax.axvspan(acc, mx, color=GREEN, alpha=.07)
    ax.axvline(acc, color=INK, lw=1.4, ls=(0, (4, 3)))
    ax.text(acc, ms * 1.07, "  account %.2fx" % r2(acc), fontsize=F_ROW, color=INK,
            family="DejaVu Sans", weight="bold")
    ax.text(acc * .35, ms * 1.07, "CUT ZONE", fontsize=F_H, color=RED, family="DejaVu Sans", weight="bold")
    ax.text(acc + (mx - acc) * .55, ms * 1.07, "SCALE ZONE", fontsize=F_H, color=GREEN,
            family="DejaVu Sans", weight="bold")
    for c in rows:
        ax.scatter([c["roas"] or 0], [c["spend"]], s=60 + (c["spend"] / ms) * 900,
                   color=SEGC.get(c["seg"], FAINT), alpha=.75, edgecolors=INK,
                   linewidths=.6, zorder=3)
    for c in sorted(rows, key=lambda c: -c["spend"])[:5]:
        ax.annotate(_clip(safe(c["ad_name"]), 20), (c["roas"] or 0, c["spend"]),
                    textcoords="offset points", xytext=(12, 10), fontsize=10, color=MUTED,
                    family="DejaVu Sans")
    ax.set_xlim(0, mx); ax.set_ylim(0, ms * 1.20)
    ax.set_xlabel("ROAS", fontsize=F_HD, color=MUTED, family="DejaVu Sans")
    ax.set_ylabel("SPEND", fontsize=F_HD, color=MUTED, family="DejaVu Sans")
    ax.tick_params(colors=FAINT, labelsize=10)
    for k in ("NEW", "ENGAGED", "EXISTING"):
        ax.scatter([], [], color=SEGC[k], label=SEGN[k], s=90)
    lg = ax.legend(loc="lower right", frameon=False, fontsize=11)
    for t in lg.get_texts(): t.set_color(MUTED)

    ax = _panel(fig, .245, .185, "the move  ·  same spend, no new budget")
    S = plan(A)
    if S:
        ax.text(1.6, 78, "CUT 30%", fontsize=F_H, color=RED, family="DejaVu Sans", weight="bold")
        ax.text(52, 78, "FUND", fontsize=F_H, color=GREEN, family="DejaVu Sans", weight="bold")
        for i, c in enumerate(S["cut"][:4]):
            y = 62 - i * 13
            ax.text(1.6, y, _clip(safe(c["ad_name"]), 24), fontsize=F_ROW, color=INK, family="DejaVu Sans")
            ax.text(34, y, "%.2fx" % r2(c["roas"]), fontsize=F_ROW, color=RED,
                    family="DejaVu Sans", weight="bold", ha="right")
            ax.text(49, y, "%.0f%% under account" % ((1 - (c["roas"] or 0) / (acc or 1)) * 100),
                    fontsize=10.5, color=FAINT, family="DejaVu Sans", ha="right")
        for i, c in enumerate(S["fund"][:4]):
            y = 62 - i * 13
            ax.text(52, y, _clip(safe(c["ad_name"]), 22), fontsize=F_ROW, color=INK, family="DejaVu Sans")
            ax.text(84, y, "%.2fx" % r2(c["roas"]), fontsize=F_ROW, color=GREEN,
                    family="DejaVu Sans", weight="bold", ha="right")
            ax.text(99, y, "freq %.1f" % r2(c["freq"]), fontsize=10.5, color=FAINT,
                    family="DejaVu Sans", ha="right")
        ax.text(1.6, 6, "Moves %s of spend. Revenue %+.1f%% at the same budget." % (
            _k(S["freed"]), S["gain_pct"]), fontsize=F_H + 4, color=INK,
            family="DejaVu Sans", weight="bold")
    else:
        ax.text(1.6, 45, "No clean reallocation. Nothing is far enough below the account to cut with confidence.",
                fontsize=F_ROW + 2, color=MUTED, family="DejaVu Sans")

    ax = _panel(fig, .055, .175, "monitor  ·  fatiguing, with the mechanism")
    F = fatiguing(A["creatives"])
    if F:
        for i, e in enumerate(F[:3]):
            y = 74 - i * 25
            ax.text(1.6, y, _clip(e["name"], 30), fontsize=F_ROW + 2, color=INK,
                    family="DejaVu Sans", weight="bold")
            ax.text(98, y, "freq %+.0f%%   ·   outbound CTR %+.0f%%   ·   ROAS %+.0f%%" % (
                e["d"]["freq"] or 0, e["d"]["octr"] or 0, e["d"]["roas"] or 0),
                fontsize=F_ROW, color=RED, family="DejaVu Sans", ha="right")
            ax.text(1.6, y - 8, e["verdict"], fontsize=F_ROW, color=RED,
                    family="DejaVu Sans", weight="bold")
            ax.text(26, y - 8, (e["why"][0] if e["why"] else "")[:92], fontsize=10,
                    color=FAINT, family="DejaVu Sans")
    else:
        ax.text(1.6, 45, "Nothing is fatiguing. Frequency and outbound CTR are still moving together.",
                fontsize=F_ROW + 2, color=GREEN, family="DejaVu Sans", weight="bold")
    _foot(fig, win)
    return _png(fig)


# ---------- CARD: the funnel, top to bottom, and where it broke ----------
def card_funnel(A, win):
    m = A["summary"]; p = m.get("prev") or {}
    R = funnel_read(m, p)
    if not R: return None
    fig = _fig(13.0)
    _head(fig, A, win, "The funnel, top to bottom",
          "Hook to hold to CTR to link CTR to ATC to CVR to AOV to ROAS. One metric on its own says nothing.")

    ax = _panel(fig, .440, .400, "every stage against the period before")
    n = len(R["stages"])
    w = 96.0 / n
    for i, st_ in enumerate(R["stages"]):
        x = 2.0 + i * w
        ch = st_["chg"] or 0
        col = GREEN if ch >= 2 else (RED if ch <= -2 else MUTED)
        bh = min(abs(ch), 60) / 60.0 * 26.0
        ax.add_patch(plt.Rectangle((x + w * .18, 40 if ch >= 0 else 40 - bh), w * .5, bh,
                                   facecolor=col, edgecolor="none"))
        ax.plot([x + w * .10, x + w * .78], [40, 40], color=LINE, lw=1)
        ax.text(x + w * .43, 40 + (bh + 4 if ch >= 0 else -bh - 8), "%+.0f%%" % ch,
                fontsize=F_ROW + 1, color=col, family="DejaVu Sans", weight="bold", ha="center")
        val = _k(st_["now"]) if st_["k"] == "aov" else (
            ("%.2fx" % r2(st_["now"])) if st_["k"] == "roas" else ("%.2f%%" % r2(st_["now"])))
        ax.text(x + w * .43, 24, val, fontsize=F_ROW + 2, color=INK, family="DejaVu Sans",
                weight="bold", ha="center")
        ax.text(x + w * .43, 14, st_["lab"], fontsize=9.5, color=MUTED, family="DejaVu Sans",
                ha="center")
        if i < n - 1:
            ax.text(x + w * .95, 24, "→", fontsize=15, color=FAINT, family="DejaVu Sans",
                    ha="center", va="center")
    br = R["broke"]
    ax.text(2.0, 83, ("FIRST STAGE TO BREAK: %s, %+.0f%%" % (br["lab"], br["chg"] or 0))
            if br else "NO STAGE BROKE BY MORE THAN 5 PERCENT",
            fontsize=F_H + 5, color=(RED if br else GREEN), family="DejaVu Sans", weight="bold")
    ax.text(2.0, 72, "The funnel is read from the top. The first stage that fell is the one to fix, "
                     "because everything under it inherits the damage.",
            fontsize=F_NOTE, color=MUTED, family="DejaVu Sans")

    ax = _panel(fig, .075, .330, "what that actually means")
    for i, ln in enumerate(R["lines"][:3]):
        y = 78 - i * 26
        ax.add_patch(plt.Rectangle((1.4, y - 12), .8, 20, facecolor=RED if i == 0 else FAINT,
                                   edgecolor="none"))
        for j, chunk in enumerate(_wrap(ln.replace("*", ""), 88)[:2]):
            ax.text(4.0, y - j * 8, chunk, fontsize=F_ROW + 2, color=INK, family="DejaVu Sans")

    C = [("CPM", m.get("cpm"), p.get("cpm"), _k), ("CPMR", m.get("cpmr"), p.get("cpmr"), _k),
         ("CPC", m_cpc(m), m_cpc(p), lambda v: "%.2f" % r2(v)),
         ("FREQUENCY", m.get("freq"), p.get("freq"), lambda v: "%.2f" % r2(v))]
    for i, (lab, now, pre, fmt) in enumerate(C):
        x = 2.0 + i * 25.0
        ch = pct(now or 0, pre or 0)
        col = RED if (ch or 0) >= 2 else (GREEN if (ch or 0) <= -2 else MUTED)
        ax.text(x, 14, lab, fontsize=9.5, color=FAINT, family="DejaVu Sans", weight="bold")
        ax.text(x, 4, "%s   %s" % (fmt(now), _d(ch)), fontsize=F_ROW + 1, color=col,
                family="DejaVu Sans", weight="bold")
    _foot(fig, win)
    return _png(fig)


# ---------- CARD: it was working, now it is not ----------
def card_decay(A, win):
    D = decay_watch(A)
    if not D:
        fig = _fig(8.0)
        _head(fig, A, win, "It was working, now it is not",
              "Creatives that used to beat the account and no longer do.")
        ax = _panel(fig, .380, .420, "flagged")
        ax.text(1.6, 45, "Nothing that was beating the account has fallen below it. No decay to report.",
                fontsize=F_H + 2, color=GREEN, family="DejaVu Sans", weight="bold")
        _foot(fig, win)
        return _png(fig)

    D = D[:3]
    # The card is sized to the number of decayers. Three panels of dead space is not a design.
    PH = 3.55                                     # inches per ad panel
    fig = _fig(3.2 + PH * len(D))
    _head(fig, A, win, "It was working, now it is not",
          "Every percent on this card is against THAT AD'S OWN PREVIOUS PERIOD, never the account.")

    acc7 = A.get("b7_acc") or A["summary"]
    b7 = A.get("b7") or {}
    figh = 3.2 + PH * len(D)
    H = PH / figh * .93                            # panel height in figure units
    top = 1 - (2.05 / figh)                        # first panel starts under the headline
    for i, e in enumerate(D):
        y0 = top - (i + 1) * H - i * (0.30 / figh)
        ax = _panel(fig, y0, H, "")
        b = e.get("now") or {}

        ax.text(1.6, 90, _clip(e["name"], 32), fontsize=F_H + 3, color=INK,
                family="DejaVu Serif", weight="bold")
        ax.text(1.6, 82, "%s  ·  7 day spend %s  ·  revenue %s%s EGP vs prev" % (
            SEGN.get(e["seg"], e["seg"]), _k(e["spend"]),
            "+" if e.get("d_rev", 0) >= 0 else "-", _k(abs(e.get("d_rev", 0)))),
            fontsize=10.5, color=FAINT, family="DejaVu Sans")

        # ROAS and the worst metric each get their OWN LINE. They were colliding because a long
        # ROAS string ran straight through a fixed x offset. Never place two strings of unknown
        # length on the same baseline.
        cul = e["culprit"]
        ax.text(1.6, 70, "ROAS %.2fx  →  %.2fx   (%s)" % (
            r2(e["proas"]), r2(e["roas"]), _d(e["d_roas"])),
            fontsize=F_H + 2, color=RED, family="DejaVu Sans", weight="bold")
        ax.text(1.6, 61, "WORST METRIC:  %s %+.0f%%" % (cul["lab"], cul["chg"]),
                fontsize=F_H, color=RED, family="DejaVu Sans", weight="bold")

        # the diagnosis: what broke, and the place to actually go and look
        dx = e.get("dx")
        if dx:
            cause, what, todo = dx
            ax.text(55, 90, cause, fontsize=F_H + 1, color=DXCOL.get(cause, MUTED),
                    family="DejaVu Sans", weight="bold")
            yy = 81
            for ln in _wrap(what, 44)[:3]:
                ax.text(55, yy, ln, fontsize=9.6, color=MUTED, family="DejaVu Sans")
                yy -= 5.2
            yy -= 2.0
            for ln in _wrap("DO: " + todo, 44)[:4]:
                ax.text(55, yy, ln, fontsize=9.8, color=INK, family="DejaVu Sans", style="italic")
                yy -= 5.2

        # every metric against its own past. One row, evenly spaced, never overlapping.
        ax.plot([1.6, 98.4], [52, 52], color=LINE, lw=1)
        ax.text(1.6, 45, "EVERY METRIC vs THIS AD'S OWN PREVIOUS PERIOD", fontsize=9,
                color=FAINT, family="DejaVu Sans", weight="bold")
        rws = e["rows"][:11]
        step = 96.0 / max(len(rws), 1)
        for j, r in enumerate(rws):
            x = 2.0 + j * step
            col = RED if r["hurt"] >= 5 else (GREEN if r["hurt"] <= -5 else FAINT)
            ax.text(x, 33, r["lab"], fontsize=8.6, color=FAINT, family="DejaVu Sans")
            ax.text(x, 22, "%+.0f%%" % r["chg"], fontsize=15, color=col,
                    family="DejaVu Sans", weight="bold")
        # and the absolute card, so the percent always has a number behind it
        ax.text(1.6, 10, "SPEND %s   ·   ROAS %.2fx   ·   CPP %s   ·   CPMR %s   ·   CVR %s   ·   AOV %s   ·   FRQ %.1f   ·   PUR %d" % (
            _k(b.get("spend") or 0), r2(b.get("roas") or 0), _k(b.get("cpa") or 0),
            _k(b.get("cpmr") or 0), ("%.2f%%" % r2(b.get("cvr") or 0)), _k(b.get("aov") or 0),
            r2(b.get("freq") or 0), int(b.get("purch") or 0)),
            fontsize=10.5, color=INK, family="DejaVu Sans", weight="bold")
        ax.text(1.6, 3, "Absolute numbers, this ad's last 7 days.", fontsize=9, color=FAINT,
                family="DejaVu Sans", style="italic")
    _foot(fig, win)
    return _png(fig)


def retention(A):
    """Where every video loses people. Measured off the 3 second view, because that is the
    moment they actually started watching."""
    b7 = A.get("b7") or {}
    vids = [c for c in b7.values() if (c.get("v3") or 0) >= 500 and (c.get("impr") or 0) >= 5000]
    if len(vids) < 2: return None
    V3 = sum(c["v3"] for c in vids) or 1
    IM = sum(c["impr"] for c in vids) or 1
    acc = {"hook": sum(c["v3"] for c in vids) / IM * 100,
           "p25": sum(c.get("p25", 0) for c in vids) / V3 * 100,
           "p50": sum(c.get("p50", 0) for c in vids) / V3 * 100,
           "p75": sum(c.get("p75", 0) for c in vids) / V3 * 100,
           "p95": sum(c.get("p95", 0) for c in vids) / V3 * 100,
           "p100": sum(c.get("p100", 0) for c in vids) / V3 * 100}
    rows = []
    for c in vids:
        v3 = c["v3"] or 1
        curve = [100.0,
                 (c.get("p25", 0) / v3) * 100, (c.get("p50", 0) / v3) * 100,
                 (c.get("p75", 0) / v3) * 100, (c.get("p95", 0) / v3) * 100,
                 (c.get("p100", 0) / v3) * 100]
        rows.append({"name": safe(c["ad_name"]), "ad_id": c.get("ad_id"),
                     "hook": (c["v3"] / (c["impr"] or 1)) * 100,
                     "curve": curve, "spend": c["spend"], "roas": c["roas"],
                     "cpmr": c.get("cpmr") or 0, "v3": c["v3"],
                     "hook_src": c.get("hook_src", "3s")})
    acc_curve = [100.0, acc["p25"], acc["p50"], acc["p75"], acc["p95"], acc["p100"]]
    # the biggest single cliff in the account's own curve
    STEPS = [("start to 25%", 0, 1), ("25% to 50%", 1, 2), ("50% to 75%", 2, 3),
             ("75% to 95%", 3, 4), ("95% to the end", 4, 5)]
    drops = [(lab, acc_curve[a] - acc_curve[b]) for lab, a, b in STEPS]
    worst = max(drops, key=lambda d: d[1])
    rows.sort(key=lambda r: -r["spend"])
    return {"acc": acc, "acc_curve": acc_curve, "rows": rows, "drops": drops, "worst": worst,
            "src": rows[0]["hook_src"] if rows else "3s"}


# ---------- CARD: where every video loses them ----------
def card_retention(A, win):
    R = retention(A)
    if not R: return None
    fig = _fig(16.2)
    src = {"3s": "3 second video views / impressions",
           "2s": "2 second continuous plays / impressions (Meta withheld the 3 second field)",
           "play": "video plays / impressions (Meta returned neither 3 second nor 2 second)"}[R["src"]]
    _head(fig, A, win, "Where every video loses them",
          "Hook rate = %s. The curve after it is measured off the people who actually started." % src)

    ax = fig.add_axes([.075, .505, .60, .355])
    ax.set_facecolor(PAPER)
    for sp_ in ax.spines.values(): sp_.set_color(LINE)
    X = [0, 1, 2, 3, 4, 5]
    LAB = ["START\n(3s view)", "25%", "50%", "75%", "95%", "100%"]
    for r in R["rows"][:6]:
        ax.plot(X, r["curve"], color=FAINT, lw=1.2, marker="o", ms=3, alpha=.75, zorder=2)
    ax.plot(X, R["acc_curve"], color=INK, lw=3.0, marker="o", ms=7, zorder=4, label="account average")
    # mark the cliff
    wi = [i for i, (lab, _d) in enumerate(R["drops"]) if lab == R["worst"][0]][0]
    ax.axvspan(wi, wi + 1, color=RED, alpha=.10, zorder=1)
    ax.annotate("BIGGEST DROP\n%s, -%.0f points" % (R["worst"][0], R["worst"][1]),
                ((wi + wi + 1) / 2.0, (R["acc_curve"][wi] + R["acc_curve"][wi + 1]) / 2.0),
                textcoords="offset points", xytext=(10, 26), fontsize=11, color=RED,
                family="DejaVu Sans", weight="bold",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))
    ax.set_xticks(X); ax.set_xticklabels(LAB, fontsize=10, color=MUTED)
    ax.set_ylim(0, 108); ax.set_xlim(-.25, 5.25)
    ax.set_ylabel("% OF THOSE WHO STARTED", fontsize=F_HD, color=MUTED, family="DejaVu Sans")
    ax.tick_params(colors=FAINT, labelsize=10)
    lg = ax.legend(loc="upper right", frameon=False, fontsize=11)
    for t in lg.get_texts(): t.set_color(INK)

    ax = _panel(fig, .505, .355, "", x0=.700, w=.245)
    ax.text(4, 94, "THE ACCOUNT CURVE", fontsize=F_HD, color=INK, family="DejaVu Sans", weight="bold")
    ax.text(4, 86, "HOOK  %.1f%%" % R["acc"]["hook"], fontsize=F_H + 5, color=INK,
            family="DejaVu Sans", weight="bold")
    ax.text(4, 79, "of impressions become a 3 second view", fontsize=9, color=FAINT,
            family="DejaVu Sans")
    y = 66
    for lab, key in (("25%", "p25"), ("50%", "p50"), ("75%", "p75"), ("95%", "p95"), ("100%", "p100")):
        ax.text(4, y, lab, fontsize=F_ROW, color=MUTED, family="DejaVu Sans")
        ax.text(96, y, "%.0f%%" % R["acc"][key], fontsize=F_ROW + 1, color=INK,
                family="DejaVu Sans", weight="bold", ha="right")
        y -= 9
    ax.text(4, 16, "Every step after the hook is the share of\npeople who started and were still\nthere at that point.",
            fontsize=9.5, color=FAINT, family="DejaVu Sans")

    ax = _panel(fig, .310, .155, "every drop, and what it means")
    for i, (lab, d) in enumerate(R["drops"]):
        x = 1.6 + i * 19.6
        col = RED if d >= R["worst"][1] * .9 else MUTED
        ax.text(x, 62, "-%.0f" % d, fontsize=22, color=col, family="DejaVu Sans", weight="bold")
        ax.text(x, 44, lab, fontsize=10, color=MUTED, family="DejaVu Sans")
        ax.text(x, 33, "points lost", fontsize=9, color=FAINT, family="DejaVu Sans")
    FIX = {"start to 25%": "They started and bailed inside the first quarter. The hook earned the view and the next line did not keep it. Cut straight to the payoff, kill the intro.",
           "25% to 50%": "You lose them in the setup. The middle is too slow. Tighten it, or move the proof earlier.",
           "50% to 75%": "They made it halfway and left before the offer. The offer is arriving too late. Move it forward.",
           "75% to 95%": "Almost everyone who got here finished. This is not where the problem is.",
           "95% to the end": "They finished. Nothing to fix here."}
    for j, ln in enumerate(_wrap("FIX THIS FIRST: " + FIX[R["worst"][0]], 98)[:2]):
        ax.text(1.6, 18 - j * 8, ln, fontsize=F_ROW, color=INK, family="DejaVu Sans",
                weight="bold")

    ax = _panel(fig, .055, .225, "the videos, by spend  ·  the whole card, plus where each one drops")
    acc7 = A.get("b7_acc") or A["summary"]
    b7 = A.get("b7") or {}
    acc_cpmr = acc7.get("cpmr") or 0
    _mhead(ax, PTOP, vs="acct")
    for i, r in enumerate(R["rows"][:5]):
        y = PTOP - 14 - i * 13.5
        ax.text(1.6, y, _clip(r["name"], 20), fontsize=11, color=INK, family="DejaVu Sans",
                weight="bold")
        b = b7.get(str(r.get("ad_id") or "")) or {}
        if b: _mrow(ax, card_of(b, b, acc7), y)
        hc = GREEN if r["hook"] >= R["acc"]["hook"] * 1.1 else (RED if r["hook"] <= R["acc"]["hook"] * .9 else MUTED)
        # this line lives in the NAME gutter only. It used to run under the metric grid.
        ax.text(1.6, y - 6.4, "hook %.1f%%  ·  25%% %.0f%%  ·  50%% %.0f%%  ·  100%% %.0f%%" % (
            r["hook"], r["curve"][1], r["curve"][2], r["curve"][5]),
            fontsize=9, color=hc, family="DejaVu Sans", weight="bold")
    ax.text(1.6, 4, "CPMR green means it reaches people cheaper than the account's %s. Red means a premium for the same reach." % _k(acc_cpmr),
            fontsize=10, color=MUTED, family="DejaVu Sans", style="italic")
    _foot(fig, win)
    return _png(fig)


# ---------- CARD: the verdict on every ad, from its own 7 days ----------
MCOLS = [("SPEND", "spend", _k, "spend"), ("ROAS", "roas", lambda v: "%.2fx" % r2(v), "roas"),
         ("CPP", "cpp", _k, "cpa"), ("CPMR", "cpmr", _k, "cpmr"),
         ("CVR", "cvr", lambda v: "%.2f%%" % r2(v), "cvr"),
         ("CTR-O", "octr", _pctv, "octr"), ("CPC", "cpc", lambda v: "%.2f" % r2(v), "cpc"),
         ("AOV", "aov", _k, "aov"), ("FRQ", "freq", lambda v: "%.1f" % r2(v), "freq"),
         ("PUR", "purch", lambda v: "%d" % int(v or 0), "purch")]


def _mrow(ax, k, y, x0=30.0, step=7.0, vcol=None):
    """Every metric that matters, on one line, with how it sits against the account."""
    VS = {"roas": "v_roas", "cpp": "v_cpp", "cpmr": "v_cpmr", "cvr": "v_cvr", "octr": "v_octr",
          "cpc": "v_cpc"}
    for j, (lab, key, fmt, ik) in enumerate(MCOLS):
        x = x0 + j * step
        ax.text(x, y, fmt(k.get(key)), fontsize=11.5, color=INK, family="DejaVu Sans",
                weight="bold", ha="right")
        vk = VS.get(key)
        if vk and k.get(vk) is not None:
            v = k[vk]
            c = MUTED if abs(v) < 5 else (GREEN if v > 0 else RED)
            ax.text(x, y - 4.8, "%+.0f%%" % v, fontsize=9.5, color=c, family="DejaVu Sans",
                    weight="bold", ha="right")


def _mhead(ax, y, x0=30.0, step=7.0, vs="acct"):
    """A percentage with no stated baseline is worthless. The baseline is printed in the
    header of every single column, so no number on this card is ever ambiguous."""
    for j, (lab, _k2, _f, _i) in enumerate(MCOLS):
        x = x0 + j * step
        ax.text(x, y, lab, fontsize=9.5, color=MUTED, family="DejaVu Sans",
                weight="bold", ha="right")
        if _k2 in ("roas", "cpp", "cpmr", "cvr", "octr", "cpc"):
            ax.text(x, y - 4.2, "vs %s" % vs, fontsize=7.8, color=FAINT,
                    family="DejaVu Sans", style="italic", ha="right")


def card_verdicts(A, win):
    V = classify(A)
    if not V: return None
    H = hit_rate(A)
    fig = _fig(16.0)
    _head(fig, A, win, "The verdict on every ad",
          "Judged on the whole card over the LAST 7 DAYS, never one day. Small percents are VS THE ACCOUNT.")

    counts = {}
    for e in V: counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    ax = _panel(fig, .790, .080, "the call")
    x = 1.6
    for v in ("SCALE", "SCALE CAREFULLY", "ITERATE", "MONITOR", "KILL"):
        n = counts.get(v, 0)
        sp_ = sum(e["k"]["spend"] for e in V if e["verdict"] == v)
        ax.text(x, 46, str(n), fontsize=24, color=VCOL[v], family="DejaVu Sans", weight="bold")
        ax.text(x, 26, v, fontsize=9.5, color=VCOL[v], family="DejaVu Sans", weight="bold")
        ax.text(x, 10, "%s of 7 day spend" % _k(sp_), fontsize=9, color=FAINT, family="DejaVu Sans")
        x += 19.6

    ax = _panel(fig, .300, .460, "every ad, its whole card, and the reason")
    _mhead(ax, PTOP, vs="acct")
    for i, e in enumerate(V[:5]):
        y = PTOP - 12 - i * 13.6
        col = VCOL[e["verdict"]]
        ax.add_patch(plt.Rectangle((1.4, y - 8.4), 0.9, 12.0, facecolor=col, edgecolor="none"))
        ax.text(3.4, y, _clip(safe(e["c"]["ad_name"]), 18), fontsize=11.5, color=INK,
                family="DejaVu Sans", weight="bold")
        ax.text(3.4, y - 5.0, e["verdict"], fontsize=9, color=col, family="DejaVu Sans",
                weight="bold")
        _mrow(ax, e["k"], y)
        ax.text(3.4, y - 10.0, _clip(e["why"], 112), fontsize=9, color=MUTED, family="DejaVu Sans")
    ax.text(1.6, 3, "All numbers are the ad's own last 7 days. Today is used only to spot volatility, never to kill.",
            fontsize=F_NOTE, color=FAINT, family="DejaVu Sans", style="italic")

    ax = _panel(fig, .050, .225, "hit rate  ·  and how many creatives that means launching")
    if H:
        ax.text(1.6, 66, "%.0f%%" % H["hr"], fontsize=34,
                color=(GREEN if H["hr"] >= 25 else (AMBER if H["hr"] >= 12 else RED)),
                family="DejaVu Sans", weight="bold")
        ax.text(14, 72, "HIT RATE", fontsize=F_H, color=INK, family="DejaVu Sans", weight="bold")
        ax.text(14, 63, "%d winners of %d ads given a real chance" % (
            H["winners"], H["tested"]), fontsize=10.5, color=MUTED, family="DejaVu Sans")
        ax.text(56, 72, "LAUNCH %d" % H["need_1"], fontsize=F_H + 4, color=INK,
                family="DejaVu Sans", weight="bold")
        ax.text(56, 63, "for 1 expected winner", fontsize=10.5, color=MUTED, family="DejaVu Sans")
        ax.text(80, 72, "LAUNCH %d" % H["need_3"], fontsize=F_H + 4, color=INK,
                family="DejaVu Sans", weight="bold")
        ax.text(80, 63, "for 3 expected winners", fontsize=10.5, color=MUTED, family="DejaVu Sans")
        for j, ln in enumerate(_wrap(H["definition"], 104)[:3]):
            ax.text(1.6, 45 - j * 9, ln, fontsize=10.5, color=INK, family="DejaVu Sans")
        ax.text(1.6, 8, "That is the definition, printed, so the number is not something you have to take on trust.",
                fontsize=F_NOTE, color=FAINT, family="DejaVu Sans", style="italic")
    else:
        ax.text(1.6, 45, "Not enough ads have cleared the evidence bar to compute a hit rate.",
                fontsize=F_ROW + 2, color=MUTED, family="DejaVu Sans")
    _foot(fig, win)
    return _png(fig)


# ---------- CARD: scale, monitor, kill. Entity by entity. ----------
def card_plan(A, win):
    V = classify(A)
    S = scenarios(A)
    HB = hook_bench(A)
    acc7 = A.get("b7_acc") or A["summary"]
    fig = _fig(15.0)
    _head(fig, A, win, "Scale. Monitor. Kill.",
          "Every line names the ad, its evidence, the exact budget action, and the threshold that would change the call.")

    GROUPS = [("SCALE", [e for e in V if e["verdict"] in ("SCALE", "SCALE CAREFULLY")][:3], GREEN, .655, .230),
              ("MONITOR", [e for e in V if e["verdict"] == "MONITOR"][:3], BLUE, .410, .230),
              ("KILL", [e for e in V if e["verdict"] == "KILL"][:3], RED, .165, .230)]
    for title, items, col, y0, h in GROUPS:
        ax = _panel(fig, y0, h, title.lower())
        if not items:
            ax.text(1.6, 45, "Nothing qualifies.", fontsize=F_ROW + 2, color=MUTED,
                    family="DejaVu Sans")
            continue
        _mhead(ax, PTOP, vs="acct")
        for i, e in enumerate(items):
            y = PTOP - 14 - i * 24
            k = e["k"]
            ax.add_patch(plt.Rectangle((1.4, y - 15), 0.9, 20, facecolor=col, edgecolor="none"))
            ax.text(3.4, y, _clip(safe(e["c"]["ad_name"]), 18), fontsize=11.5, color=INK,
                    family="DejaVu Sans", weight="bold")
            _mrow(ax, k, y)
            ax.text(3.4, y - 10.5, _clip(e["why"], 112), fontsize=9, color=MUTED,
                    family="DejaVu Sans")
            ax.text(3.4, y - 15.5, "DO: " + _clip(e["trigger"], 104), fontsize=9.8, color=col,
                    family="DejaVu Sans", weight="bold")
    ax = _panel(fig, .060, .095, "what this is worth, at today's spend")
    if S:
        ax.text(1.6, 62, "NET REVENUE %+.1f%%" % S["net_pct"], fontsize=F_H + 6,
                color=(GREEN if S["net"] >= 0 else RED), family="DejaVu Sans", weight="bold")
        txt = ("Pausing the kills frees %s a day. Raising the scale list costs %s a day. "
               "Redeployed at their own ROAS the freed money is worth %s." % (
                   _k(S["freed"]), _k(S["added"]), _k(S["redeploy"])))
        for j, ln in enumerate(_wrap(txt, 66)[:2]):
            ax.text(38, 66 - j * 11, ln, fontsize=10.5, color=MUTED, family="DejaVu Sans")
        ax.text(1.6, 34, "Each number is that ad's own 7 day ROAS applied to its own budget change.",
                fontsize=F_NOTE, color=FAINT, family="DejaVu Sans", style="italic")
        if HB:
            strong = [r for r in HB["rows"] if r["verdict"] == "STRONG HOOK"][:1]
            weak = [r for r in HB["rows"] if r["verdict"] == "WEAK HOOK"][:1]
            bits = ["Account video hook average %.1f%%" % HB["avg_hook"]]
            if strong: bits.append("strongest %s at %.1f%% (%+.0f%%)" % (
                _clip(strong[0]["name"], 22), strong[0]["hook"], strong[0]["vs"]))
            if weak: bits.append("weakest %s at %.1f%% (%+.0f%%)" % (
                _clip(weak[0]["name"], 22), weak[0]["hook"], weak[0]["vs"]))
            for j, ln in enumerate(_wrap("CREATIVE LEARNING:  " + "   ·   ".join(bits), 118)[:2]):
                ax.text(1.6, 16 - j * 9, ln, fontsize=10, color=INK, family="DejaVu Sans",
                        weight="bold")
    _foot(fig, win)
    return _png(fig)


# ---------- CARD: what happens if, per ad ----------
def card_scenarios(A, win):
    S = scenarios(A)
    if not S: return None
    fig = _fig(12.6)
    _head(fig, A, win, "What each move is worth",
          "Not 'scale winners'. This ad, this budget change, priced at its own last 7 days.")

    moves = S["moves"]
    ax = fig.add_axes([.330, .300, .360, .520])
    ax.set_facecolor(BONE)
    for sp_ in ax.spines.values(): sp_.set_visible(False)
    ax.set_yticks([]); ax.tick_params(colors=FAINT, labelsize=10)
    n = len(moves)
    mx = max(abs(m["delta_rev"]) for m in moves) or 1
    for i, m in enumerate(moves):
        y = n - 1 - i
        col = GREEN if m["delta_rev"] >= 0 else RED
        ax.barh([y], [m["delta_rev"]], height=.6, color=col, alpha=.9, edgecolor="none")
    ax.axvline(0, color=INK, lw=1.2)
    ax.set_xlim(-mx * 1.3, mx * 1.3); ax.set_ylim(-.6, n - .4)
    ax.set_xlabel("REVENUE PER DAY", fontsize=F_HD, color=MUTED, family="DejaVu Sans")

    for i, m in enumerate(moves):
        yc = .300 + .520 * ((n - 1 - i) + .5) / n
        col = VCOL["SCALE"] if m["kind"] == "SCALE" else RED
        fig.text(.318, yc + .010, _clip(m["name"], 24), fontsize=F_ROW + 1, color=INK,
                 family="DejaVu Sans", weight="bold", ha="right", va="center")
        fig.text(.318, yc - .010, "%s  ·  %s" % (m["action"], m["at"]), fontsize=9.5,
                 color=col, family="DejaVu Sans", ha="right", va="center")
        fig.text(.700, yc + .010, "%s%s revenue / day" % (
            "+" if m["delta_rev"] >= 0 else "-", _k(abs(m["delta_rev"]))),
            fontsize=F_ROW + 1, color=(GREEN if m["delta_rev"] >= 0 else RED),
            family="DejaVu Sans", weight="bold", va="center")
        fig.text(.700, yc - .010, "%s%s spend / day" % (
            "+" if m["delta_spend"] >= 0 else "-", _k(abs(m["delta_spend"]))),
            fontsize=9.5, color=MUTED, family="DejaVu Sans", va="center")

    ax = _panel(fig, .075, .190, "the whole move together")
    ax.text(1.6, 66, "NET REVENUE %+.1f%%   at %s%s a day of spend" % (
        S["net_pct"], "+" if S["net_spend"] >= 0 else "-", _k(abs(S["net_spend"]))),
        fontsize=F_H + 6, color=(GREEN if S["net"] >= 0 else RED),
        family="DejaVu Sans", weight="bold")
    for j, ln in enumerate(_wrap(
            "Kills give back %s of revenue a day but free %s of spend. Redeployed into the scale "
            "list at their own ROAS that is worth %s." % (
                _k(S["lost"]), _k(S["freed"]), _k(S["redeploy"])), 104)[:2]):
        ax.text(1.6, 42 - j * 9, ln, fontsize=F_ROW, color=MUTED, family="DejaVu Sans")
    ax.text(1.6, 14, "A pause is only worth doing if the money lands somewhere better. That is why the redeployment is priced, not assumed.",
            fontsize=F_NOTE, color=FAINT, family="DejaVu Sans", style="italic")
    _foot(fig, win)
    return _png(fig)


# ---------- CARDS 5-7: campaigns / ad sets / ads, as bars. Ranked, not tabulated. ----------
DXCOL = {"FATIGUE": AMBER, "CREATIVE — HOOK": RED, "CREATIVE — THE CLICK": RED,
         "LANDING PAGE": BLUE, "CHECKOUT": BLUE, "BASKET": AMBER, "AUCTION": AMBER,
         "MIXED": MUTED}


def card_blame(A, win, level="ad"):
    """Who is accountable for the revenue move. In EGP, and as a share of the whole move.
    Every bar is one entity. The bars add up to the account. Nothing hides."""
    B = attribution(A, level, n=9)
    if not B or not B["rows"]: return None
    rows = B["rows"]
    LVN = {"campaign": "campaign", "adset": "ad set", "ad": "ad"}[level]

    fig = _fig(15.4)
    _head(fig, A, win, "Who moved the revenue",
          "Bars are EGP gained or lost vs the period before. The percent is that %s's share of "
          "the account's whole move." % LVN)

    # ---- the diverging bar chart. Right of zero made money, left of zero lost it.
    AX0, AY0, AW_, AH = .300, .420, .350, .430
    ax = fig.add_axes([AX0, AY0, AW_, AH])
    ax.set_facecolor(BONE)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.set_yticks([])
    n = len(rows)
    mx = max(abs(r["d_rev"]) for r in rows) or 1
    for i, r in enumerate(rows):
        y = n - 1 - i
        col = GREEN if r["d_rev"] >= 0 else RED
        ax.barh([y], [r["d_rev"]], height=.62, color=col, alpha=.9, edgecolor="none")
        # The label goes on the OPPOSITE side of zero to the bar. That half is always empty,
        # so a long label can never run into the name gutter or into another bar.
        off = mx * .05
        pos = r["d_rev"] >= 0
        ax.text(-off if pos else off, y,
                "%s%s  ·  %+.0f%%" % ("+" if pos else "-", _k(abs(r["d_rev"])), r["share"]),
                fontsize=11.5, color=col, family="DejaVu Sans", weight="bold",
                ha="right" if pos else "left", va="center", zorder=3)
    ax.axvline(0, color=INK, lw=1.4, zorder=4)
    ax.set_ylim(-.6, n - .4)
    ax.set_xlim(-mx * 1.30, mx * 1.30)
    ax.set_xlabel("REVENUE CHANGE, EGP, VS THE PERIOD BEFORE", fontsize=F_HD, color=MUTED,
                  family="DejaVu Sans")
    ax.tick_params(colors=FAINT, labelsize=9)

    # names in their own gutter so a long name can never collide with a bar
    for i, r in enumerate(rows):
        yc = AY0 + AH * ((n - 1 - i) + .5) / n
        fig.text(AX0 - .014, yc + .010, _clip(r["name"], 26), fontsize=F_ROW, color=INK,
                 family="DejaVu Sans", weight="bold", ha="right", va="center")
        sub = ("NEW — no prior period to compare" if r["new"]
               else "%s to %s  ·  ROAS %.2fx" % (_k(r["prev_rev"]), _k(r["rev"]), r2(r["roas"])))
        fig.text(AX0 - .014, yc - .008, sub, fontsize=10, color=FAINT,
                 family="DejaVu Sans", ha="right", va="center")
        if r.get("dx"):
            fig.text(AX0 - .014, yc - .024, r["dx"][0], fontsize=9.5,
                     color=DXCOL.get(r["dx"][0], MUTED), family="DejaVu Sans",
                     weight="bold", ha="right", va="center")

    # ---- the totals, stated plainly
    ax = _panel(fig, .420, .430, "the move", x0=.680, w=.265)
    ax.text(4, PTOP - 4, "TOTAL", fontsize=F_HD, color=MUTED, family="DejaVu Sans", weight="bold")
    tc = GREEN if B["d_tot"] >= 0 else RED
    ax.text(4, PTOP - 16, "%s%s" % ("+" if B["d_tot"] >= 0 else "-", _k(abs(B["d_tot"]))),
            fontsize=30, color=tc, family="DejaVu Sans", weight="bold")
    ax.text(4, PTOP - 24, "EGP of revenue, against the period before", fontsize=9.5,
            color=FAINT, family="DejaVu Sans")
    ax.text(4, PTOP - 36, "WHAT GAINED", fontsize=F_HD, color=MUTED, family="DejaVu Sans", weight="bold")
    ax.text(4, PTOP - 45, "+%s" % _k(B["gain"]), fontsize=20, color=GREEN,
            family="DejaVu Sans", weight="bold")
    ax.text(4, PTOP - 56, "WHAT LOST", fontsize=F_HD, color=MUTED, family="DejaVu Sans", weight="bold")
    ax.text(4, PTOP - 65, "%s" % _k(B["loss"]), fontsize=20, color=RED,
            family="DejaVu Sans", weight="bold")
    for j, ln in enumerate(_wrap(
            "Gainers and losers are both real. The account only shows you the net, which is how "
            "a good %s gets hidden by a bad one." % LVN, 32)[:3]):
        ax.text(4, 11 - j * 3.9, ln, fontsize=8.6, color=FAINT, family="DejaVu Sans", style="italic")

    # ---- the cause, per entity, in words. Name left, money right, cause on its own line.
    # Nothing shares a horizontal band with anything else, so nothing can collide.
    ax = _panel(fig, .055, .330, "why each one moved  ·  the stage that broke, and where to go")
    y = PTOP - 6
    for r in rows[:5]:
        if not r.get("dx"):
            continue
        cause, what, todo = r["dx"]
        col = DXCOL.get(cause, MUTED)
        mcol = GREEN if r["d_rev"] >= 0 else RED
        ax.text(1.6, y, _clip(r["name"], 34), fontsize=F_ROW + 1, color=INK,
                family="DejaVu Sans", weight="bold")
        ax.text(98.4, y, "%s%s EGP   ·   %+.0f%% of the move" % (
            "+" if r["d_rev"] >= 0 else "-", _k(abs(r["d_rev"])), r["share"]),
            fontsize=F_ROW + 1, color=mcol, family="DejaVu Sans", weight="bold", ha="right")
        ax.text(1.6, y - 5.6, cause, fontsize=10, color=col, family="DejaVu Sans", weight="bold")
        ax.text(24, y - 5.6, _clip(what, 96), fontsize=10, color=MUTED, family="DejaVu Sans")
        ax.text(1.6, y - 10.8, "DO: " + _clip(todo, 116), fontsize=10, color=INK,
                family="DejaVu Sans", style="italic")
        y -= 16.0
    _foot(fig, win)
    return _png(fig)


def card_bars(A, win, level):
    LV = {"campaign": ("Campaigns", "seg_campaigns"), "adset": ("Ad sets", "seg_adsets"),
          "ad": ("Ads", "seg_ads")}
    title, key = LV[level]
    roll_ = A.get(key) or {}
    proll = A.get(key + "_prev") or {}
    ents = [(k_, e) for k_, e in roll_.items() if e["total"]["spend"] > 0]
    ents = sorted(ents, key=lambda kv: -kv[1]["total"]["spend"])[:8]
    if not ents: return None
    acc = A["summary"]["roas"] or 0
    prevmap = {k_: e["total"] for k_, e in proll.items()}   # match on ID, never on name

    # each entity's share of the ACCOUNT's total revenue move, so the bars mean something
    d_all = 0.0
    for k_, e in roll_.items():
        d_all += (e["total"].get("rev") or 0) - ((prevmap.get(k_) or {}).get("rev") or 0)
    denom = abs(d_all) if abs(d_all) > 1 else (sum(
        abs((e["total"].get("rev") or 0) - ((prevmap.get(k_) or {}).get("rev") or 0))
        for k_, e in roll_.items()) or 1)

    fig = _fig(14.4)
    _head(fig, A, win, "%s: where the money is and what it returns" % title,
          "Bars are spend. Green beats the account's %.2fx, red loses to it." % r2(acc))

    AX0, AY0, AW, AH = .255, .150, .175, .660
    ax = fig.add_axes([AX0, AY0, AW, AH])
    ax.set_facecolor(BONE)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.set_yticks([]); ax.tick_params(colors=FAINT, labelsize=10)
    ms = max(e["total"]["spend"] for _k_, e in ents) or 1
    n = len(ents)
    for i, (k_, e) in enumerate(ents):
        m = e["total"]; y = n - 1 - i
        col = GREEN if (m["roas"] or 0) >= acc else RED
        ax.barh([y], [m["spend"]], height=.58, color=col, alpha=.9, edgecolor="none")
        # A label only goes INSIDE the bar if the bar is actually long enough to hold it.
        # Otherwise white text spills out of the bar and lands on the name gutter.
        if m["spend"] >= ms * .32:
            ax.text(m["spend"] - ms * .02, y, _k(m["spend"]), fontsize=F_ROW + 1, color=PAPER,
                    family="DejaVu Sans", weight="bold", ha="right", va="center", zorder=3)
        else:
            ax.text(m["spend"] + ms * .02, y, _k(m["spend"]), fontsize=F_ROW + 1, color=INK,
                    family="DejaVu Sans", weight="bold", ha="left", va="center", zorder=3)
    ax.set_ylim(-.6, n - .4); ax.set_xlim(0, ms * 1.02)
    ax.set_xlabel("SPEND", fontsize=F_HD, color=MUTED, family="DejaVu Sans")

    # names live in their own gutter, so they can never be clipped by the plot
    for i, (k_, e) in enumerate(ents):
        m = e["total"]; q = prevmap.get(k_)
        yc = AY0 + AH * ((n - 1 - i) + .5) / n
        fig.text(AX0 - .012, yc + .016, _clip(e["name"], 21), fontsize=F_ROW + 1, color=INK,
                 family="DejaVu Sans", weight="bold", ha="right", va="center")
        parts = sorted(e["segments"].items(), key=lambda kv: -kv[1]["spend"])[:3]
        seg_t = "  ".join("%s %d%%" % (SEGN.get(k2, k2).split()[0], round(v["spend"] / (m["spend"] or 1) * 100))
                          for k2, v in parts)
        fig.text(AX0 - .012, yc + .001, seg_t, fontsize=10, color=FAINT, family="DejaVu Sans",
                 ha="right", va="center")
        d_sp = _safe_pct(m["spend"], (q or {}).get("spend") or 0) if q else None
        lab_sp = ("spend %s vs prev" % _d(d_sp)) if d_sp is not None else "new or restarted"
        fig.text(AX0 - .012, yc - .014, lab_sp, fontsize=10, color=BLUE,
                 family="DejaVu Sans", weight="bold", ha="right", va="center")

    # THE ACCOUNTABILITY COLUMN. What this entity did to revenue, in EGP, and what share of
    # the whole account's move that is. This is the number he actually asked for.
    RX = .530
    fig.text(RX, AY0 + AH + .040, "REVENUE Δ", fontsize=10.5, color=INK,
             family="DejaVu Sans", weight="bold", ha="right")
    fig.text(RX, AY0 + AH + .022, "EGP · share of move", fontsize=8.6, color=FAINT,
             family="DejaVu Sans", style="italic", ha="right")
    # ONE legend line for the whole grid. Repeating "vs prev" under ten columns made the
    # headers collide with each other, which is worse than not labelling them at all.
    fig.text(.055, AY0 + AH + .062,
             "EVERY SMALL PERCENT BELOW A NUMBER = THAT %s vs ITS OWN PREVIOUS PERIOD"
             % title.rstrip("s").upper(),
             fontsize=9.5, color=INK, family="DejaVu Sans", weight="bold")
    for i, (k_, e) in enumerate(ents):
        m = e["total"]; q = prevmap.get(k_)
        y = AY0 + AH * ((n - 1 - i) + .5) / n + .008
        if not q:
            fig.text(RX, y, "new", fontsize=11.5, color=MUTED, family="DejaVu Sans",
                     weight="bold", ha="right")
            continue
        d = (m.get("rev") or 0) - (q.get("rev") or 0)
        col = GREEN if d >= 0 else RED
        fig.text(RX, y, "%s%s" % ("+" if d >= 0 else "-", _k(abs(d))), fontsize=13,
                 color=col, family="DejaVu Sans", weight="bold", ha="right")
        fig.text(RX, y - .018, "%+.0f%% of move" % (d / denom * 100.0), fontsize=9.5,
                 color=col, family="DejaVu Sans", weight="bold", ha="right")

    # CPM is dropped. CPMR is the one that matters and two near-identical columns were what
    # squeezed the grid until the numbers touched each other.
    COLS = [("ROAS", "roas", lambda v: "%.2f" % r2(v), True), ("CPP", "cpa", _k, False),
            ("AOV", "aov", _k, True), ("CVR", "cvr", lambda v: "%.2f%%" % r2(v), True),
            ("CPC", "cpc", lambda v: "%.2f" % r2(v), False), ("CTR-O", "octr", _pctv, True),
            ("CPMR", "cpmr", _k, False),
            ("FRQ", "freq", lambda v: "%.1f" % r2(v), False),
            ("PUR", "purch", lambda v: "%d" % int(v or 0), True)]
    xs = [.600 + i * .0485 for i in range(9)]      # evenly spaced, so nothing can collide
    for x, c in zip(xs, COLS):
        fig.text(x, AY0 + AH + .040, c[0], fontsize=10.5, color=INK, family="DejaVu Sans",
                 weight="bold", ha="right")
    for i, (k_, e) in enumerate(ents):
        m = e["total"]; q = prevmap.get(k_)
        y = AY0 + AH * ((n - 1 - i) + .5) / n + .008
        for x, (nm_, k2, fmt, ug) in zip(xs, COLS):
            v = m_cpc(m) if k2 == "cpc" else m.get(k2)
            fig.text(x, y, fmt(v), fontsize=12, color=INK, family="DejaVu Sans",
                     weight="bold", ha="right")
            pv = (m_cpc(q) if k2 == "cpc" else q.get(k2)) if q else None
            d = _safe_pct(v or 0, pv or 0, floor=(0.01 if k2 not in ("spend",) else 50)) if q else None
            fig.text(x, y - .018, _d(d) if d is not None else "n/a", fontsize=9.8,
                     color=impact(k2, d, v), family="DejaVu Sans", weight="bold", ha="right")
    _foot(fig, win)
    return _png(fig)


def msg_short(A, win):
    """The cards carry the numbers. This is the call. Percent only, never absolutes,
    never standard deviations."""
    s = A["summary"]; p = s.get("prev") or {}
    H = A.get("hist") or []
    nd = WIN_DAYS.get(win, 1)
    W = waterfall(s, p); M = mix_split(A); S = plan(A); F = fatiguing(A["creatives"])
    B = normal_band(H, "rev", (s["rev"] or 0) / nd)
    segs = A.get("segs") or {}
    L = ["*%s — %s*" % (A["account"]["name"].upper(), WIN_TITLE.get(win, "MEMO"))]

    if B:
        if B["inside"]:
            L.append("Revenue is *%+.0f%%* against a typical day.\nThat sits inside this account's normal swing of %+.0f%% to %+.0f%%, so treat it as noise, not a trend." % (
                B["vs"], B["lo_p"], B["hi_p"]))
        else:
            L.append("Revenue is *%+.0f%%* against a typical day.\nThat is outside the normal swing of %+.0f%% to %+.0f%%, so this is a real move. Act on it." % (
                B["vs"], B["lo_p"], B["hi_p"]))
    else:
        L.append("Revenue %s, %s against the period before." % (
            money(s["rev"]), _d(pct(s["rev"], p.get("rev") or 0))))

    if W:
        # The lever that MOVED revenue must push the SAME WAY revenue went. A lever that
        # pulled the other way is a drag, not the reason. Never call a drag the reason.
        tot = sum(x["rev_pct"] for x in W["steps"])
        up = tot >= 0
        same = [x for x in W["steps"] if (x["rev_pct"] >= 0) == up]
        opp = [x for x in W["steps"] if (x["rev_pct"] >= 0) != up]
        t = max(same or W["steps"], key=lambda x: abs(x["rev_pct"]))

        def _why(k, r):
            return {"SPEND": "You bought %s media. The account did not get better or worse." % (
                        "more" if r >= 0 else "less"),
                    "CPC": "Clicks got %s. That is auction cost and click rate, so it is creative and audience." % (
                        "cheaper" if r >= 0 else "dearer"),
                    "CVR": "Conversion rate %s. That is offer, price or landing page, not the ad account." % (
                        "rose" if r >= 0 else "fell"),
                    "AOV": "Basket size %s. That is the mix of what is selling." % (
                        "rose" if r >= 0 else "fell")}.get(k, "")

        # TWO DIFFERENT BASELINES, SO NAME BOTH. The line above is versus a TYPICAL DAY of the
        # last 30. The lever split below is versus the PERIOD IMMEDIATELY BEFORE. Never print
        # one number against one baseline and the next against another without saying so.
        pp = _safe_pct(s.get("rev") or 0, p.get("rev") or 0)
        L.append("Against the *period immediately before* (a different baseline to the line above), revenue %s.\n*%s is the reason.* It is %d%% of that move and worth *%+.1f%%* of revenue on its own.\n%s" % (
            ("moved *%+.0f%%*" % pp) if pp is not None else "moved",
            t["k"], t["pct"], t["rev_pct"], _why(t["k"], t["rev_pct"])))

        if opp:
            d = max(opp, key=lambda x: abs(x["rev_pct"]))
            if abs(d["rev_pct"]) >= 3:
                # A lever pointing against a FALL is holding you up, so do not "fix" it.
                # A lever pointing against a RISE is capping you, so that one you fix.
                tail = ("*%s capped the gain*, worth *%+.1f%%* of revenue.\n%s\nFix that and the gain gets bigger." % (
                            d["k"], d["rev_pct"], _why(d["k"], d["rev_pct"]))
                        if up else
                        "*%s cushioned the fall*, worth *%+.1f%%* of revenue.\n%s\nWithout it the drop would be worse. Protect it." % (
                            d["k"], d["rev_pct"], _why(d["k"], d["rev_pct"])))
                L.append(tail)

    if M:
        dom = "mix" if abs(M["mix"]) >= abs(M["perf"]) else "performance"
        if dom == "mix" and abs(M["mix"]) >= 3:
            L.append("Blended ROAS moved *%+.0f%%*, but *%+.0f%%* of that is mix, not performance.\nYou shifted money between audiences. The account did not actually get better." % (
                M["total"], M["mix"]))
        else:
            L.append("Blended ROAS moved *%+.0f%%*, and *%+.0f%%* of that is real performance, not mix." % (
                M["total"], M["perf"]))

    room = [k for k in ("NEW", "ENGAGED", "EXISTING")
            if segs.get(k) and (segs[k].get("freq") or 9) < 3.0 and segs[k].get("share", 0) >= 5]
    full = [k for k in ("NEW", "ENGAGED", "EXISTING")
            if segs.get(k) and (segs[k].get("freq") or 0) >= 3.0 and segs[k].get("share", 0) >= 5]
    if room or full:
        bits = []
        if room: bits.append("headroom in %s" % ", ".join(SEGN[k].lower() for k in room))
        if full: bits.append("%s is saturated" % ", ".join(SEGN[k].lower() for k in full))
        L.append("Budget: %s." % "; ".join(bits))

    if F:
        L.append("*Burning:* %s, %s.\nOutbound CTR %+.0f%% while frequency %+.0f%%." % (
            _clip(F[0]["name"], 36), F[0]["verdict"].lower(),
            F[0]["d"]["octr"] or 0, F[0]["d"]["freq"] or 0))
    if S:
        L.append("*Do this:* cut %s (%.2fx, %.0f%% under the account) by 30%%, fund %s (%.2fx).\nSame budget, revenue *%+.1f%%*." % (
            _clip(safe(S["cut"][0]["ad_name"]), 32), r2(S["cut"][0]["roas"]),
            (1 - (S["cut"][0]["roas"] or 0) / (s["roas"] or 1)) * 100,
            _clip(safe(S["fund"][0]["ad_name"]), 32), r2(S["fund"][0]["roas"]), S["gain_pct"]))

    # WHO IS ACCOUNTABLE. Named, priced in EGP, share of the move, cause, and a link that
    # opens that exact ad in Ads Manager. No hunting.
    B = attribution(A, "ad", n=4)
    if B and B["rows"]:
        acct_id = A["account"].get("id") or ""
        L.append("*WHO MOVED IT* — revenue change vs the period before, and each one's share of the account's move:")
        for r in B["rows"]:
            cause = (" · %s" % r["dx"][0]) if r.get("dx") else (" · new" if r["new"] else "")
            L.append("• %s — *%s%s EGP* (%+.0f%% of the move)%s" % (
                ads_link(_clip(safe(r["name"]), 40), acct_id, r["ent_id"]),
                "+" if r["d_rev"] >= 0 else "-", _k(abs(r["d_rev"])), r["share"], cause))
        top = B["rows"][0]
        if top.get("dx"):
            L.append("_%s_\n*Go and do:* %s" % (top["dx"][1], top["dx"][2]))

    L.append("_Margin and LTV are unknown from ad data. Nothing above assumes them._")
    return "\n".join(x for x in L if x)


def ads_link(label, acct_id, ad_id):
    """A Slack link that opens THAT EXACT AD in Ads Manager, filtered to it. Named ads in a
    memo are useless if he has to go and find them by hand."""
    a = str(acct_id or "").replace("act_", "")
    if not a or not ad_id:
        return label
    url = ("https://adsmanager.facebook.com/adsmanager/manage/ads"
           "?act=%s&selected_ad_ids=%s" % (a, ad_id))
    return "<%s|%s>" % (url, label)


def render_cards(A, win):
    """The whole board: levers, campaigns, ad sets, ads, and the move."""
    out = []
    try:
        _mpl()
        for suf, fn in (("pulse", lambda: card_pulse(A, win)),
                        ("trend", lambda: card_trend(A, win)),
                        ("audience", lambda: card_audience(A, win)),
                        ("funnel", lambda: card_funnel(A, win)),
                        ("blame", lambda: card_blame(A, win, "ad")),
                        ("blame_camp", lambda: card_blame(A, win, "campaign")),
                        ("decay", lambda: card_decay(A, win)),
                        ("retention", lambda: card_retention(A, win)),
                        ("verdicts", lambda: card_verdicts(A, win)),
                        ("scenarios", lambda: card_scenarios(A, win)),
                        ("plan", lambda: card_plan(A, win)),
                        ("campaigns", lambda: card_bars(A, win, "campaign")),
                        ("adsets", lambda: card_bars(A, win, "adset")),
                        ("ads", lambda: card_bars(A, win, "ad"))):
            try:
                png = fn()
                if png: out.append((suf, png))
            except Exception as e:
                import traceback
                sys.stderr.write("[card] %s failed: %s\n%s\n" % (suf, e, traceback.format_exc()))
    except Exception as e:
        sys.stderr.write("[card] render failed: %s\n" % e)
    return out


def slug(s):
    return "".join(ch if ch.isalnum() else "-" for ch in (s or "").lower()).strip("-")

def img_url(fn):
    """raw.githubusercontent serves a committed file instantly, no Pages build needed."""
    repo = os.environ.get("GITHUB_REPOSITORY", "bOBsNVAGINA123/ai-media-buyer")
    return "https://raw.githubusercontent.com/%s/main/docs/img/%s?v=%d" % (repo, fn, int(time.time()))

def push_images():
    """Commit the cards so Slack has a public URL to render."""
    import subprocess
    try:
        subprocess.run(["git", "config", "user.name", "ai-media-buyer"], check=False)
        subprocess.run(["git", "config", "user.email", "bot@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", "docs/img", "docs/data.json"], check=False)
        subprocess.run(["git", "commit", "-m", "auto: briefing cards"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        r = subprocess.run(["git", "push"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sys.stderr.write("[cards] pushed (rc=%s)\n" % r.returncode)
        return r.returncode == 0
    except Exception as e:
        sys.stderr.write("[cards] push failed: %s\n" % e); return False

def slack_image_url(channel, url, comment):
    """Post the card as an image block. Needs only chat:write, which the bot already has."""
    if not channel: return False
    if not SLACK_TOKEN:
        print("[card-url:%s] %s\n" % (channel, url)); return True
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": comment}},
              {"type": "image", "image_url": url, "alt_text": "performance briefing"}]
    body = json.dumps({"channel": channel, "text": comment, "blocks": blocks,
                       "unfurl_links": False}).encode()
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=body,
        headers={"Authorization": "Bearer %s" % SLACK_TOKEN,
                 "Content-Type": "application/json; charset=utf-8"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if not r.get("ok"):
            sys.stderr.write("[card-url] %s: %s\n" % (channel, r.get("error"))); return False
        return True
    except Exception as e:
        sys.stderr.write("[card-url] %s\n" % e); return False


def slack_image(channel, png, title, comment=""):
    """Upload the card straight into the channel. No dependency on any external host."""
    if not channel or not png: return False
    if not SLACK_TOKEN:
        print("[card:%s] %d bytes\n" % (channel, len(png))); return True
    try:
        # 1. reserve an upload url
        q = urllib.parse.urlencode({"filename": title + ".png", "length": len(png)}).encode()
        r1 = urllib.request.Request("https://slack.com/api/files.getUploadURLExternal", data=q,
              headers={"Authorization": "Bearer %s" % SLACK_TOKEN,
                       "Content-Type": "application/x-www-form-urlencoded"})
        j1 = json.loads(urllib.request.urlopen(r1, timeout=30).read().decode())
        if not j1.get("ok"):
            sys.stderr.write("[card] getUploadURL: %s\n" % j1.get("error")); return False
        # 2. push the bytes
        r2 = urllib.request.Request(j1["upload_url"], data=png, method="POST")
        urllib.request.urlopen(r2, timeout=60).read()
        # 3. publish it into the channel
        body = json.dumps({"files": [{"id": j1["file_id"], "title": title}],
                           "channel_id": channel, "initial_comment": comment}).encode()
        r3 = urllib.request.Request("https://slack.com/api/files.completeUploadExternal", data=body,
              headers={"Authorization": "Bearer %s" % SLACK_TOKEN,
                       "Content-Type": "application/json; charset=utf-8"})
        j3 = json.loads(urllib.request.urlopen(r3, timeout=30).read().decode())
        if not j3.get("ok"):
            sys.stderr.write("[card] complete: %s\n" % j3.get("error")); return False
        return True
    except Exception as e:
        sys.stderr.write("[card] %s\n" % e); return False


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
    L += ["", BAR, "*AUDIENCE - % of spend by who they actually are*",
          "_Read from each ad set's real targeting. Engaged = interacted, never bought. Existing = has purchased. New = prospecting (broad, interest, lookalike)._", ""]
    # ALWAYS show all three, even at 0%. A zero is a finding, not a reason to hide the row.
    for sg in sorted(segs, key=lambda sg: csp[sg], reverse=True):
        pu = sum(c["purch"] for c in _in(sg)); lcs = sum(c["lc"] for c in _in(sg))
        if not csp[sg] and not psp[sg]:
            L.append("*%s* - *0%%* of spend.  Nothing is running to this audience at all." % SEGN[sg]); L.append(""); continue
        L.append("*%s* - spend %s, *%d%% of spend* (was %d%%)" % (SEGN[sg], money(csp[sg]), round(mixC[sg]), round(mixP[sg])))
        L.append("     ROAS %s (was %s) · CPA %s · AOV %s · CVR %s%% · reach %s · frequency %s" % (
            r2(roC[sg]), r2(roP[sg]),
            money(round(csp[sg] / pu)) if pu else "n/a", money(round(crev[sg] / pu)) if pu else "n/a",
            round(pu / lcs * 100, 2) if lcs else 0, money(crch[sg]), r2(frq[sg])))
        L.append("")
    live = [sg for sg in segs if csp[sg] > 0]
    if len(live) <= 1:
        only = SEGN[live[0]] if live else "nothing"
        L.append("*Read:* every EGP is going to *%s*.  There is no retargeting and no customer re-activation running at all, so nothing is monetising the traffic you already paid for.  That is the single biggest gap here, not the ROAS." % only)
    elif gained == lost:
        L.append("*Read:* the audience mix did not move this period.  The split is a decision you are making, not one Meta is making for you.")
    else:
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


# ----------------------- the full metric set, at every level -----------------------
# CPC, AOV, CPMR, CVR, ROAS, CPA, CTR, Outbound CTR, CPM, frequency. Always. No exceptions.
def full_metrics(g):
    spend = sum(c["spend"] for c in g); rev = sum(c["rev"] for c in g); purch = sum(c["purch"] for c in g)
    impr = sum(c["impr"] for c in g); reach = sum(c["reach"] for c in g); lc = sum(c["lc"] for c in g)
    atc = sum(c.get("atc", 0) for c in g)
    return {
        "spend": round(spend), "rev": round(rev), "purch": int(purch),
        "roas": round(rev / spend, 2) if spend else 0,
        "cpa":  round(spend / purch) if purch else None,
        "aov":  round(rev / purch) if purch else None,
        "cpc":  round(spend / lc, 2) if lc else None,
        "cvr":  round(purch / lc * 100, 2) if lc else None,
        "ctr":  round(sum(c["ctr"] * c["impr"] for c in g) / impr, 2) if impr else 0,
        "octr": round(sum(c["octr"] * c["impr"] for c in g) / impr, 2) if impr else 0,
        "cpm":  round(spend / impr * 1000, 2) if impr else 0,
        "cpmr": round(spend / reach * 1000, 2) if reach else 0,
        "freq": round(impr / reach, 2) if reach else 0,
        "atc_rate": round(atc / lc * 100, 1) if lc else 0,
        # the video funnel, so diagnose() can tell a hook problem from a page problem
        "hook": round(sum(c.get("v3", 0) for c in g) / impr * 100, 1) if impr else 0,
        "hold": round(sum(c.get("thru", 0) for c in g) / (sum(c.get("v3", 0) for c in g) or 1) * 100, 1),
        "reach": int(reach), "impr": int(impr), "lc": round(lc), "n": len(g),
    }

def dom_seg(g):
    """The audience a campaign/adset actually buys. MIXED when it is genuinely split."""
    sp = {}
    for c in g: sp[c["seg"]] = sp.get(c["seg"], 0) + c["spend"]
    if not sp: return "NEW"
    tot = sum(sp.values()) or 1
    top = max(sp, key=sp.get)
    return top if sp[top] / tot >= 0.8 else "MIXED"

def group_full(rows, keyfn, namefn, tot_spend):
    buckets = {}
    for c in rows:
        buckets.setdefault(keyfn(c), []).append(c)
    out = []
    for k, g in buckets.items():
        m = full_metrics(g)
        m["name"] = namefn(g[0]) or "(unnamed)"
        m["seg"] = dom_seg(g)
        m["share"] = round(m["spend"] / tot_spend * 100, 1) if tot_spend else 0
        out.append(m)
    out.sort(key=lambda x: -x["spend"])
    return out


# ----------------------- WHERE exactly did it move -----------------------
def movers(rows, n=5):
    """Name the exact ads that caused the revenue move, biggest absolute swing first."""
    out = []
    for c in rows:
        p = c.get("prev")
        if not p or not p.get("rev"): continue
        d = c["rev"] - (p["rev"] or 0)
        if abs(d) < 1: continue
        out.append({"name": c["ad_name"], "seg": c["seg"], "adset": c["adset"], "campaign": c["campaign"],
                    "d_rev": round(d), "rev": round(c["rev"]), "prev_rev": round(p["rev"] or 0),
                    "roas": c["roas"], "prev_roas": p.get("roas"), "spend": round(c["spend"]),
                    "prev_spend": round(p.get("spend") or 0),
                    "cvr": c["cvr"], "prev_cvr": p.get("cvr"), "cpa": c["cpa"], "prev_cpa": p.get("cpa")})
    out.sort(key=lambda x: -abs(x["d_rev"]))
    return out[:n]

def scenario(A):
    """The concrete move: cut the worst, fund the best, and what that is worth."""
    s = A["summary"]; rows = A["creatives"]
    sig = [c for c in rows if c.get("significant")]
    if not sig: return None
    cut = None
    bleed = [c for c in sig if c["roas"] and c["roas"] < s["roas"] * 0.8]
    if bleed: cut = min(bleed, key=lambda c: c["roas"])
    fund = None
    good = [c for c in sig if c["roas"] and c["roas"] >= s["roas"] and c["freq"] < freq_ceiling(c) and (c["purch"] or 0) >= 2]
    if good: fund = max(good, key=lambda c: (c["roas"] or 0) * (c["spend"] ** 0.3))
    if not cut or not fund or cut["ad_id"] == fund["ad_id"]: return None
    freed = round(cut["spend"] * 0.3)
    gain = round(freed * ((fund["roas"] or 0) - (cut["roas"] or 0)))
    return {"cut": {"name": cut["ad_name"], "seg": cut["seg"], "spend": round(cut["spend"]), "roas": cut["roas"],
                    "cpa": cut["cpa"], "cvr": cut["cvr"]},
            "fund": {"name": fund["ad_name"], "seg": fund["seg"], "spend": round(fund["spend"]), "roas": fund["roas"],
                     "cpa": fund["cpa"], "cvr": fund["cvr"], "freq": fund["freq"]},
            "freed": freed, "gain": gain,
            "rev_now": round(s["revenue"]), "rev_then": round(s["revenue"] + gain)}


# ----------------------- payload for the visual dashboard -----------------------
def strat_payload(A):
    """Everything the Strategic dashboard renders, per account per window."""
    s = A["summary"]; rows = A["creatives"]; prev = s.get("prev") or {}
    tot = sum(c["spend"] for c in rows) or 1
    # AUDIENCE: the complete metric set per segment, never a partial row
    segs = {}
    for sg in ("NEW", "ENGAGED", "EXISTING"):
        g = [c for c in rows if c["seg"] == sg]
        m = full_metrics(g) if g else full_metrics([])
        m["name"] = SEGN[sg]
        m["share"] = round(m["spend"] / tot * 100, 1) if tot else 0
        segs[sg] = m
    def cd(c):
        return {"name": c["ad_name"], "seg": c["seg"], "segn": SEGN[c["seg"]], "spend": round(c["spend"]),
                "share": c.get("spend_share"), "roas": c["roas"], "cpa": c["cpa"], "cvr": c["cvr"],
                "aov": c["aov"], "cpm": c["cpm"], "cpmr": c["cpmr"], "octr": c["octr"], "freq": c["freq"],
                "type": c["type"], "hook": c.get("hold"), "r50": c.get("r50"), "purch": int(c["purch"]),
                "rev": round(c["rev"]), "status": statuslabel(c)}
    scal = [c for c in rows if c.get("significant") and c["roas"] and c["roas"] >= s["roas"]
            and c["freq"] < freq_ceiling(c) and (c["purch"] or 0) >= 2]
    scal.sort(key=lambda c: -((c["roas"] or 0) * (c["spend"] ** 0.4)))
    bleed = sorted([c for c in rows if c.get("waste", 0) > 0], key=lambda c: -c["waste"])[:5]
    if not bleed:
        bleed = sorted([c for c in rows if c.get("significant") and c["roas"] and c["roas"] < s["roas"] * 0.8],
                       key=lambda c: c["roas"])[:5]
    fat = [c for c in rows if c.get("significant") and c.get("d_freq") is not None and c["d_freq"] > 10
           and c.get("d_octr") is not None and c["d_octr"] < -8]
    fat.sort(key=lambda c: -((c.get("d_freq") or 0) - (c.get("d_octr") or 0)))
    vids = sorted([c for c in rows if c["type"] == "VIDEO" and c["impr"] >= 1000 and c.get("hold")],
                  key=lambda c: -(c["hold"] or 0))
    fmt = {}
    for f in ("VIDEO", "IMAGE", "CATALOGUE"):
        g = [c for c in rows if c["type"] == f]; spd = sum(c["spend"] for c in g)
        if spd:
            fmt[f] = {"spend": round(spd), "share": round(spd / tot * 100, 1),
                      "roas": round(sum(c["rev"] for c in g) / spd, 2), "purch": int(sum(c["purch"] for c in g))}
    def fatd(c):
        p = c.get("prev") or {}
        d = cd(c); d.update({"d_freq": round(c.get("d_freq") or 0), "d_octr": round(c.get("d_octr") or 0),
                             "freq_prev": p.get("freq"), "octr_prev": p.get("octr")})
        return d
    return {
        "summary": {k: s.get(k) for k in ("spend", "revenue", "purchases", "roas", "cpa", "aov", "cpm",
                                          "cpmr", "cvr", "ctr", "cpc", "cold_roas", "reach", "lc")},
        "prev": {k: prev.get(k) for k in ("spend", "rev", "purch", "roas", "cpa", "aov", "cpm", "cpmr", "cvr", "ctr", "cpc")},
        "delta": {k: (round(s["d_" + k]) if s.get("d_" + k) is not None else None)
                  for k in ("spend", "roas", "cpa", "cpm", "cpmr", "cvr")},
        "audience": segs,
        "contrib": contrib(s, prev) or [],
        "formats": fmt,
        # the full metric set at every level of the account
        "campaigns": group_full(rows, lambda c: c["campaign"], lambda c: c["campaign"], tot),
        "adsets":    group_full(rows, lambda c: c.get("adset_id") or c["adset"], lambda c: c["adset"], tot),
        "ads":       group_full(rows, lambda c: c["ad_id"], lambda c: c["ad_name"], tot),
        "top": [cd(c) for c in sorted([c for c in rows if c.get("significant")], key=lambda c: -c["spend"])[:8]],
        "scalable": [cd(c) for c in scal[:5]],
        "bleeders": [cd(c) for c in bleed],
        "fatiguing": [fatd(c) for c in fat[:5]],
        "videos": [cd(c) for c in vids[:8]],
    }


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
    CARDS = []
    global SEGMAP, ADSEG
    SEGX = ",".join(["ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name"])
    for acct in get_accounts():
        cur_rows = get_insights(acct["id"], last)
        if not cur_rows: continue
        prev_rows = get_insights(acct["id"], prev)
        STATUS = get_ad_statuses(acct["id"])
        # AUDIENCE TRUTH: Meta's own breakdown by audience segment (user_segment_key).
        # New / Engaged / Existing come straight from Meta. Targeting is only a fallback.
        SEGMAP = build_segmap(acct["id"])
        seg7 = get_seg_insights(acct["id"], last, "ad", SEGX)
        ADSEG = segmap_from_rows(seg7)
        sys.stderr.write("[seg] %s: %d ads from Meta breakdown, %d adsets from targeting\n" % (
            acct["name"], len(ADSEG), len(SEGMAP)))
        HIST = get_daily_series(acct["id"])   # 30 days of this account's own normal
        # THE 7 DAY BASELINE. A single day can never kill an ad.
        B7 = {}
        for r in (cur_rows or []):
            m = metric(r)
            if m["ad_id"]: B7[str(m["ad_id"])] = m
        B7_ACC = agg(list(B7.values())) if B7 else None
        A = analyze(acct, cur_rows, prev_rows, STATUS)
        if A["summary"]["spend"] <= 0: continue
        report["accounts"].append(A)
        NUMID = acct["id"].replace("act_", "")
        ch = channel_for(acct["name"]); lch = channel_launch_for(acct["name"])
        ach = channel_advisor_for(acct["name"])
        sm = A["summary"]
        if a.daily or a.dry_run:
            # ---- ONE memo per window, per brand. Nothing else. No duplicate digests, pulses or side channels. ----
            save7 = (DATES["label"], DATES["p_label"])
            A["windows"] = {}
            for win, cw, pw, lab, plab in WINDOWS:
                cr = get_insights(acct["id"], cw)
                if not cr: continue
                pr = get_insights(acct["id"], pw)
                # Meta's audience-segment breakdown for THIS window, and the one before it
                sr = get_seg_insights(acct["id"], cw, "ad", SEGX)
                sp = get_seg_insights(acct["id"], pw, "ad", SEGX)
                if sr: ADSEG = segmap_from_rows(sr)
                AW = analyze(acct, cr, pr, STATUS)
                if AW["summary"]["spend"] <= 0: continue
                AW["segs"] = account_segments(sr)          # exact split, Meta's numbers
                AW["segs_prev"] = account_segments(sp)
                AW["seg_campaigns"] = seg_rollup(sr, "campaign_id", "campaign_name")
                AW["seg_adsets"] = seg_rollup(sr, "adset_id", "adset_name")
                AW["seg_ads"] = seg_rollup(sr, "ad_id", "ad_name")
                AW["seg_campaigns_prev"] = seg_rollup(sp, "campaign_id", "campaign_name")
                AW["seg_adsets_prev"] = seg_rollup(sp, "adset_id", "adset_name")
                AW["seg_ads_prev"] = seg_rollup(sp, "ad_id", "ad_name")
                DATES["label"], DATES["p_label"], DATES["win"] = lab, plab, win
                A["windows"][win] = strat_payload(AW)      # feeds the visual dashboard
                wch = channel_window_for(acct["name"], win)
                if wch:
                    AW["hist"] = HIST
                    AW["b7"] = B7            # each ad's own last 7 days
                    AW["b7_acc"] = B7_ACC    # the account's last 7 days
                    CAP = {"pulse": "*1 · IS THIS NORMAL* — today against this account's own 30 days, and the lever that moved it.",
                           "trend": "*2 · THE TREND* — rolling 7 day. Frequency, CPMR and CPM are never judged on one day.",
                           "audience": "*3 · WHERE THE MONEY WENT* — spend allocation by audience, and whether the mix or the performance moved the account.",
                           "funnel": "*4 · THE FUNNEL* — hook to hold to CTR to ATC to CVR to ROAS, and the first stage that broke.",
                           "blame": "*5 · WHO MOVED THE REVENUE — ADS* — every ad's share of the account's revenue change, in EGP, with the stage that broke and where to go.",
                           "blame_camp": "*6 · WHO MOVED THE REVENUE — CAMPAIGNS* — same, one level up.",
                           "decay": "*7 · IT WAS WORKING, NOW IT IS NOT* — creatives that fell below the account, the metric responsible, and the place to go and look.",
                           "retention": "*8 · WHERE EVERY VIDEO LOSES THEM* — hook rate, the drop-off curve, the biggest cliff, and CPMR.",
                           "verdicts": "*9 · THE VERDICT ON EVERY AD* — scale, iterate, monitor or kill, plus the hit rate and how many creatives to launch.",
                           "scenarios": "*10 · WHAT HAPPENS IF* — spend, CVR, CPM and frequency, each priced in revenue.",
                           "plan": "*11 · DO TODAY / DO THIS WEEK / MONITOR* — every line with its expected impact.",
                           "decide_unused": "*x* — the decision map and the money move.",
                           "campaigns": "*12 · CAMPAIGNS* — every metric, plus revenue Δ in EGP and share of the move.",
                           "adsets": "*13 · AD SETS* — every metric, plus revenue Δ in EGP and share of the move.",
                           "ads": "*14 · ADS* — every metric, plus revenue Δ in EGP and share of the move."}
                    cards = render_cards(AW, win)
                    for i, (suf, png) in enumerate(cards):
                        fn = "%s-%s-%s.png" % (slug(acct["name"]), win, suf)
                        os.makedirs(IMG_DIR, exist_ok=True)
                        with open(os.path.join(IMG_DIR, fn), "wb") as fh: fh.write(png)
                        head = ("%s  *%s — %s*\n" % (MENTION, acct["name"].upper(),
                                                     WIN_TITLE.get(win, "MEMO"))) if i == 0 else ""
                        CARDS.append({"ch": wch, "png": png, "fn": fn,
                                      "title": "%s-%s-%s" % (slug(acct["name"]), win, suf),
                                      "comment": head + CAP.get(suf, ""),
                                      "memo": msg_short(AW, win) if suf == "ads" else None})
            DATES["label"], DATES["p_label"] = save7
            DATES["win"] = "7day"
        # --weekly is retired: the 7day memo already ships every day to the 7day channel.
        time.sleep(1)

    for A in report["accounts"]:
        for c in A["creatives"]:
            c.pop("prev", None)
    save_json(DATA_PATH, report)

    # ---- ship the cards. Try a native upload; if the app lacks files:write, push + link the image. ----
    if CARDS:
        pushed = push_images() if any(c["png"] for c in CARDS) else False
        if pushed: time.sleep(6)   # give raw.githubusercontent a moment to serve the new blobs
        for c in CARDS:
            sent = slack_image(c["ch"], c["png"], c["title"], c["comment"])
            if not sent and c["png"] and pushed:
                sent = slack_image_url(c["ch"], img_url(c["fn"]), c["comment"])
            if not sent:
                slack(c["ch"], c["comment"])   # never leave the channel silent
            time.sleep(1)
            if c.get("memo"):
                slack(c["ch"], c["memo"])
                time.sleep(1)
            time.sleep(1)

    sys.stderr.write("[done] %d accounts, %d cards\n" % (len(report["accounts"]), len(CARDS)))

if __name__ == "__main__":
    main()
