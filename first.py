import json
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

def _normalize_price(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.]", "", value)
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None

def _extract_price_from_ld(data):
    if isinstance(data, dict):
        if "price" in data:
            return data["price"]
        if "lowPrice" in data:
            return data["lowPrice"]
        if "offers" in data:
            price = _extract_price_from_ld(data["offers"])
            if price is not None:
                return price
        for value in data.values():
            price = _extract_price_from_ld(value)
            if price is not None:
                return price
        return None
    if isinstance(data, list):
        for item in data:
            price = _extract_price_from_ld(item)
            if price is not None:
                return price
    return None

def _extract_price_from_html(html):
    soup = BeautifulSoup(html, "html.parser")

    for script in soup.select('script[type="application/ld+json"]'):
        text = script.string
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue
        price = _extract_price_from_ld(data)
        if price is not None:
            return price

    for prop in ("product:price:amount", "og:price:amount"):
        meta = soup.find("meta", {"property": prop})
        if meta and meta.get("content"):
            return meta["content"]

    price_tag = soup.find(attrs={"itemprop": "price"})
    if price_tag:
        if price_tag.get("content"):
            return price_tag["content"]
        if price_tag.text:
            return price_tag.text

    return None

# Pro tip: If "price"/"ld+json" is missing in View Page Source, rely on Playwright and wait for the page to render.

def _fetch_price(vendor, handle, headers):
    headers = headers or {}
    user_agent = headers.get("User-Agent")
    extra_headers = {k: v for k, v in headers.items() if k.lower() != "user-agent"}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=user_agent,
            extra_http_headers=extra_headers,
            viewport={"width": 1920, "height": 1080},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true});"
        )
        Stealth().apply_stealth_sync(context)
        page = context.new_page()
        try:
            # Flexispot (Magento) uses flat URLs
            url = f"https://{vendor}/{handle}"
            resp = page.goto(url, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(2000)
            if resp and 200 <= resp.status < 300:
                html = page.content()
                price_raw = _extract_price_from_html(html)
                price = _normalize_price(price_raw)
                if price:
                    return {"price": price, "status": resp.status, "source": "html"}

            # Shopify fallback (Hinomi)
            shopify_url = f"https://{vendor}/products/{handle}"
            resp = page.goto(shopify_url, wait_until="networkidle", timeout=45000)
            if resp and 200 <= resp.status < 300:
                price_raw = _extract_price_from_html(page.content())
                price = _normalize_price(price_raw)
                if price:
                    return {"price": price, "status": resp.status, "source": "shopify_html"}
        except Exception as e:
            return {"error": f"Failed: {str(e)}"}
        finally:
            context.close()
            browser.close()

    return {"error": "Price not found"}

def check_price_war(target_vendor, target_handle, comp_vendor, comp_handle):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    #TargetPrice
    target_result = _fetch_price(target_vendor, target_handle, headers)
    if "error" in target_result:
        return target_result["error"]
    target_price = target_result["price"]
    print(f"DEBUG: {target_vendor} status: {target_result['status']} ({target_result['source']})")
    #CompetitorPrice
    comp_result = _fetch_price(comp_vendor, comp_handle, headers)
    if "error" in comp_result:
        return comp_result["error"]
    comp_price = comp_result["price"]
    print(f"DEBUG: {comp_vendor} status: {comp_result['status']} ({comp_result['source']})")
    delta = target_price - comp_price
    
    return {
        "target_price": target_price,
        "comp_price": comp_price,
        "delta": delta,
        "alert": delta > 100
    }

print(check_price_war("www.hinomi.co", "hinomi-q2-ergonomic-office-chair", 
"www.flexispot.com", "flexispot-professional-ergonomic-office-chair-c7m"))
