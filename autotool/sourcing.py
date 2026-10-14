"""Phase 2: component sourcing.

For every BOM line, ask each configured supplier for offers on the exact manufacturer part number, work out what we
would actually have to buy (quantity for N boards + spares, MOQ, order multiple, price break) and the total in INR,
then pick the cheapest offer that has enough stock. Writes a comparison CSV, one cart/upload CSV per supplier,
downloads datasheets and renders an HTML + PDF purchase report.

Suppliers:
  lcsc       JLCPCB/LCSC parts search, no key needed (USD prices)
  element14  needs keys.json "element14_key"   (developer account at partner.element14.com, store in.element14.com)
  mouser     needs keys.json "mouser_key"      (Mouser Search API key, My Mouser > APIs)
  digikey    needs keys.json "digikey_client_id" + "digikey_client_secret"  (developer.digikey.com, Production app)

Nothing is ordered here: the output is cart files you upload/review on each site.
"""
import csv
import datetime
import json
import math
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = os.path.join(ROOT, "keys.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AltiumAutoTool/1.0"}


def load_keys():
    return json.load(open(KEYS, encoding="utf-8")) if os.path.exists(KEYS) else {}


def norm(mpn):
    return re.sub(r"[\s_\-/.,()]", "", (mpn or "").upper())


def usd_inr(keys):
    if keys.get("usd_inr"):
        return float(keys["usd_inr"])
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=INR", timeout=10).json()
        return float(r["rates"]["INR"])
    except Exception:
        return 84.0


def _num(s):
    m = re.search(r"[\d,]*\.?\d+", str(s).replace(",", ""))
    return float(m.group(0)) if m else 0.0


# ------------------------------------------------------------------------------------------------ providers
# Every provider returns a list of offers:
#   dict(supplier, sku, mpn, mfr, stock, moq, mult, breaks=[(qty, unit_price_inr)], url, datasheet, package)

def lcsc(mpn, keys, fx):
    r = requests.post("https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList",
                      json={"keyword": mpn, "currentPage": 1, "pageSize": 25}, headers=UA, timeout=20)
    items = (((r.json().get("data") or {}).get("componentPageInfo") or {}).get("list")) or []
    out = []
    for x in items:
        prices = x.get("componentPrices") or []
        out.append(dict(supplier="lcsc", sku=x.get("componentCode"), mpn=x.get("componentModelEn", ""), mfr=x.get("componentBrandEn", ""),
                        stock=int(x.get("stockCount") or 0), moq=int(x.get("minPurchaseNum") or 1) or 1, mult=1,
                        breaks=sorted((int(p["startNumber"]), float(p["productPrice"]) * fx) for p in prices),
                        url=x.get("lcscGoodsUrl", ""), datasheet=x.get("dataManualUrl") or "", package=x.get("componentSpecificationEn", ""),
                        jlc_library=x.get("componentLibraryType", "")))
    return out


def element14(mpn, keys, fx):
    k = keys.get("element14_key")
    if not k:
        return None
    store = keys.get("element14_store", "in.element14.com")
    q = {"callInfo.responseDataFormat": "JSON", "callInfo.apiKey": k, "term": "manuPartNum:" + mpn, "storeInfo.id": store,
         "resultsSettings.offset": 0, "resultsSettings.numberOfResults": 10, "resultsSettings.responseGroup": "large"}
    r = requests.get("https://api.element14.com/catalog/products", params=q, headers=UA, timeout=20)
    r.raise_for_status()
    d = r.json()
    ret = d.get("manufacturerPartNumberSearchReturn") or d.get("premierFarnellPartNumberReturn") or {}
    cur_fx = 1.0 if store.startswith("in.") else fx  # India store prices are INR, others treated as USD
    out = []
    for p in ret.get("products") or []:
        ds = p.get("datasheets") or []
        out.append(dict(supplier="element14", sku=p.get("sku"), mpn=p.get("translatedManufacturerPartNumber", ""), mfr=p.get("brandName", ""),
                        stock=int((p.get("stock") or {}).get("level") or 0), moq=int(p.get("translatedMinimumOrderQuality") or 1) or 1,
                        mult=int(p.get("orderMultiples") or 1) or 1,
                        breaks=sorted((int(b["from"]), float(b["cost"]) * cur_fx) for b in p.get("prices") or []),
                        url="https://%s/%s" % (store, p.get("sku")), datasheet=ds[0].get("url", "") if ds else "", package=p.get("packSize", "")))
    return out


def mouser(mpn, keys, fx):
    k = keys.get("mouser_key")
    if not k:
        return None
    r = requests.post("https://api.mouser.com/api/v1/search/partnumber?apiKey=" + k,
                      json={"SearchByPartRequest": {"mouserPartNumber": mpn, "partSearchOptions": "Exact"}}, headers=UA, timeout=20)
    r.raise_for_status()
    out = []
    for p in ((r.json().get("SearchResults") or {}).get("Parts")) or []:
        br = []
        for b in p.get("PriceBreaks") or []:
            f = 1.0 if b.get("Currency", "INR") == "INR" else fx
            br.append((int(b["Quantity"]), _num(b["Price"]) * f))
        out.append(dict(supplier="mouser", sku=p.get("MouserPartNumber"), mpn=p.get("ManufacturerPartNumber", ""), mfr=p.get("Manufacturer", ""),
                        stock=int(_num(p.get("AvailabilityInStock") or 0)), moq=int(_num(p.get("Min") or 1)) or 1, mult=int(_num(p.get("Mult") or 1)) or 1,
                        breaks=sorted(br), url=p.get("ProductDetailUrl", ""), datasheet=p.get("DataSheetUrl", ""), package=""))
    return out


_dk_token = {}


def digikey(mpn, keys, fx):
    cid, sec = keys.get("digikey_client_id"), keys.get("digikey_client_secret")
    if not (cid and sec):
        return None
    if _dk_token.get("exp", 0) < time.time():
        t = requests.post("https://api.digikey.com/v1/oauth2/token",
                          data={"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"}, timeout=20)
        t.raise_for_status()
        _dk_token.update(tok=t.json()["access_token"], exp=time.time() + int(t.json().get("expires_in", 600)) - 30)
    h = {"Authorization": "Bearer " + _dk_token["tok"], "X-DIGIKEY-Client-Id": cid, "X-DIGIKEY-Locale-Site": "IN",
         "X-DIGIKEY-Locale-Currency": "INR", "X-DIGIKEY-Locale-Language": "en", "Content-Type": "application/json"}
    r = requests.post("https://api.digikey.com/products/v4/search/keyword", headers=h, timeout=20,
                      json={"Keywords": mpn, "Limit": 10, "Offset": 0})
    r.raise_for_status()
    d = r.json()
    prods = (d.get("ExactMatches") or []) + (d.get("Products") or [])
    out = []
    for p in prods:
        for v in p.get("ProductVariations") or []:
            out.append(dict(supplier="digikey", sku=v.get("DigiKeyProductNumber"), mpn=p.get("ManufacturerProductNumber", ""),
                            mfr=(p.get("Manufacturer") or {}).get("Name", ""), stock=int(v.get("QuantityAvailableforPackageType") or p.get("QuantityAvailable") or 0),
                            moq=int(v.get("MinimumOrderQuantity") or 1) or 1, mult=1,
                            breaks=sorted((int(b["BreakQuantity"]), float(b["UnitPrice"])) for b in v.get("StandardPricing") or []),
                            url=p.get("ProductUrl", ""), datasheet=p.get("DatasheetUrl", ""), package=(v.get("PackageType") or {}).get("Name", "")))
    return out


PROVIDERS = {"lcsc": lcsc, "element14": element14, "mouser": mouser, "digikey": digikey}


# ------------------------------------------------------------------------------------------------ costing
def cost(offer, need):
    """(buy_qty, unit_inr, total_inr) for `need` pieces, or None if the offer can't supply it."""
    if not offer["breaks"] or offer["stock"] < need:
        return None
    buy = max(need, offer["moq"], offer["breaks"][0][0])
    m = offer["mult"] or 1
    buy = int(math.ceil(buy / m) * m)
    unit = [p for q, p in offer["breaks"] if q <= buy]
    unit = unit[-1] if unit else offer["breaks"][0][1]
    # buying up to the next price break can be cheaper overall (e.g. 10 resistors cost less than 7)
    best = (buy, unit, buy * unit)
    for q, p in offer["breaks"]:
        if q > buy and q * p < best[2] and offer["stock"] >= q:
            best = (q, p, q * p)
    return best


def match_kind(want, got):
    a, b = norm(want), norm(got)
    if not a or not b:
        return None
    if a == b:
        return "exact"
    if b.startswith(a) or a.startswith(b):
        return "partial"
    return None


def variants(mpn):
    """Search keys to try in order: as written, separators as spaces, the first word (drops packaging suffixes)."""
    out = [mpn]
    v = re.sub(r"[_]", " ", mpn).strip()
    if v not in out:
        out.append(v)
    first = re.split(r"[\s_]", mpn.strip())[0]
    if len(first) >= 5 and first not in out:
        out.append(first)
    return out


def source(bom_lines, boards=1, spare_pct=10, providers=None, log=print):
    keys = load_keys()
    fx = usd_inr(keys)
    providers = providers or list(PROVIDERS)
    active = []
    for name in providers:
        try:
            if PROVIDERS[name]("TEST", keys, fx) is None:
                log("  %-9s not configured (add its key to keys.json)" % name)
                continue
        except Exception:
            pass  # configured but the dummy query failed; real queries will report errors
        active.append(name)
    log("  suppliers: %s | 1 USD = %.2f INR" % (", ".join(active), fx))
    results = []
    for ln in bom_lines:
        per_board = len(ln["des"])
        need = per_board * boards
        need = need + max(1 if need < 20 else 0, math.ceil(need * spare_pct / 100)) if spare_pct else need
        row = dict(line=ln, need=need, offers=[], errors=[])
        mpn = ln["mpn"]
        for name in active:
            for key in variants(mpn):
                try:
                    n0 = len(row["offers"])
                    for o in PROVIDERS[name](key, keys, fx) or []:
                        mk = match_kind(key, o["mpn"])
                        if not mk:
                            continue
                        if key != mpn:
                            mk = "partial"   # found only via a simplified search key
                        c = cost(o, need)
                        row["offers"].append(dict(o, match=mk, buy=c[0] if c else None, unit=c[1] if c else None, total=c[2] if c else None))
                    if any(o["total"] is not None for o in row["offers"][n0:]):
                        break   # stop at the first search key that gives a buyable offer
                except Exception as e:
                    row["errors"].append("%s: %s" % (name, str(e)[:120]))
                    break
        ok = [o for o in row["offers"] if o["total"] is not None]
        exact = [o for o in ok if o["match"] == "exact"]
        pool = exact or ok
        row["best"] = min(pool, key=lambda o: o["total"]) if pool else None
        results.append(row)
        log("  %-28s %-30s need %4d -> %s" % (", ".join(ln["des"])[:28], mpn[:30], need,
            "%s %s Rs %.2f (%s)" % (row["best"]["supplier"], row["best"]["sku"], row["best"]["total"], row["best"]["match"]) if row["best"] else "NOT FOUND"))
    return results, fx, active


# ------------------------------------------------------------------------------------------------ outputs
CART_HEADERS = {
    "lcsc": ["LCSC Part Number", "Quantity", "Manufacturer Part Number", "Customer Reference"],
    "element14": ["Part Number", "Quantity", "Manufacturer Part Number", "Line Note"],
    "mouser": ["Mouser Part Number", "Quantity", "Manufacturer Part Number", "Customer Part Number"],
    "digikey": ["Digi-Key Part Number", "Quantity", "Manufacturer Part Number", "Customer Reference"],
}


def write_outputs(results, fx, active, out_dir, title, boards, datasheets=True, log=print):
    os.makedirs(out_dir, exist_ok=True)
    date = datetime.date.today().isoformat()
    comp = os.path.join(out_dir, "sourcing_comparison_%s.csv" % date)
    with open(comp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Designators", "MPN", "Need", "Chosen supplier", "SKU", "Match", "Buy qty", "Unit INR", "Total INR"] +
                   ["%s total INR" % s for s in active] + ["Notes"])
        for r in results:
            b = r["best"]
            per = {}
            for o in r["offers"]:
                if o["total"] is not None and (o["supplier"] not in per or o["total"] < per[o["supplier"]]):
                    per[o["supplier"]] = o["total"]
            nostock = sorted({"%s %s (stock %d)" % (o["supplier"], o["sku"], o["stock"]) for o in r["offers"] if o["total"] is None})
            note = "; ".join(r["errors"]) or ("" if b else ("found but not enough stock: " + ", ".join(nostock[:3]) if nostock
                                                             else "not found - choose manually"))
            w.writerow([", ".join(r["line"]["des"]), r["line"]["mpn"], r["need"], b["supplier"] if b else "", b["sku"] if b else "",
                        b["match"] if b else "", b["buy"] if b else "", "%.2f" % b["unit"] if b else "", "%.2f" % b["total"] if b else ""] +
                       ["%.2f" % per[s] if s in per else "" for s in active] + [note])
    carts = {}
    for s in active:
        rows = [r for r in results if r["best"] and r["best"]["supplier"] == s]
        if not rows:
            continue
        p = os.path.join(out_dir, "cart_%s_%s.csv" % (s, date))
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(CART_HEADERS[s])
            for r in rows:
                w.writerow([r["best"]["sku"], r["best"]["buy"], r["best"]["mpn"], " ".join(r["line"]["des"])[:30]])
        carts[s] = (p, sum(r["best"]["total"] for r in rows), len(rows))
    ds_dir = os.path.join(out_dir, "datasheets")
    got = 0
    if datasheets:
        os.makedirs(ds_dir, exist_ok=True)
        for r in results:
            b = r["best"]
            url = b and b.get("datasheet")
            if not url:
                continue
            fn = os.path.join(ds_dir, re.sub(r"[^\w.\-]", "_", r["line"]["mpn"]) + ".pdf")
            if os.path.exists(fn):
                got += 1
                continue
            try:
                if url.startswith("//"):
                    url = "https:" + url
                d = requests.get(url, headers=UA, timeout=30)
                if d.ok and d.content[:4] == b"%PDF":
                    open(fn, "wb").write(d.content)
                    got += 1
            except Exception:
                pass
    html_path = os.path.join(out_dir, "purchase_report_%s.html" % date)
    open(html_path, "w", encoding="utf-8").write(_html(results, fx, active, carts, title, boards, got))
    pdf = _pdf(html_path)
    log("  datasheets downloaded: %d" % got)
    return dict(comparison=comp, carts=carts, report_html=html_path, report_pdf=pdf, datasheets=ds_dir)


def _html(results, fx, active, carts, title, boards, ndocs):
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;")
    total = sum(r["best"]["total"] for r in results if r["best"])
    missing = [r for r in results if not r["best"]]
    rows = []
    for r in results:
        b = r["best"]
        rows.append("<tr%s><td>%s</td><td>%s</td><td class=n>%d</td><td>%s</td><td>%s</td><td>%s</td><td class=n>%s</td><td class=n>%s</td><td class=n>%s</td></tr>" % (
            "" if b else " class=miss", esc(", ".join(r["line"]["des"])), esc(r["line"]["mpn"]), r["need"],
            esc(b["supplier"]) if b else "-", ('<a href="%s">%s</a>' % (esc(b["url"]), esc(b["sku"]))) if b else "-",
            esc(b["match"]) if b else "", b["buy"] if b else "", "%.2f" % b["unit"] if b else "", "%.2f" % b["total"] if b else ""))
    cart_rows = "".join("<tr><td>%s</td><td class=n>%d</td><td class=n>%.2f</td><td>%s</td></tr>" % (s, n, t, esc(os.path.basename(p)))
                        for s, (p, t, n) in carts.items())
    return """<!doctype html><html><head><meta charset="utf-8"><title>Purchase report</title><style>
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#111}h1{font-size:20px}h2{font-size:15px;margin-top:22px}
table{border-collapse:collapse;width:100%%;font-size:11px}td,th{border:1px solid #ccc;padding:3px 5px;text-align:left}
th{background:#eee}.n{text-align:right}.miss td{background:#fde2e2}.box{background:#f4f4f4;padding:8px 12px;border-radius:6px}
</style></head><body>
<h1>Purchase report: %s</h1>
<div class=box>Boards: <b>%d</b> &nbsp;|&nbsp; Suppliers searched: <b>%s</b> &nbsp;|&nbsp; 1 USD = %.2f INR &nbsp;|&nbsp; Generated %s<br>
Parts total (cheapest per line, excl. shipping/GST): <b>Rs %.2f</b> &nbsp;|&nbsp; Lines not found: <b>%d</b> &nbsp;|&nbsp; Datasheets: %d</div>
<h2>Per supplier (upload the cart file on that site)</h2><table><tr><th>Supplier</th><th>Lines</th><th>Total INR</th><th>Cart file</th></tr>%s</table>
<h2>Lines</h2><table><tr><th>Designators</th><th>MPN</th><th>Need</th><th>Supplier</th><th>SKU</th><th>Match</th><th>Buy</th><th>Unit INR</th><th>Total INR</th></tr>%s</table>
<p style="font-size:11px;color:#555">"partial" = supplier part number only starts with / extends the BOM MPN (packaging or suffix variant): check before ordering.
Red lines were not found with stock: choose a part manually. Shipping, customs and GST are not included.</p>
</body></html>""" % (esc(title), boards, ", ".join(active), fx, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), total, len(missing), ndocs,
                     cart_rows, "".join(rows))


EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def _pdf(html_path):
    edge = next((e for e in EDGE if os.path.exists(e)), None)
    if not edge:
        return None
    pdf = html_path[:-5] + ".pdf"
    subprocess.run([edge, "--headless", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf=" + pdf, Path(html_path).as_uri()],
                   capture_output=True, timeout=120)
    return pdf if os.path.exists(pdf) else None
