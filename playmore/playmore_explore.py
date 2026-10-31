"""Read-only: where Shopify product ids live in Playmore's Odoo (for admin links)."""
import os, xmlrpc.client
U = "https://isi-play-more.odoo.com"; DB = "isi-c-play-more-production-12131321"
L, K = os.environ["LOGIN"], os.environ["KEY"]
uid = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/common").authenticate(DB, L, K, {})
M = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/object", allow_none=True)
def x(model, method, *a, **kw):
    try: return M.execute_kw(DB, uid, K, model, method, list(a), kw)
    except Exception as e: return "ERR " + str(e)[-140:]
for model in ("product.template", "product.product", "sale.order.line", "sale.order", "shopify.product.image.ept", "shopify.instance.ept"):
    f = x(model, "fields_get", attributes=["string", "type", "relation"])
    if isinstance(f, dict):
        print(model, {k: (v["type"], v.get("relation")) for k, v in f.items() if "shopify" in k.lower() or "shopify" in (v.get("string") or "").lower()})
print("shopify tmpl count (sudo-less):", x("shopify.product.template.ept", "search_count", [["active", "in", [True, False]]]))
print("image ept with template:", x("shopify.product.image.ept", "search_read", [["shopify_template_id", "!=", False]], fields=["shopify_template_id", "shopify_image_id"], limit=3))
print("instance:", x("shopify.instance.ept", "search_read", [], fields=["name", "shopify_host"], limit=2))
print("order line sample:", x("sale.order.line", "search_read", [["order_id.team_id.name", "=", "Shopify"]], fields=[k for k in (x("sale.order.line", "fields_get", attributes=["type"]) or {}) if "shopify" in k][:8] + ["product_id"], limit=2))
