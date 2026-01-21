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

class PriceScraper:
    def get_price(self, url):
        return get_price(url)


