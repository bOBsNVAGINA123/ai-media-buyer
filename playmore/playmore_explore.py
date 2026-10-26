"""Read-only survey round 5: value of returned goods by month (Shopify orders), delivery status by month. Totals only."""
import os, xmlrpc.client, datetime, collections
U = "https://isi-play-more.odoo.com"; DB = "isi-c-play-more-production-12131321"
L, K = os.environ["LOGIN"], os.environ["KEY"]
uid = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/common").authenticate(DB, L, K, {})
M = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/object", allow_none=True)
x = lambda model, method, *a, **kw: M.execute_kw(DB, uid, K, model, method, list(a), kw)
mv = x("stock.move", "search_read", [["picking_id.picking_type_code", "=", "incoming"], ["picking_id.return_id", "!=", False], ["state", "=", "done"], ["date", ">=", "2025-07-01"]],
       fields=["date", "product_uom_qty", "quantity", "sale_line_id", "origin_returned_move_id", "picking_id", "product_id"], limit=20000)
print("returned move lines:", len(mv))
sl = list({m["sale_line_id"][0] for m in mv if m.get("sale_line_id")})
print("with a sale line:", sum(1 for m in mv if m.get("sale_line_id")))
# fall back to the outgoing move's sale line
om = list({m["origin_returned_move_id"][0] for m in mv if not m.get("sale_line_id") and m.get("origin_returned_move_id")})
omap = {}
for i in range(0, len(om), 1000):
    for o in x("stock.move", "read", om[i:i + 1000], fields=["sale_line_id"]):
        if o.get("sale_line_id"): omap[o["id"]] = o["sale_line_id"][0]
sl = list(set(sl) | set(omap.values()))
L2 = {}
for i in range(0, len(sl), 1000):
    for l in x("sale.order.line", "read", sl[i:i + 1000], fields=["price_total", "price_subtotal", "product_uom_qty", "margin", "order_id"]):
        L2[l["id"]] = l
oids = list({l["order_id"][0] for l in L2.values()})
team = {}
for i in range(0, len(oids), 1000):
    for o in x("sale.order", "read", oids[i:i + 1000], fields=["team_id", "partner_id"]):
        team[o["id"]] = ((o.get("team_id") or [0, "?"])[1], (o.get("partner_id") or [0])[0])
agg = collections.defaultdict(lambda: collections.defaultdict(float)); orders = collections.defaultdict(set); nolink = collections.Counter()
for m in mv:
    sid = (m.get("sale_line_id") or [None])[0] or omap.get((m.get("origin_returned_move_id") or [None])[0])
    mon = m["date"][:7]
    if not sid or sid not in L2: nolink[mon] += 1; continue
    l = L2[sid]; q = m.get("quantity") or m.get("product_uom_qty") or 0; uq = l["product_uom_qty"] or 1
    t, partner = team.get(l["order_id"][0], ("?", 0))
    key = "OurKids (intercompany)" if partner == 920 else t
    a = agg[(mon, key)]; a["value_incl"] += l["price_total"] / uq * q; a["value_ex"] += l["price_subtotal"] / uq * q; a["margin"] += (l["margin"] or 0) / uq * q; a["qty"] += q
    orders[(mon, key)].add(l["order_id"][0])
print("month | channel | returned orders | qty | value incl VAT | value ex VAT | margin lost")
for (mon, key), a in sorted(agg.items()):
    print(f"  {mon} | {key} | {len(orders[(mon, key)])} | {a['qty']:.0f} | {a['value_incl']:.0f} | {a['value_ex']:.0f} | {a['margin']:.0f}")
print("returned lines with no sale link, by month:", dict(nolink))
print("== Shopify orders by month and delivery_status")
g = x("sale.order", "read_group", [["team_id.name", "=", "Shopify"], ["state", "=", "sale"], ["date_order", ">=", "2026-03-01 00:00:00"]], ["amount_total:sum"], ["date_order:month", "delivery_status"], lazy=False)
for r in g: print("  ", r["date_order:month"], r.get("delivery_status"), r["__count"], round(r["amount_total"]))
