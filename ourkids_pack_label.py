#!/usr/bin/env python3
"""
Tag every product whose title says it is a pack of two with the `Pack of 2` tag.

Why this exists
---------------
The pack size lives only in the product TITLE, which nothing can filter, rank or
badge on. 510 products say "Pack of 2" / "2-Pack" / "Set of 2" / "Twin Pack" in
their title and 15 of them carried the tag -- so a shopper could not find the
multipacks, no automated collection could gather them, and the theme had nothing
to render a badge from.

Tagging is the fix because a tag is the one product field that automated
collections, search filters, the theme and the Meta/Google feeds all read.

What it does
------------
Dumps every product (bulk operation -- 40k products, one request), matches the
title against the pack-of-two patterns, and adds the tag to whatever is missing
it. Runs daily, so anything merchandising uploads tomorrow is labelled by itself.

Deliberately NOT matched, because the titles are ambiguous and a wrong label is
worse than none:
  "2 Pcs" / "2 Pieces"  (178 products) -- a 2-piece cutlery SET is not a pack of
                        two of the same thing. Set INCLUDE_PIECES=1 to add them.
  "2x" / "x2"           -- mostly dimensions ("2 x 24 Pieces", "1.25 H x 2.5 W").
  "Duo"                 -- product names (Sistema Snack Attack Duo).

Safety
------
* The only write is `tagsAdd`. Nothing is removed, renamed, hidden or repriced.
* DRY_RUN=1 prints the plan and writes nothing.
* MAX_TAGS caps one run, so a bad regex cannot relabel the whole catalogue.
* A run whose every write is rejected exits non-zero -- a missing API scope must
  not look identical to "there was nothing to do".
"""
import os, sys, json, re, time, urllib.request, urllib.error

STORE   = os.environ.get("SHOPIFY_STORE", "").strip()
TOKEN   = os.environ.get("SHOPIFY_TOKEN", "").strip()
VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10").strip() or "2024-10"
DRY_RUN = os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False")
MAX_TAGS = int(os.environ.get("MAX_TAGS", "1200"))
INCLUDE_PIECES = os.environ.get("INCLUDE_PIECES", "").strip() not in ("", "0", "false", "False")
TAG = os.environ.get("PACK_TAG", "Pack of 2").strip() or "Pack of 2"

# en/em dashes appear in these titles ("2 – Pack"), so the separator class is explicit.
# (?<!\d) stops "12 Pack" and "32-Pack" matching on their trailing 2.
# \b after the 2 stops "Pack of 20" and "Pack of 24" matching on their leading 2.
PACK2 = re.compile(r"(?i)(?:"
                   r"pack\s*of\s*(?:2|two)\b"
                   r"|(?<!\d)2\s*[-–—]?\s*pack\b"
                   r"|\bset\s*of\s*(?:2|two)\b"
                   r"|\btwin\s*pack\b"
                   r")")
PIECES2 = re.compile(r"(?i)(?<!\d)2\s*(?:pcs?|pieces?)\b")


def is_pack_of_two(title):
    if PACK2.search(title):
        return True
    return bool(INCLUDE_PIECES and PIECES2.search(title))


def selftest():
    """The regex is the whole job, so it gets the only test."""
    yes = ["Junior Baby Towels - Pack Of 2",
           "Dr Browns Prevent Soother 6-18 Months, Pack of 2",
           "BIBS Color Baby Pacifier 2-Pack Latex Size 2",
           "BIBS Infinity Pacifier 2 PACK Anatomical Silicone",
           "2 Pack – BIBS Pacifier Studio Colour Latex",
           "Animal Prints Cotton Bibs – Set of 2",
           "Philips Avent Natural Bottle 260ML Twin Pack",
           "Unisex Baby Cotton Long-Sleeved Ribbed Thermal Set – Grey (Pack Of 2)"]
    no  = ["Dr. Brown's Teats (Pack of 3)",
           "Huggies Wipes Pack of 20",
           "Pampers Pack of 24 Nappies",
           "Sistema 12 Pack Food Containers",
           "Smiggle Backpack Blue",
           "Washable Liquid School Glue [Set of 3]",
           "Lisciani Minnie Double-Face Puzzle & 2 x 24 Pieces",
           "Magic Sand Set – 2 x 250g",
           "Chicco Perfect-5 Silicone Slow Flow Teat - 2 Pieces",   # excluded by default
           "Sistema Snack Attack Duo Food Container 975ml"]
    for t in yes:
        assert is_pack_of_two(t), "should match: %s" % t
    for t in no:
        assert not is_pack_of_two(t), "should NOT match: %s" % t
    # the pieces bucket is opt-in, and opting in must actually turn it on
    global INCLUDE_PIECES
    was, INCLUDE_PIECES = INCLUDE_PIECES, True
    assert is_pack_of_two("Chicco Well Being Adjustable Rubber Teat Nipple – 2 Pcs")
    assert not is_pack_of_two("Paw Patrol 12 Pieces Cutlery Set")
    INCLUDE_PIECES = was
    print("selftest OK (%d positive, %d negative)" % (len(yes), len(no)))


if not STORE or not TOKEN:
    if "--selftest" in sys.argv:
        selftest(); sys.exit(0)
    sys.exit("SHOPIFY_STORE and SHOPIFY_TOKEN are required")
if ".myshopify.com" not in STORE:
    STORE += ".myshopify.com"          # the secret is stored as the bare handle
ENDPOINT = "https://%s/admin/api/%s/graphql.json" % (STORE, VERSION)

WRITE_DENIED = []          # every write rejected by Shopify, so the run can fail loudly


def gql(query, variables=None, tries=6):
    """POST a GraphQL document, retrying on Shopify's throttle and 5xx."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(tries):
        req = urllib.request.Request(ENDPOINT, data=body, headers={
            "X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                payload = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** attempt); continue
            raise
        errs = payload.get("errors")
        if errs:
            if any("THROTTLED" in json.dumps(x) for x in errs) and attempt < tries - 1:
                time.sleep(2 ** attempt); continue
            raise RuntimeError(json.dumps(errs)[:400])
        cost = (payload.get("extensions") or {}).get("cost") or {}
        if ((cost.get("throttleStatus") or {}).get("currentlyAvailable") or 1000) < 300:
            time.sleep(2.0)
        return payload["data"]
    raise RuntimeError("giving up after %d attempts" % tries)


def show_token_scopes():
    """Ask Shopify what this token can actually do.

    A scope ticked in the admin UI does not necessarily reach an already-issued
    token, and this store has more than one custom app -- so "the admin says
    write_products" is not evidence that THIS token has it.
    """
    url = "https://%s/admin/oauth/access_scopes.json" % STORE
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            scopes = sorted(h["handle"] for h in json.loads(r.read().decode())["access_scopes"])
    except Exception as e:
        print("could not read token scopes: %s" % str(e)[:120]); return
    print("TOKEN write scopes: %s" % (", ".join(s for s in scopes if s.startswith("write_")) or "NONE"))
    if "write_products" not in scopes:
        print("--> this token does NOT carry write_products; tagsAdd will be rejected.")


BULK_M = """
mutation {
  bulkOperationRunQuery(query: "{ products { edges { node { id title tags } } } }") {
    bulkOperation { id status }
    userErrors { field message }
  }
}"""

BULK_Q = """
query($id: ID!) {
  node(id: $id) { ... on BulkOperation { status objectCount url errorCode } }
}"""

TAGS_M = "mutation($id: ID!, $tags: [String!]!) { tagsAdd(id: $id, tags: $tags) { userErrors { field message } } }"


def every_product():
    """One bulk operation beats 160 paginated pages for a 40k-product catalogue."""
    res = gql(BULK_M)["bulkOperationRunQuery"]
    if res["userErrors"]:
        raise RuntimeError(json.dumps(res["userErrors"])[:300])
    bid = res["bulkOperation"]["id"]
    print("bulk op %s running..." % bid.rsplit("/", 1)[-1])
    for _ in range(120):                                   # 120 x 10s = 20 min ceiling
        time.sleep(10)
        node = gql(BULK_Q, {"id": bid})["node"]
        if node["status"] == "COMPLETED":
            break
        if node["status"] in ("FAILED", "CANCELED", "EXPIRED"):
            raise RuntimeError("bulk op %s (%s)" % (node["status"], node.get("errorCode")))
    else:
        raise RuntimeError("bulk op still running after 20 minutes")
    print("bulk op done: %s objects" % node["objectCount"])
    if not node["url"]:                                    # an empty catalogue has no file
        return []
    with urllib.request.urlopen(node["url"], timeout=300) as r:
        raw = r.read().decode("utf-8")
    out = []
    for line in raw.splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("id", "").startswith("gid://shopify/Product/"):
                out.append(rec)
    return out


def main():
    selftest()                     # never tag anything with an unproven regex
    show_token_scopes()
    products = every_product()
    print("catalogue: %d products" % len(products))

    matched = [p for p in products if is_pack_of_two(p["title"])]
    need    = [p for p in matched if TAG not in (p.get("tags") or [])]
    print("title says pack of two: %d   already tagged: %d   to tag: %d"
          % (len(matched), len(matched) - len(need), len(need)))
    if not need:
        print("nothing to do"); return

    if len(need) > MAX_TAGS:
        print("capped at MAX_TAGS=%d (%d matched) -- the rest go on the next run"
              % (MAX_TAGS, len(need)))
        need = need[:MAX_TAGS]

    for p in need[:15]:
        print("   + %s" % p["title"][:88])
    if len(need) > 15:
        print("   ... and %d more" % (len(need) - 15))

    if DRY_RUN:
        print("DRY_RUN: nothing written"); return

    done = 0
    for p in need:
        try:
            res = gql(TAGS_M, {"id": p["id"], "tags": [TAG]})["tagsAdd"]
            if res["userErrors"]:
                print("   REJECTED %s: %s" % (p["id"].rsplit("/", 1)[-1],
                                              json.dumps(res["userErrors"])[:160]))
                WRITE_DENIED.append("tagsAdd")
                if len(WRITE_DENIED) >= 5:                 # it is the token, not the product
                    break
                continue
            done += 1
            if done % 50 == 0:
                print("   tagged %d/%d" % (done, len(need)))
        except Exception as e:
            print("   FAILED %s: %s" % (p["id"].rsplit("/", 1)[-1], str(e)[:160]))
            if "Access denied" in str(e) or "access scope" in str(e):
                WRITE_DENIED.append("tagsAdd")
                if len(WRITE_DENIED) >= 5:
                    break
    print("done: tagged %d products with %r" % (done, TAG))

    # A scheduled job that changes nothing must NOT report success, or a missing
    # scope goes green every night while the catalogue stays unlabelled.
    if WRITE_DENIED and done == 0:
        sys.exit("FAILED: every tagsAdd was rejected. The token behind SHOPIFY_TOKEN "
                 "needs the write_products scope. Nothing was changed.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
