#!/usr/bin/env python3
"""Rank Ourkids products by the revenue they actually earn, and write it back to Shopify.

Shopify can only sort a collection by units ("best-selling"), never by money. On this store
those two orderings barely agree -- a check on 31 Aug 2026 found only 6 of the top 20 products
in common. A EGP 23,000 breast pump (2 units) ranked 51st by units; Panini cards ranked 1st by
units and 55th by revenue.

So: pull the rolling 7-day revenue per product from ShopifyQL and store it on each product as
custom.rev_7d. A smart collection ("Best Earners") is built on that metafield and rebuilds
itself, which is the only native way to surface a revenue ranking -- Shopify has no revenue
sort and Search & Discovery boosts have no API.

Runs on GitHub's servers. Nothing here depends on a laptop being open.

NOTE: this repo is public. Revenue numbers are written to Shopify only and are never committed
or printed in full -- the log shows counts and ranks, not the money column.
"""
import json, os, sys, urllib.request

# The SHOPIFY_STORE secret holds the bare handle ("ourkids1"), not a hostname -- the other
# scripts in this repo append the domain themselves. Do the same or DNS fails.
STORE = os.environ["SHOPIFY_STORE"].strip().replace("https://", "").rstrip("/")
if ".myshopify.com" not in STORE:
    STORE += ".myshopify.com"
TOKEN = os.environ["SHOPIFY_TOKEN"]
VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-01")
URL = f"https://{STORE}/admin/api/{VERSION}/graphql.json"
WINDOW_DAYS = int(os.environ.get("REV_WINDOW_DAYS", "7"))
TOP_N = int(os.environ.get("REV_TOP_N", "250"))


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "X-Shopify-Access-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    if "errors" in out:
        raise SystemExit("GraphQL error: %s" % json.dumps(out["errors"])[:400])
    return out["data"]


def fetch_revenue():
    """ShopifyQL, through the Admin API. The first row comes back with a blank product_id --
    that is the aggregate row, not a product, and must be dropped or it writes a junk metafield."""
    q = (f"FROM sales SHOW total_sales, net_items_sold "
         f"GROUP BY product_id, product_title SINCE -{WINDOW_DAYS}d UNTIL today "
         f"ORDER BY total_sales DESC LIMIT {TOP_N}")
    data = gql("""
      query($q: String!) {
        shopifyqlQuery(query: $q) {
          __typename
          ... on TableResponse { tableData { rowData columns { name } } }
          parseErrors { code message }
        }
      }""", {"q": q})
    res = data["shopifyqlQuery"]
    if res.get("parseErrors"):
        raise SystemExit("ShopifyQL rejected the query: %s" % res["parseErrors"])
    rows = res["tableData"]["rowData"]
    return [r for r in rows if r[0]]


def write_ranks(rows):
    """metafieldsSet takes at most 25 per call, so batch. A failed batch is reported and the
    run continues -- a partial refresh beats none, and the next run overwrites everything."""
    written = failed = 0
    for i in range(0, len(rows), 25):
        batch = rows[i:i + 25]
        metafields = [{
            "ownerId": f"gid://shopify/Product/{r[0]}",
            "namespace": "custom", "key": "rev_7d",
            "type": "number_decimal", "value": str(round(float(r[2]), 2)),
        } for r in batch]
        d = gql("""
          mutation($m: [MetafieldsSetInput!]!) {
            metafieldsSet(metafields: $m) { metafields { id } userErrors { field message } }
          }""", {"m": metafields})
        errs = d["metafieldsSet"]["userErrors"]
        if errs:
            failed += len(batch)
            print("  batch %d failed: %s" % (i // 25 + 1, errs[:2]), file=sys.stderr)
        else:
            written += len(batch)
    return written, failed


def clear_stale(keep_ids):
    """A product that sold last week and not this one keeps a stale rev_7d for ever, so it
    would sit in Best Earners on numbers that are no longer true. Zero anything outside the
    current window."""
    d = gql("""
      { products(first: 250, query: "metafields.custom.rev_7d:>0") {
          edges { node { id } } } }""")
    stale = [e["node"]["id"] for e in d["products"]["edges"]
             if e["node"]["id"].rsplit("/", 1)[-1] not in keep_ids]
    for i in range(0, len(stale), 25):
        gql("""
          mutation($m: [MetafieldsSetInput!]!) {
            metafieldsSet(metafields: $m) { userErrors { message } }
          }""", {"m": [{"ownerId": pid, "namespace": "custom", "key": "rev_7d",
                        "type": "number_decimal", "value": "0"} for pid in stale[i:i + 25]]})
    return len(stale)


def main():
    rows = fetch_revenue()
    if not rows:
        raise SystemExit("No sales rows returned -- refusing to wipe rev_7d on an empty result.")
    written, failed = write_ranks(rows)
    cleared = clear_stale({r[0] for r in rows})
    # deliberately no money in the log: this repo is public
    print(f"{WINDOW_DAYS}-day window | products ranked: {len(rows)} | "
          f"written: {written} | failed: {failed} | zeroed as stale: {cleared}")
    print("top 10 by revenue:")
    for i, r in enumerate(rows[:10], 1):
        print(f"  {i:2d}. {r[1][:64]}  ({r[3]} units)")
    if failed:
        raise SystemExit("some batches failed -- see above")


if __name__ == "__main__":
    main()
