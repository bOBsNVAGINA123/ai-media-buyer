#!/usr/bin/env python3
"""
Push Our Kids in-store (POS) purchases into Google Ads as offline conversions.

Runs in GitHub Actions off the same secrets ourkids_live.py already uses.
Odoo POS -> aggregate to orders -> hash the customer identifier -> ingest against
the per-branch conversion action.

Uses the Data Manager API (datamanager.googleapis.com). The older
ConversionUploadService.UploadClickConversions now rejects new integrations
outright - every row comes back "New integrations for uploading click
conversions should use the Data Manager API".

Branch rides in the conversion action name ("In-store Purchase - Smouha"),
because the upload schema has no store-code field. Same shape as the Meta side,
where the 15 custom conversions split on custom_data.content_category.

Env:
  ODOO_SERVER ODOO_DB ODOO_LOGIN ODOO_APIKEY
  GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REFRESH_TOKEN
  GOOGLE_DEVELOPER_TOKEN GOOGLE_LOGIN_CID GOOGLE_CUSTOMER_ID
  DAYS          how far back to pull (default 7; 90 for a backfill)
  VALIDATE_ONLY "1" to let Google check the payload without recording anything

NOTE: the refresh token must carry BOTH scopes:
  https://www.googleapis.com/auth/adwords          (to read conversion actions)
  https://www.googleapis.com/auth/datamanager      (to ingest events)
A token minted for adwords alone will 401/403 on the ingest call.
"""
import os, sys, json, time, hashlib, datetime, re
import urllib.request, urllib.parse, urllib.error

GADS_HOST = "https://googleads.googleapis.com"
DM_INGEST = "https://datamanager.googleapis.com/v1/events:ingest"
_VER = {"v": ""}
DAYS = int(os.environ.get("DAYS", "7"))
VALIDATE_ONLY = os.environ.get("VALIDATE_ONLY", "").strip() in ("1", "true", "yes")
BATCH = 2000
ODOO_PAGE = 5000

BRANCHES = [
    ("newcairo", "New Cairo"), ("new  cairo", "New Cairo"), ("new cairo", "New Cairo"),
    ("moa", "Mall of Arabia"), ("mall of arabia", "Mall of Arabia"),
    ("nasrcity", "Nasr City"), ("nasr city", "Nasr City"),
    ("dokki", "Dokki"), ("smouha", "Smouha"),
    ("october", "October"), ("zayed", "Zayed"),
]


def log(*a):
    sys.stderr.write("[gads-offline] " + " ".join(str(x) for x in a) + "\n")
    sys.stderr.flush()


def env(k):
    v = os.environ.get(k, "").strip()
    if not v:
        sys.exit("missing env: " + k)
    return v


def digits(s):
    return re.sub(r"\D", "", s or "")


# --------------------------------------------------------------------- Odoo
def odoo_rpc(service, method, args):
    payload = {"jsonrpc": "2.0", "method": "call",
               "params": {"service": service, "method": method, "args": args}, "id": 1}
    req = urllib.request.Request(env("ODOO_SERVER").rstrip("/") + "/jsonrpc",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise SystemExit("odoo: " + json.dumps(out["error"])[:400])
    return out["result"]


_uid = None


def odoo(model, method, params, kwargs=None):
    global _uid
    db, login, key = env("ODOO_DB"), env("ODOO_LOGIN"), env("ODOO_APIKEY")
    if _uid is None:
        _uid = odoo_rpc("common", "authenticate", [db, login, key, {}])
    return odoo_rpc("object", "execute_kw",
                    [db, _uid, key, model, method, params, kwargs or {}])


# --------------------------------------------------------- normalise + hash
def sha(s):
    return hashlib.sha256(s.encode()).hexdigest() if s else ""


def norm_email(e):
    e = (e or "").strip().lower()
    return e if "@" in e else ""


def norm_phone(p):
    d = digits(str(p or ""))
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("20") and len(d) == 12:
        return "+" + d
    if d.startswith("0") and len(d) == 11:
        return "+20" + d[1:]
    if len(d) == 10 and d.startswith("1"):
        return "+20" + d
    return ""


def branch_of(ref):
    s = re.sub(r"\(\d+\)", " ", str(ref)).lower().replace("retail", " ")
    s = re.sub(r"\s+", " ", s).strip()
    if "refund" in s:
        return None
    for key, name in BRANCHES:
        if key in s:
            return name
    return None


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ------------------------------------------------------------------- Google
def access_token():
    body = urllib.parse.urlencode({
        "client_id": env("GOOGLE_CLIENT_ID"),
        "client_secret": env("GOOGLE_CLIENT_SECRET"),
        "refresh_token": env("GOOGLE_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    with urllib.request.urlopen(req, timeout=60) as r:
        tok = json.load(r)
    scopes = tok.get("scope", "")
    log("token scopes:", scopes or "(not reported)")
    if scopes and "datamanager" not in scopes:
        log("WARNING: refresh token has no datamanager scope - ingest will fail. "
            "Re-mint it with adwords + datamanager.")
    return tok["access_token"]


def _gads_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "developer-token": env("GOOGLE_DEVELOPER_TOKEN"),
        "login-customer-id": digits(env("GOOGLE_LOGIN_CID")),
        "Content-Type": "application/json",
    }


def probe_version(token):
    """Google sunsets API versions without warning - v21 died mid-Aug 2026."""
    if _VER["v"]:
        return _VER["v"]
    cid = digits(env("GOOGLE_CUSTOMER_ID"))
    for v in ("v22", "v23", "v24", "v25", "v21"):
        req = urllib.request.Request(
            f"{GADS_HOST}/{v}/customers/{cid}/googleAds:search",
            data=json.dumps({"query": "SELECT customer.id FROM customer LIMIT 1"}).encode(),
            headers=_gads_headers(token))
        try:
            with urllib.request.urlopen(req, timeout=60):
                _VER["v"] = v
                log("api version probe picked", v)
                return v
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if e.code == 404 or "UNSUPPORTED_VERSION" in body:
                continue
            raise SystemExit("google %s on %s: %s" % (e.code, v, body[:1200]))
        except Exception:
            continue
    raise SystemExit("no live Google Ads API version found among v21-v25")


def conversion_action_ids(token):
    """branch conversion action name -> numeric id (Data Manager wants the id)."""
    cid = digits(env("GOOGLE_CUSTOMER_ID"))
    ver = probe_version(token)
    q = ("SELECT conversion_action.id, conversion_action.name "
         "FROM conversion_action "
         "WHERE conversion_action.name LIKE 'In-store Purchase%'")
    req = urllib.request.Request(
        f"{GADS_HOST}/{ver}/customers/{cid}/googleAds:search",
        data=json.dumps({"query": q}).encode(),
        headers=_gads_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit("gaql %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:1200]))
    out = {}
    for row in res.get("results", []):
        ca = row["conversionAction"]
        out[ca["name"]] = str(ca["id"])
    return out


def ingest(destinations, events, token):
    payload = {
        "destinations": destinations,
        "events": events,
        "encoding": "HEX",
        "validateOnly": VALIDATE_ONLY,
    }
    req = urllib.request.Request(DM_INGEST, data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit("datamanager %s: %s"
                         % (e.code, e.read().decode("utf-8", "ignore")[:1800]))


# --------------------------------------------------------------------- main
def main():
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    log("window since", since, "| validate_only", VALIDATE_ONLY)

    orders, offset = {}, 0
    while True:
        batch = odoo("pos.order.line", "search_read", [[["create_date", ">=", since]]],
                     {"fields": ["order_id", "order_partner_id",
                                 "price_subtotal_incl", "create_date"],
                      "limit": ODOO_PAGE, "offset": offset, "order": "id"})
        if not batch:
            break
        for r in batch:
            oid = r.get("order_id")
            if not oid:
                continue
            k = oid[0]
            o = orders.get(k)
            if o is None:
                p = r.get("order_partner_id")
                o = orders[k] = {"ref": oid[1], "pid": p[0] if p else None,
                                 "t": r["create_date"], "v": 0.0}
            o["v"] += r.get("price_subtotal_incl") or 0.0
        offset += len(batch)
    log("orders", len(orders))

    pids = sorted({o["pid"] for o in orders.values() if o["pid"]})
    contacts = {}
    for i in range(0, len(pids), 2000):
        for p in odoo("res.partner", "read", [pids[i:i + 2000]],
                      {"fields": ["email", "phone", "mobile"]}):
            contacts[p["id"]] = p
    log("customers", len(contacts))

    token = access_token()
    actions = conversion_action_ids(token)
    log("conversion actions found", len(actions))
    if not actions:
        sys.exit("no 'In-store Purchase - *' conversion actions in the account")

    operating = {"product": "GOOGLE_ADS", "accountId": digits(env("GOOGLE_CUSTOMER_ID"))}
    login = {"product": "GOOGLE_ADS", "accountId": digits(env("GOOGLE_LOGIN_CID"))}
    destinations, dest_ref = [], {}
    for name, aid in actions.items():
        ref = slug(name)
        dest_ref[name] = ref
        destinations.append({
            "reference": ref,
            "loginAccount": login,
            "operatingAccount": operating,
            "productDestinationId": aid,
        })

    events, no_branch, no_contact, no_action = [], 0, 0, 0
    for o in orders.values():
        br = branch_of(o["ref"])
        if not br:
            no_branch += 1
            continue
        if o["v"] <= 0:
            continue
        name = "In-store Purchase - " + br
        if name not in dest_ref:
            no_action += 1
            continue
        c = contacts.get(o["pid"]) or {}
        em = norm_email(c.get("email"))
        ph = norm_phone(c.get("mobile") or c.get("phone"))
        ids = []
        if em:
            ids.append({"emailAddress": sha(em)})
        if ph:
            ids.append({"phoneNumber": sha(ph)})
        if not ids:
            no_contact += 1
            continue
        events.append({
            "destinationReferences": [dest_ref[name]],
            "transactionId": str(o["ref"]),
            "eventTimestamp": o["t"].replace(" ", "T") + "Z",
            "eventSource": "IN_STORE",      # POS purchase, not web or app
            "currency": "EGP",
            "conversionValue": round(o["v"], 2),
            "userData": {"userIdentifiers": ids},
        })

    log("uploadable", len(events), "| dropped: refund/no-branch", no_branch,
        "no-contact", no_contact, "no-action", no_action)
    if not events:
        log("nothing to send")
        return

    sent = failed = 0
    for i in range(0, len(events), BATCH):
        chunk = events[i:i + BATCH]
        res = ingest(destinations, chunk, token)
        # Data Manager reports per-request counts rather than per-row rejects.
        bad = 0
        for k in ("errors", "failures", "eventErrors"):
            if res.get(k):
                bad = len(res[k])
                log("errors in batch:", json.dumps(res[k])[:600])
        sent += len(chunk) - bad
        failed += bad
        log("batch %d-%d: sent %d, errors %d | resp %s"
            % (i, i + len(chunk), len(chunk) - bad, bad, json.dumps(res)[:300]))
        time.sleep(1)

    log("TOTAL sent", sent, "errors", failed)
    if VALIDATE_ONLY:
        log("validate-only run: nothing was actually recorded")


if __name__ == "__main__":
    main()
