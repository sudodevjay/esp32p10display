"""Phase 3: JLCPCB PCB order preparation through the website (Playwright, Chromium).

  login   opens a visible browser with a saved profile; you sign in once, the session is kept in work/jlc_profile
  quote   uploads the Gerber zip, waits for JLC's detection ("Detected 2 layer board of AxB mm"), sets the options,
          reads the price / build time / shipping estimate and saves a screenshot
  --cart  additionally clicks SAVE TO CART (needs a signed-in profile). Checkout and payment are never automated.

The site is a Vue app; option rows look like
  <div class="main-btn-group"><label> PCB Thickness ...</label><div class="formgroup"> ...buttons... </div></div>
If JLC changes the page, adjust ROW / the selectors here.
"""
import asyncio
import datetime
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "work", "jlc_profile")
QUOTE_URL = "https://cart.jlcpcb.com/quote"
ROW = "xpath=//div[contains(@class,'main-btn-group')][label[normalize-space(text())=\"%s\"]]"

DEFAULT_OPTIONS = {
    "PCB Thickness": "1.6mm",
    "PCB Color": "Green",
    "Surface Finish": "HASL(with lead)",
    "Outer Copper Weight": "1 oz",
}


async def _ctx(p, headless):
    os.makedirs(PROFILE, exist_ok=True)
    return await p.chromium.launch_persistent_context(PROFILE, headless=headless, viewport={"width": 1400, "height": 1000})


async def _signed_in(pg):
    t = await pg.inner_text("body")
    return "Sign In" not in t[:400]


async def login(timeout_s=600, log=print):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        ctx = await _ctx(p, headless=False)
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pg.goto(QUOTE_URL, wait_until="domcontentloaded", timeout=90000)
        await pg.wait_for_timeout(4000)
        if await _signed_in(pg):
            log("already signed in")
            await ctx.close()
            return True
        log("Sign in to JLCPCB in the browser window that just opened (up to %d s) ..." % timeout_s)
        for _ in range(timeout_s // 3):
            await pg.wait_for_timeout(3000)
            try:
                if "cart.jlcpcb.com" not in pg.url:
                    continue
                if await _signed_in(pg):
                    log("signed in, session saved in " + PROFILE)
                    await ctx.close()
                    return True
            except Exception:
                pass
        await ctx.close()
        return False


async def _set_option(pg, label, value, log):
    row = pg.locator(ROW % label)
    if await row.count() == 0:
        log("  ! option row not found: %s" % label)
        return False
    opt = row.first.locator("xpath=.//*[self::button or self::span or self::div or self::li][normalize-space()=\"%s\"]" % value)
    if await opt.count() == 0:
        log("  ! value '%s' not offered for %s" % (value, label))
        return False
    await opt.last.click()
    await pg.wait_for_timeout(700)
    return True


async def _set_qty(pg, qty, log):
    """The quantity is a popover (span.qtySelect) with preset buttons and a custom-number input + Confirm."""
    row = pg.locator(ROW % "PCB Qty")
    if await row.count() == 0:
        log("  ! PCB Qty row not found")
        return False
    await row.first.locator("css=.qtySelect").first.click()
    await pg.wait_for_timeout(800)
    pop = pg.locator("css=.el-popover").locator("visible=true")   # the popover is rendered outside the row
    item = pg.locator("css=ul.qty-select-list button").filter(has_text=re.compile(r"^\s*%d\s*$" % qty)).locator("visible=true")
    if await item.count():
        await item.first.click()
    else:
        inp = pop.locator("css=input.el-input__inner").locator("visible=true")
        if await inp.count() == 0:
            log("  ! quantity %d not offered" % qty)
            return False
        await inp.first.fill(str(qty))
        await pop.get_by_text("Confirm").first.click()
    await pg.wait_for_timeout(1500)
    shown = (await row.first.locator("css=.qtySelect").first.inner_text()).strip()
    if shown != str(qty):
        log("  ! quantity shows %s instead of %d" % (shown, qty))
        return False
    return True


def _money(s):
    m = re.search(r"\$\s*([\d,]+\.?\d*)", s)
    return float(m.group(1).replace(",", "")) if m else None


async def quote(zip_path, out_dir, qty=5, options=None, cart=False, headless=True, log=print):
    from playwright.async_api import async_playwright
    opts = dict(DEFAULT_OPTIONS)
    opts.update(options or {})
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    res = dict(zip=zip_path, qty=qty, options=opts, detected=None, price_usd=None, build_time=None, shipping=None,
               signed_in=None, added_to_cart=False, screenshots=[])
    async with async_playwright() as p:
        ctx = await _ctx(p, headless)
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pg.goto(QUOTE_URL, wait_until="domcontentloaded", timeout=90000)
        await pg.wait_for_timeout(5000)
        res["signed_in"] = await _signed_in(pg)
        log("  signed in: %s" % res["signed_in"])
        async with pg.expect_file_chooser(timeout=30000) as fc:
            await pg.get_by_text("Add gerber file").first.click()
        await (await fc.value).set_files(zip_path)
        for _ in range(60):
            await pg.wait_for_timeout(2000)
            t = await pg.inner_text("body")
            m = re.search(r"Detected[^\n|]*", t)
            if m:
                res["detected"] = m.group(0).strip()
                break
        log("  JLC: %s" % (res["detected"] or "board NOT detected"))
        if not res["detected"]:
            shot = os.path.join(out_dir, "jlc_failed_%s.png" % stamp)
            await pg.screenshot(path=shot, full_page=True)
            res["screenshots"].append(shot)
            await ctx.close()
            return res
        await pg.wait_for_timeout(3000)
        await _set_qty(pg, qty, log)
        for k, v in opts.items():
            ok = await _set_option(pg, k, v, log)
            log("  %-20s %s%s" % (k, v, "" if ok else "  (not set)"))
        await pg.wait_for_timeout(3000)
        t = await pg.inner_text("body")
        i = t.find("Calculated Price")
        res["price_usd"] = _money(t[i:i + 300]) if i >= 0 else None
        m = re.search(r"PCB Build Time(.{0,200})", t, re.S)
        res["build_time"] = re.sub(r"\s+", " ", m.group(1))[:120] if m else None
        m = re.search(r"Shipping Estimate\s*\n?\s*(\$[\d.,]+)\s*\n?\s*([^\n]+)\s*\n?\s*([^\n]+)", t)
        res["shipping"] = " ".join(x.strip() for x in m.groups()) if m else None
        shot = os.path.join(out_dir, "jlc_quote_%s.png" % stamp)
        await pg.screenshot(path=shot, full_page=True)
        res["screenshots"].append(shot)
        if cart:
            if not res["signed_in"]:
                log("  ! not signed in: run 'python -m autotool jlc-login' first; cart step skipped")
            else:
                await pg.get_by_text("SAVE TO CART", exact=True).first.click()
                await pg.wait_for_timeout(6000)
                shot = os.path.join(out_dir, "jlc_cart_%s.png" % stamp)
                await pg.screenshot(path=shot, full_page=False)
                res["screenshots"].append(shot)
                res["added_to_cart"] = True
                res["cart_url"] = pg.url
                log("  saved to cart -> %s (review and pay on the website yourself)" % pg.url)
        await ctx.close()
    return res


def run(coro):
    return asyncio.run(coro)
