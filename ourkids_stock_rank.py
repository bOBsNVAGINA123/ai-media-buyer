#!/usr/bin/env python3
"""
Push picked-over products to the bottom of their collection, store-wide.

Why this exists
---------------
Shopify's `available` is binary. A product with one size left and a product with
every size in stock are both "available", so a nearly-sold-out item can sit at the
top of a collection purely on best-seller history. Shopify offers no sort by stock
depth, and a theme can only reorder the page it is currently rendering.

Every Ourkids collection is automated (rule-based) AND carries sortOrder MANUAL,
which means `collectionReorderProducts` is accepted on them -- verified against the
live store. That is the only lever that reorders a WHOLE collection rather than one
page of it.

What it does
------------
For each configured collection: read every product with its variant availability,
sort into three bands, and move everything below the top band to the tail while
preserving the existing order inside each band. Nothing is added, removed, hidden or
repriced -- only position changes.

  band 1  fully shoppable   single-variant in stock, or >=3 variants available,
                            or at least half the variants available
  band 2  picked over       in stock but most sizes gone
  band 3  sold out          nothing available

Safety
------
* Read-only apart from `collectionReorderProducts`.
* DRY_RUN=1 prints the plan and writes nothing.
* MAX_MOVES caps how many products a single run will reposition per collection, so a
  bad config can never rewrite a 14,000-product collection in one go.
* Collections larger than MAX_PRODUCTS are skipped outright and named in the log.
"""
import os, sys, json, time, urllib.request, urllib.error

STORE   = os.environ.get("SHOPIFY_STORE", "").strip()
TOKEN   = os.environ.get("SHOPIFY_TOKEN", "").strip()
VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10").strip() or "2024-10"
DRY_RUN = os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False")
MAX_MOVES    = int(os.environ.get("MAX_MOVES", "250"))
MAX_PRODUCTS = int(os.environ.get("MAX_PRODUCTS", "1500"))

# The collections shoppers and ads actually land on (GA4, Egypt, 30d). Override with
# COLLECTION_HANDLES="a,b,c" to run a different set.
DEFAULT_HANDLES = [
    "dorganize-bz", "dorganize-lunch-boxs", "strollers-1", "all-school", "b1g1",
    "smiggle", "school-bags", "lunch-bags-box", "all-baby-essentials", "flasks",
    "all-clothing", "school-shoes-1", "potty-training", "all-footwear",
]
HANDLES = [h.strip() for h in os.environ.get("COLLECTION_HANDLES", "").split(",") if h.strip()] or DEFAULT_HANDLES

if not STORE or not TOKEN:
    sys.exit("SHOPIFY_STORE and SHOPIFY_TOKEN are required")
if ".myshopify.com" not in STORE:
    STORE += ".myshopify.com"          # the secret is stored as the bare handle
ENDPOINT = "https://%s/admin/api/%s/graphql.json" % (STORE, VERSION)


def gql(query, variables=None, tries=6):
    """POST a GraphQL document, retrying on Shopify's throttle and 5xx."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(tries):
        req = urllib.request.Request(ENDPOINT, data=body, headers={
            "X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                payload = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** attempt); continue
            raise
        errs = payload.get("errors")
        if errs:
            throttled = any("THROTTLED" in json.dumps(x) for x in errs)
            if throttled and attempt < tries - 1:
                time.sleep(2 ** attempt); continue
            raise RuntimeError(json.dumps(errs)[:400])
        # stay well clear of the cost ceiling
        cost = (payload.get("extensions") or {}).get("cost") or {}
        left = ((cost.get("throttleStatus") or {}).get("currentlyAvailable") or 1000)
        if left < 300:
            time.sleep(2.0)
        return payload["data"]
    raise RuntimeError("giving up after %d attempts" % tries)


COLLECTION_Q = """
query($q: String!) {
  collections(first: 20, query: $q) {
    nodes { id handle title sortOrder productsCount { count } }
  }
}"""

LOCATIONS_Q = """
query { locations(first: 50) { nodes { id name fulfillsOnlineOrders } } }"""

# Per-location stock, because `availableForSale` counts branches that never ship an
# online order. Heavier query, so the page size drops to 50 to stay inside the cost
# ceiling -- the throttle handling in gql() covers the rest.
# sellableOnlineQuantity is Shopify's OWN answer to "how many of these can a website
# order actually take" -- it already excludes branches that do not fulfil online
# orders. Verified against a hand count: M-Design 600ml Blue reads inventoryQuantity 8
# / sellable 0, M Design 1.1 Blue 9 / 0, M-Design 600 Purple 30 / 23 -- all three match
# summing the fulfilling locations by hand. Using it instead of walking
# inventoryLevels keeps the query under Shopify's 1000-point cost ceiling.
PRODUCTS_Q = """
query($id: ID!, $cursor: String) {
  collection(id: $id) {
    products(first: 30, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        variantsCount { count }
        variants(first: 25) { nodes { sellableOnlineQuantity availableForSale } }
      }
    }
  }
}"""

REORDER_M = """
mutation($id: ID!, $moves: [MoveInput!]!) {
  collectionReorderProducts(id: $id, moves: $moves) {
    job { id }
    userErrors { field message }
  }
}"""


def band(total_variants, available_variants):
    """1 = fully shoppable, 2 = picked over, 3 = nothing shippable.

    `available_variants` counts variants with stock at a location that fulfils
    online orders -- not Shopify's availableForSale, which includes branches that
    never ship."""
    if available_variants == 0:
        return 3
    if total_variants <= 1:
        return 1
    if available_variants >= 3:
        return 1
    if available_variants * 2 >= total_variants:
        return 1
    return 2


def collection_products(cid):
    """Every product in the collection, in its CURRENT manual order."""
    out, cursor = [], None
    while True:
        data = gql(PRODUCTS_Q, {"id": cid, "cursor": cursor})
        block = data["collection"]["products"]
        for n in block["nodes"]:
            variants = n["variants"]["nodes"]
            total = n["variantsCount"]["count"] or len(variants)
            shippable = sum(1 for v in variants
                            if (v.get("sellableOnlineQuantity") or 0) > 0)
            out.append((n["id"], band(total, shippable), total, shippable))
        if not block["pageInfo"]["hasNextPage"]:
            return out
        cursor = block["pageInfo"]["endCursor"]


def rank_collection(node):
    cid, handle, count = node["id"], node["handle"], node["productsCount"]["count"]
    if node["sortOrder"] != "MANUAL":
        print("  SKIP %-26s sortOrder is %s, not MANUAL -- reorder would be rejected"
              % (handle, node["sortOrder"]))
        return 0
    if count > MAX_PRODUCTS:
        print("  SKIP %-26s %d products exceeds MAX_PRODUCTS=%d" % (handle, count, MAX_PRODUCTS))
        return 0

    items = collection_products(cid)
    total = len(items)
    bands = [b for _, b, _, _ in items]
    demote = [p for p in items if p[1] != 1]
    if not demote:
        print("  OK   %-26s %d products, nothing to demote" % (handle, total))
        return 0
    # already in band order? then leave it completely alone
    if bands == sorted(bands):
        print("  OK   %-26s %d products, already ordered (%d below top band)"
              % (handle, total, len(demote)))
        return 0

    demote = sorted(demote, key=lambda p: p[1])          # band 2 then band 3
    if len(demote) > MAX_MOVES:
        print("  NOTE %-26s capping moves at %d of %d" % (handle, MAX_MOVES, len(demote)))
        demote = demote[:MAX_MOVES]

    start = total - len(demote)
    moves = [{"id": pid, "newPosition": str(start + i)} for i, (pid, _, _, _) in enumerate(demote)]
    thin = sum(1 for p in demote if p[1] == 2)
    gone = sum(1 for p in demote if p[1] == 3)
    print("  MOVE %-26s %d products | %d picked-over + %d sold-out -> tail"
          % (handle, total, thin, gone))
    if DRY_RUN:
        return len(moves)

    res = gql(REORDER_M, {"id": cid, "moves": moves})["collectionReorderProducts"]
    if res["userErrors"]:
        print("       REJECTED: %s" % json.dumps(res["userErrors"])[:200])
        return 0
    time.sleep(1.5)                                       # let the job settle
    return len(moves)


SHIPPING_LOCATIONS = set()


def load_locations():
    """Only locations that fulfil online orders can serve a website order.

    `fulfillsOnlineOrders` gates order ROUTING, not availability -- Shopify happily
    counts a branch that never ships toward availableForSale, so a product can sell
    online with nothing at a shippable location (measured: 63% of units in a Lunch
    Bags sample sat at non-fulfilling branches, and 4 of 12 products were buyable
    with zero shippable units). Ranking on availableForSale would therefore promote
    stock that cannot be posted.
    """
    nodes = gql(LOCATIONS_Q)["locations"]["nodes"]
    ships = [n for n in nodes if n["fulfillsOnlineOrders"]]
    SHIPPING_LOCATIONS.update(n["id"] for n in ships)
    print("shippable locations: %s" % ", ".join(sorted(n["name"] for n in ships)))
    print("ignored (no online fulfilment): %s"
          % ", ".join(sorted(n["name"] for n in nodes if not n["fulfillsOnlineOrders"])))


def main():
    print("Ourkids stock-depth merchandising%s" % ("  [DRY RUN]" if DRY_RUN else ""))
    print("store=%s  collections=%d  max_moves=%d" % (STORE, len(HANDLES), MAX_MOVES))
    load_locations()
    q = " OR ".join("handle:%s" % h for h in HANDLES)
    found = gql(COLLECTION_Q, {"q": q})["collections"]["nodes"]
    missing = sorted(set(HANDLES) - {n["handle"] for n in found})
    if missing:
        print("not found: %s" % ", ".join(missing))
    moved = 0
    for node in found:
        try:
            moved += rank_collection(node)
        except Exception as e:                            # one bad collection must not kill the run
            print("  FAIL %-26s %s" % (node["handle"], str(e)[:160]))
    print("done: %d products repositioned across %d collections" % (moved, len(found)))


if __name__ == "__main__":
    main()
