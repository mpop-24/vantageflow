import json
import re
from urllib.parse import urlparse
import httpx

def _vendor_candidates(vendor):
    cleaned = vendor.strip()
    cleaned = cleaned.replace("https://", "").replace("http://", "")
    cleaned = cleaned.strip("/")
    if not cleaned:
        return []
    candidates = [cleaned]
    if cleaned.startswith("www."):
        candidates.append(cleaned[4:])
    else:
        candidates.append(f"www.{cleaned}")
    seen = set()
    ordered = []
    for host in candidates:
        if host and host not in seen:
            ordered.append(host)
            seen.add(host)
    return ordered

def _parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return None
    return None

def _extract_first_price(text):
    if not text:
        return None
    match = re.search(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
    if not match:
        return None
    value = match.group(0).replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def _extract_stock_status(text):
    if not text:
        return None
    lowered = text.lower()
    if re.search(r"\b(out of stock|sold out|unavailable)\b", lowered):
        return "Out of Stock"
    if re.search(r"\b(backorder|backordered|pre[-\s]?order|preorder)\b", lowered):
        return "Backordered"
    if re.search(r"\b(low stock|only \d+\s+left|limited stock)\b", lowered):
        return "Low Stock"
    if re.search(r"\b(in stock|available now|ready to ship)\b", lowered):
        return "In Stock"
    return None


def _extract_shipping_estimate(text):
    if not text:
        return None, None
    lowered = text.lower()
    patterns = [
        (r"estimated delivery[:\s]+(\d{1,2})\s*[-–]\s*(\d{1,2})\s*(business\s*)?days?", "range"),
        (r"estimated delivery[:\s]+(\d{1,2})\s*(business\s*)?days?", "single"),
        (r"(free\s+)?(\d{1,2})\s*[-–]\s*(\d{1,2})\s*(business\s*)?days?", "range"),
        (r"(free\s+)?(\d{1,2})\s*day shipping", "single"),
        (r"ships?\s+in\s+(\d{1,2})\s*(business\s*)?days?", "ships_days"),
        (r"ships?\s+in\s+(\d{1,2})\s*(week|weeks)", "ships_weeks"),
        (r"delivery in\s+(\d{1,2})\s*(business\s*)?days?", "ships_days"),
    ]
    for pattern, kind in patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        if kind == "range":
            low = int(match.group(2))
            high = int(match.group(3))
            label = f"Ships in {low}-{high} days"
            if match.group(1):
                label = f"Free {low}-{high} day shipping"
            return label, (low + high) / 2
        if kind == "single":
            days = int(match.group(2))
            label = f"Free {days}-day shipping" if match.group(1) else f"{days}-day shipping"
            return label, days
        if kind == "ships_days":
            days = int(match.group(1))
            return f"Ships in {days} days", days
        if kind == "ships_weeks":
            weeks = int(match.group(1))
            days = weeks * 7
            return f"Ships in {weeks} weeks", days
    return None, None


def _extract_discount_code(text):
    if not text:
        return None
    lowered = text.lower()
    code_match = re.search(r"\b(?:code|promo code|coupon code|use code)\s*[:\-]?\s*([a-z0-9]{3,12})\b", lowered)
    discount_match = re.search(r"\b(\d{1,2})\s*%?\s*off\b", lowered)
    dollars_match = re.search(r"\$\s*(\d{1,4})\s*off\b", lowered)
    keyword_match = re.search(r"\b(sale|discount|promo|promotion)\b", lowered)
    code = code_match.group(1).upper() if code_match else None
    if discount_match:
        amount = f"{discount_match.group(1)}% off"
    elif dollars_match:
        amount = f"${dollars_match.group(1)} off"
    else:
        amount = None
    if code and amount:
        return f"{amount} (CODE {code})"
    if code:
        return f"CODE {code}"
    if amount:
        return amount
    if keyword_match:
        return "Promo active"
    return None


def _extract_shipping_cost(text):
    if not text:
        return None
    lowered = text.lower()
    if re.search(r"\bfree shipping\b", lowered):
        return 0.0
    match = re.search(r"shipping\s*(?:costs|is|:)?\s*\$?\s*(\d{1,4}(?:\.\d{2})?)", lowered)
    if not match:
        match = re.search(r"\$\s*(\d{1,4}(?:\.\d{2})?)\s*shipping", lowered)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_warranty_years(text):
    if not text:
        return None
    match = re.search(r"(\d{1,2})\s*(?:year|yr)\s*warranty", text, re.IGNORECASE)
    if not match:
        match = re.search(r"warranty\s*(?:of|:)?\s*(\d{1,2})\s*(?:year|yr)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_review_count(text):
    if not text:
        return None
    match = re.search(r"(\d{1,3}(?:,\d{3})+)\s+(?:reviews|ratings)\b", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(\d{1,5})\s+(?:reviews|ratings)\b", text, re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).replace(",", "")
    try:
        return int(value)
    except ValueError:
        return None
def _extract_price_from_jina(data):
    payload = data
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        payload = data["data"]
    if not isinstance(payload, dict):
        return None, None
    content = payload.get("content") or ""
    title = payload.get("title")
    price = _extract_first_price(content)
    return price, title

def _normalize_url(url):
    cleaned = (url or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return f"https://{cleaned.lstrip('/')}"

def _path_candidates(handle):
    cleaned = handle.strip()
    if cleaned.startswith("/"):
        cleaned = cleaned[1:]
    if not cleaned:
        return []
    candidates = [
        f"/products/{cleaned}.js",
        f"/{cleaned}.js",
    ]
    seen = set()
    ordered = []
    for path in candidates:
        if path not in seen:
            ordered.append(path)
            seen.add(path)
    return ordered

def _page_candidates(handle):
    cleaned = handle.strip()
    if cleaned.startswith("/"):
        cleaned = cleaned[1:]
    candidates = []
    if cleaned:
        candidates.extend([
            f"/{cleaned}",
            f"/products/{cleaned}",
        ])
    candidates.append("/")
    seen = set()
    ordered = []
    for path in candidates:
        if path not in seen:
            ordered.append(path)
            seen.add(path)
    return ordered

def _fetch_json(url, headers, use_jina=False):
    target_url = url
    if use_jina:
        target_url = f"https://r.jina.ai/{url}"
        headers = {"Accept": "application/json"}
    try:
        response = httpx.get(target_url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None, f"Status {response.status_code}"
        try:
            return response.json(), None
        except Exception:
            data = _parse_json(response.text)
            if data is None:
                return None, "Invalid JSON"
            return data, None
    except Exception as e:
        return None, str(e)

def fetch_shopify_js(vendor, handle, include_raw=False):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    last_error = None
    for host in _vendor_candidates(vendor):
        for path in _path_candidates(handle):
            url = f"https://{host}{path}"
            data, error = _fetch_json(url, headers)
            if data:
                price_raw = data.get("price")
                if price_raw is None:
                    last_error = f"{host}{path}: Missing price"
                else:
                    price = price_raw / 100
                    raw_compare = data.get("compare_at_price")
                    compare_at = (raw_compare / 100) if raw_compare else None
                    return {
                        "vendor": host,
                        "product": data.get("title"),
                        "current_price": price,
                        "msrp": compare_at,
                        "on_sale": compare_at is not None and price < compare_at,
                        "status": "success",
                        "source": "shopify_js",
                        "raw": data if include_raw else None,
                    }
            else:
                last_error = f"{host}{path}: {error}"

        for path in _page_candidates(handle):
            url = f"https://{host}{path}"
            data, error = _fetch_json(url, headers, use_jina=True)
            if data:
                price, title = _extract_price_from_jina(data)
                if price is None:
                    last_error = f"{host}{path} (jina): Price not found"
                    continue
                result = {
                    "vendor": host,
                    "product": title or handle,
                    "current_price": price,
                    "msrp": None,
                    "on_sale": None,
                    "status": "success",
                    "source": "jina",
                }
                if include_raw:
                    result["raw"] = data
                return result
            last_error = f"{host}{path} (jina): {error}"

    return {"vendor": vendor, "error": last_error or "No vendor candidates"}

def get_price(url):
    full_url = _normalize_url(url)
    if not full_url:
        return None
    parsed = urlparse(full_url)
    host = parsed.netloc
    handle = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    if host and handle:
        result = fetch_shopify_js(host, handle)
        if result.get("status") == "success":
            return result.get("current_price")
    data, _error = _fetch_json(full_url, headers={}, use_jina=True)
    if data:
        price, _title = _extract_price_from_jina(data)
        return price
    return None


PLACEHOLDER_MANUAL = "Manual Audit Required"
PLACEHOLDER_PENDING = "Data Pending"


def _needs_placeholder(value):
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in {"", "n/a", "na", "--"}:
        return True
    return False


def get_product_snapshot(url):
    full_url = _normalize_url(url)
    if not full_url:
        return {}
    parsed = urlparse(full_url)
    host = parsed.netloc
    handle = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""

    snapshot = {
        "price": None,
        "stock_status": None,
        "shipping_estimate": None,
        "shipping_days": None,
        "shipping_cost": None,
        "discount": None,
        "review_count": None,
        "warranty_years": None,
    }

    if host and handle:
        result = fetch_shopify_js(host, handle, include_raw=True)
        if result.get("status") == "success":
            snapshot["price"] = result.get("current_price")

    data, _error = _fetch_json(full_url, headers={}, use_jina=True)
    if data:
        content = ""
        if isinstance(data, dict):
            payload = data.get("data", data)
            content = payload.get("content") or ""
        snapshot["stock_status"] = _extract_stock_status(content)
        ship_label, ship_days = _extract_shipping_estimate(content)
        snapshot["shipping_estimate"] = ship_label
        snapshot["shipping_days"] = ship_days
        snapshot["shipping_cost"] = _extract_shipping_cost(content)
        snapshot["discount"] = _extract_discount_code(content)
        snapshot["review_count"] = _extract_review_count(content)
        snapshot["warranty_years"] = _extract_warranty_years(content)
        if snapshot["price"] is None:
            price, _title = _extract_price_from_jina(data)
            snapshot["price"] = price

    if _needs_placeholder(snapshot.get("stock_status")):
        snapshot["stock_status"] = PLACEHOLDER_MANUAL
    if _needs_placeholder(snapshot.get("shipping_estimate")) and snapshot.get("shipping_days") is None:
        snapshot["shipping_estimate"] = PLACEHOLDER_PENDING
    if _needs_placeholder(snapshot.get("discount")):
        snapshot["discount"] = PLACEHOLDER_PENDING

    return snapshot

class PriceScraper:
    def get_price(self, url):
        return get_price(url)

    def get_snapshot(self, url):
        return get_product_snapshot(url)
