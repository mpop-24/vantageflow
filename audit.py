import os
import re
from datetime import datetime, timezone

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

from main import PriceScraper
from supabase_db import get_client_product, update_competitor, update_client_product

PLACEHOLDER_SCAN = "Scanning..."
PLACEHOLDER_NONE = "--"
PLACEHOLDER_MANUAL = "Manual Audit Required"
PLACEHOLDER_PENDING = "Data Pending"


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in {"", "n/a", "na", "--"}:
        return True
    return False


def _is_placeholder_text(value):
    if _is_blank(value):
        return True
    if isinstance(value, str) and value.strip().lower() in {"scanning...", "data pending", "manual audit required"}:
        return True
    return False


def _normalize_competitor_fields(row):
    if _is_blank(row.get("stock_status")):
        row["stock_status"] = PLACEHOLDER_MANUAL
    if _is_blank(row.get("shipping_estimate")):
        row["shipping_estimate"] = PLACEHOLDER_PENDING
    if _is_blank(row.get("discount")):
        row["discount"] = PLACEHOLDER_PENDING
    return row


def _format_price(value):
    if value is None:
        return PLACEHOLDER_SCAN
    return f"${value:.2f}"


def _format_price_precise(value):
    if value is None:
        return PLACEHOLDER_SCAN
    return f"${value:,.2f}"


def _format_signed(value):
    if value is None:
        return PLACEHOLDER_NONE
    sign = ""
    if value > 0:
        sign = "+"
    elif value < 0:
        sign = "-"
    return f"{sign}${abs(value):.2f}"


def _format_signed_precise(value):
    if value is None:
        return PLACEHOLDER_NONE
    sign = ""
    if value > 0:
        sign = "+"
    elif value < 0:
        sign = "-"
    return f"{sign}${abs(value):,.2f}"


def _format_price_round(value):
    if value is None:
        return PLACEHOLDER_NONE
    if value < 0:
        return f"-${abs(value):,.0f}"
    return f"${value:,.0f}"


def _format_premium_hint(value):
    if value is None or value <= 0:
        return PLACEHOLDER_NONE
    rounded = int(round(value, -1))
    return f"${rounded}+"


def _format_checked_label(value):
    if not value:
        return "Last check"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        if "T" in value:
            return value.split("T", 1)[0]
        return value[:10]
    return "Last check"


def _parse_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    return None


def _pt_to_mm(value):
    return value * 0.3528


def _line_height(font_size_pt):
    return _pt_to_mm(font_size_pt) * 1.2


def _right_indent_mm():
    return 6.0


def _draw_multicell(pdf, x, y, w, line_height, text, border=0, align="L", fill=False):
    pdf.set_xy(x, y)
    start_y = y
    pdf.multi_cell(w, line_height, text, border=border, align=align, fill=fill)
    return pdf.get_y() - start_y


def _fit_font_size(pdf, text, max_width, font_name, style, start_size, min_size=10):
    size = start_size
    pdf.set_font(font_name, style, size)
    while size > min_size and pdf.get_string_width(text) > max_width:
        size -= 1
        pdf.set_font(font_name, style, size)
    return size


def _estimate_text_height(pdf, text, max_width, line_height):
    lines = _wrap_pdf_line(pdf, _sanitize_pdf_text(text), max_width)
    return max(1, len(lines)) * line_height


def _estimate_competitor_card_height(pdf, box, theme, width):
    padding = 5
    right_indent = _right_indent_mm()
    max_width = width - padding * 2 - right_indent
    title_size = 9
    body_size = 9
    line_height = _line_height(body_size)

    pdf.set_font(theme["font"], "B", title_size)
    title_width = min(80.0, max_width)
    title_height = _estimate_text_height(pdf, box.get("name") or "", title_width, line_height)

    pdf.set_font(theme["font"], size=body_size)
    price = _format_price_precise(box.get("price"))
    gap = _format_signed_precise(box.get("gap"))
    price_label = f"PRICE: {price}"
    gap_label = f"GAP: {gap}"
    target_price_w = 40.0
    target_gap_w = 40.0
    gutter = 4.0
    if target_price_w + target_gap_w + gutter > max_width:
        available = max_width - gutter
        if available <= 0:
            price_width = max_width / 2
            gap_width = max_width / 2
        else:
            scale = available / (target_price_w + target_gap_w)
            price_width = target_price_w * scale
            gap_width = target_gap_w * scale
    else:
        price_width = target_price_w
        gap_width = target_gap_w
    price_height = max(
        _estimate_text_height(pdf, price_label, price_width, line_height),
        _estimate_text_height(pdf, gap_label, gap_width, line_height),
    )

    stock = box.get("stock_status")
    if _is_blank(stock):
        stock = PLACEHOLDER_MANUAL
    shipping = _format_shipping_label(box)
    promo = box.get("discount")
    if _is_blank(promo):
        promo = PLACEHOLDER_PENDING
    reviews = _format_review_line(box)
    ship_cost = box.get("shipping_cost")
    ship_cost_label = PLACEHOLDER_NONE if ship_cost is None else ("FREE" if ship_cost == 0 else _format_price_precise(ship_cost))
    detail_lines = [
        f"Stock: {stock}",
        f"Delivery: {shipping}",
        f"Ship Cost: {ship_cost_label}",
        f"Promo: {promo}",
        reviews,
    ]
    details_height = 0
    for line in detail_lines:
        details_height += _estimate_text_height(pdf, line, max_width, line_height)

    top_padding = 6
    bottom_padding = 4
    return top_padding + title_height + price_height + details_height + bottom_padding


def _estimate_competitor_cards_height(pdf, payload, theme, width, gutter):
    boxes = _build_competition_boxes(payload)
    heights = []
    for box in boxes:
        heights.append(_estimate_competitor_card_height(pdf, box, theme, width))
    min_card_height = 36
    card_height = max(max(heights) if heights else 0, min_card_height)
    return card_height * 2 + gutter


def _report_id(payload):
    product_id = payload.get("product_id")
    if isinstance(product_id, int):
        return f"{product_id:03d}"
    checked_at = payload.get("checked_at")
    if isinstance(checked_at, datetime):
        return checked_at.strftime("%y%m%d")
    return "000"


def _market_position_tier(payload):
    client_price = payload.get("client_price")
    competitors = payload.get("competitors", [])
    prices = [row.get("price") for row in competitors if row.get("price") is not None]
    if client_price is None or not prices:
        return "Unknown Tier"
    if client_price > max(prices):
        return "Premium Tier"
    if client_price < min(prices):
        return "Value Tier"
    return "Mid Tier"


def _status_summary(payload):
    max_gap = payload.get("max_gap_comp")
    if not max_gap or max_gap.get("gap") is None:
        competitors = payload.get("competitors", [])
        with_gap = [row for row in competitors if row.get("gap") is not None]
        if with_gap:
            max_gap = max(with_gap, key=lambda row: row.get("gap"))
        else:
            return "INSUFFICIENT DATA"
    gap = max_gap["gap"]
    if gap >= 200:
        label = "CRITICAL PRICE VARIANCE"
    elif gap >= 100:
        label = "ELEVATED PRICE VARIANCE"
    elif gap >= 0:
        label = "PRICE VARIANCE"
    else:
        label = "PRICE ADVANTAGE"
    return f"{label} ({_format_signed_precise(gap)})"


def _market_velocity_summary(payload):
    max_gap = payload.get("max_gap_comp")
    if not max_gap or max_gap.get("gap") is None:
        return "Market Velocity (24h): No 24h baseline available."
    prev_gap = max_gap.get("prev_gap")
    prev_checked = _parse_datetime(max_gap.get("prev_checked"))
    if prev_gap is None or prev_checked is None:
        return "Market Velocity (24h): No 24h baseline available."
    checked_at = payload.get("checked_at")
    if not isinstance(checked_at, datetime):
        return "Market Velocity (24h): No 24h baseline available."
    hours = (checked_at - prev_checked).total_seconds() / 3600
    if hours <= 0 or hours > 24:
        return "Market Velocity (24h): No 24h baseline available."
    delta = max_gap.get("gap") - prev_gap
    direction = "widened" if delta > 0 else "narrowed" if delta < 0 else "held steady"
    delta_value = _format_price_round(abs(delta))
    comp_name = _sanitize_pdf_text(max_gap.get("name") or "primary rival")
    if direction == "held steady":
        return f"Market Velocity (24h): Gap held steady vs {comp_name}."
    return f"Market Velocity (24h): Gap {direction} by {delta_value} vs {comp_name}."


def _estimate_ad_leak_from_gap(gap, daily_spend=1000.0):
    if gap is None:
        return None, None
    if gap > 200:
        defection_rate = 0.25
    elif gap > 100:
        defection_rate = 0.15
    else:
        defection_rate = 0.0
    daily_leak = daily_spend * defection_rate
    monthly_leak = daily_leak * 30
    return daily_leak, monthly_leak, defection_rate


def _estimate_ad_leak(payload, daily_spend=1000.0):
    max_gap = payload.get("max_gap_comp")
    gap = max_gap.get("gap") if max_gap else None
    return _estimate_ad_leak_from_gap(gap, daily_spend)


def _top_competitor_rows(payload, limit=2):
    competitors = payload.get("competitors", [])
    def gap_value(row):
        gap = row.get("gap")
        if gap is None:
            gap = _compute_gap(payload.get("client_price"), row.get("price"))
        return gap if gap is not None else float("-inf")

    ranked = sorted(competitors, key=gap_value, reverse=True)
    selected = [row for row in ranked if row.get("price") is not None][:limit]
    if len(selected) < limit:
        remaining = [row for row in competitors if row not in selected]
        remaining_sorted = sorted(remaining, key=lambda r: (r.get("name") or "").lower())
        for row in remaining_sorted:
            if len(selected) >= limit:
                break
            selected.append(row)
    return selected


def _build_competition_boxes(payload):
    boxes = []
    rows = _top_competitor_rows(payload, limit=2)
    while len(rows) < 2:
        rows.append({
            "name": "Competitor TBD",
            "price": None,
            "gap": None,
            "stock_status": None,
            "shipping_estimate": None,
            "shipping_days": None,
            "discount": None,
            "review_count": None,
            "review_velocity": None,
        })
    for row in rows:
        row = _normalize_competitor_fields(dict(row))
        boxes.append({
            "name": row.get("name") or "Competitor",
            "price": row.get("price"),
            "gap": row.get("gap"),
            "trend": row.get("price_trend"),
            "stock_status": row.get("stock_status"),
            "shipping_estimate": row.get("shipping_estimate"),
            "shipping_days": row.get("shipping_days"),
            "shipping_cost": row.get("shipping_cost"),
            "discount": row.get("discount"),
            "review_count": row.get("review_count"),
            "review_velocity": row.get("review_velocity"),
            "warranty_years": row.get("warranty_years"),
            "highlight": _is_highlight_gap(row, payload.get("max_gap_comp")),
        })
    return boxes


def _format_shipping_label(row):
    days = row.get("shipping_days")
    if isinstance(days, (int, float)):
        return f"{int(round(days))}d"
    estimate = row.get("shipping_estimate")
    if estimate and not _is_blank(estimate):
        return estimate
    return PLACEHOLDER_PENDING


def _format_review_line(row):
    count = row.get("review_count")
    if count is None:
        return f"Reviews: {PLACEHOLDER_NONE}"
    velocity = row.get("review_velocity")
    if velocity is not None:
        velocity_value = f"{abs(velocity):.1f}/day"
        if abs(velocity) >= 1:
            velocity_value = f"{abs(velocity):.0f}/day"
        sign = "+" if velocity >= 0 else "-"
        return f"Reviews: {count:,} ({sign}{velocity_value})"
    return f"Reviews: {count:,}"


if FPDF is not None:
    class VantagePDF(FPDF):
        def __init__(self, theme, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.theme = theme

        def header(self):
            self.set_fill_color(*self.theme["background"])
            self.rect(0, 0, self.w, self.h, "F")

            if self.page_no() <= 1:
                return

            header_y = 14
            self.set_draw_color(*self.theme["line"])
            self.set_line_width(0.4)
            self.line(self.l_margin, header_y, self.w - self.r_margin, header_y)

            logo_path = self.theme.get("logo_path")
            if logo_path and os.path.exists(logo_path):
                self.image(logo_path, x=self.l_margin, y=4, w=14)
            self.set_font(self.theme.get("font", "Courier"), "B", 9)
            self.set_text_color(*self.theme["text"])
            self.set_xy(self.l_margin + 18, 5)
            self.cell(0, 4, "VANTAGEFLOW // INTEL_REPORT")
else:
    VantagePDF = None


def _compute_gap(client_price, comp_price):
    if client_price is None or comp_price is None:
        return None
    return client_price - comp_price


def _status_for_gap(gap):
    if gap is None:
        return {
            "label": "Unknown",
            "fill": (220, 220, 220),
            "text": (60, 60, 60),
        }
    if gap >= 200:
        return {"label": "High Risk", "fill": (220, 53, 69), "text": (255, 255, 255)}
    if gap >= 100:
        return {"label": "Elevated", "fill": (255, 152, 0), "text": (255, 255, 255)}
    if gap >= 0:
        return {"label": "Watch", "fill": (255, 235, 59), "text": (60, 60, 60)}
    return {"label": "Price Advantage", "fill": (40, 167, 69), "text": (255, 255, 255)}


def _infer_hero_feature(product_name):
    name = (product_name or "").lower()
    if "omni" in name:
        return "Omni-Dynamic back support"
    if "pro" in name or "elite" in name:
        return "multi-zone lumbar system"
    return "signature ergonomic system"


def _truncate_text(pdf, text, max_width):
    if pdf.get_string_width(text) <= max_width:
        return text
    ellipsis = "..."
    trimmed = text
    while trimmed and pdf.get_string_width(trimmed + ellipsis) > max_width:
        trimmed = trimmed[:-1]
    return trimmed + ellipsis if trimmed else text[:1] + ellipsis


def build_audit_payload(product, client_price, competitor_rows, checked_at, client_snapshot=None):
    competitors = []
    for row in competitor_rows:
        gap = row.get("gap")
        if gap is None:
            gap = _compute_gap(client_price, row.get("price"))
        status = _status_for_gap(gap)
        enriched = _normalize_competitor_fields(dict(row))
        enriched["gap"] = gap
        enriched["status"] = status
        competitors.append(enriched)

    prices = [row["price"] for row in competitors if row.get("price") is not None]
    cheapest = min(competitors, key=lambda r: r["price"]) if prices else None
    most_expensive = max(competitors, key=lambda r: r["price"]) if prices else None

    closest = None
    if client_price is not None and competitors:
        with_gap = [row for row in competitors if row.get("gap") is not None]
        if with_gap:
            closest = min(with_gap, key=lambda r: abs(r["gap"]))

    max_gap_comp = None
    if competitors:
        positive_gaps = [row for row in competitors if row.get("gap") is not None and row["gap"] > 0]
        if positive_gaps:
            max_gap_comp = max(positive_gaps, key=lambda r: r["gap"])

    biggest_move = None
    move_candidates = []
    for row in competitors:
        if row.get("prev_price") is None or row.get("price") is None:
            continue
        move_candidates.append((abs(row["price"] - row["prev_price"]), row))
    if move_candidates:
        biggest_move = max(move_candidates, key=lambda item: item[0])[1]

    price_position_label = None
    if client_price is not None and prices:
        if client_price > max(prices):
            nearest = closest["name"] if closest else (most_expensive["name"] if most_expensive else "nearest rival")
            gap_val = _format_price_round(closest["gap"]) if closest else PLACEHOLDER_NONE
            price_position_label = f"Price Leader: {gap_val} above {nearest}"
        elif client_price < min(prices):
            price_position_label = "Price Leader: Lowest priced vs tracked rivals"
        else:
            price_position_label = "Price Position: Mid-pack vs tracked rivals"

    market_avg_price = None
    market_avg_gap = None
    if prices:
        market_avg_price = sum(prices) / len(prices)
        market_avg_gap = _compute_gap(client_price, market_avg_price)

    ad_leak_statement = None
    if max_gap_comp and max_gap_comp.get("gap") is not None:
        daily_leak, monthly_leak, rate = _estimate_ad_leak_from_gap(max_gap_comp["gap"])
        if monthly_leak is not None and rate is not None:
            ad_leak_statement = (
                f"Estimated Monthly Ad Leak: {_format_price_precise(monthly_leak)}. "
                "Your current pricing delta is effectively subsidizing your rivals' customer acquisition."
            )

    client_snapshot = client_snapshot or {}
    return {
        "product_id": product.id,
        "product_name": product.product_name,
        "client_price": client_price,
        "client_shipping_cost": client_snapshot.get("shipping_cost"),
        "client_warranty_years": client_snapshot.get("warranty_years"),
        "client_review_count": client_snapshot.get("review_count"),
        "checked_at": checked_at,
        "checked_at_label": checked_at.strftime("%B %d, %Y"),
        "competitors": competitors,
        "cheapest": cheapest,
        "most_expensive": most_expensive,
        "closest": closest,
        "max_gap_comp": max_gap_comp,
        "biggest_move": biggest_move,
        "price_position_label": price_position_label,
        "market_avg_price": market_avg_price,
        "market_avg_gap": market_avg_gap,
        "ad_leak_statement": ad_leak_statement,
    }


def build_audit_summary(product, client_price, competitor_rows, checked_at):
    checked_at_str = checked_at.isoformat()
    if checked_at_str.endswith("+00:00"):
        checked_at_str = checked_at_str[:-6] + "Z"
    lines = [
        f"Audit: {product.product_name}",
        f"Checked: {checked_at_str}",
        f"Client price: {_format_price(client_price)}",
        "",
        "Competitors:",
    ]

    if not competitor_rows:
        lines.append("- No competitor prices available")
    else:
        for row in competitor_rows:
            gap = None
            if client_price is not None and row["price"] is not None:
                gap = client_price - row["price"]
            lines.append(
                f"- {row['name']}: {_format_price(row['price'])}"
                f" (gap: {_format_signed(gap)})"
                f" - {row['url']}"
            )

    return "\n".join(lines)


def collect_audit_data(scraper, product_id):
    checked_at = datetime.now(timezone.utc)
    product = get_client_product(product_id)
    if not product:
        raise RuntimeError("Product not found")

    def _get_snapshot(url):
        if hasattr(scraper, "get_snapshot"):
            snapshot = scraper.get_snapshot(url)
            if isinstance(snapshot, dict):
                return snapshot
        return {"price": scraper.get_price(url)}

    client_snapshot = _get_snapshot(product.base_url)
    client_price = client_snapshot.get("price")
    if client_price is not None:
        update_client_product(product.id, client_price=client_price)
    competitor_rows = []

    for comp in product.competitors:
        prev_price = comp.last_price
        prev_checked = comp.last_checked
        snapshot = _get_snapshot(comp.url)
        price = snapshot.get("price")
        if price is None:
            continue
        gap = _compute_gap(client_price, price)
        prev_gap = _compute_gap(client_price, prev_price)
        price_trend = None
        if prev_price is not None and price is not None:
            if price > prev_price:
                price_trend = "UP"
            elif price < prev_price:
                price_trend = "DOWN"
            else:
                price_trend = "FLAT"
        review_count = snapshot.get("review_count")
        prev_review_count = getattr(comp, "last_review_count", None)
        review_velocity = None
        prev_checked_dt = _parse_datetime(prev_checked)
        if review_count is not None and prev_review_count is not None and prev_checked_dt:
            days = (checked_at - prev_checked_dt).total_seconds() / 86400
            if days > 0:
                review_velocity = (review_count - prev_review_count) / days
        competitor_rows.append({
            "name": comp.name,
            "url": comp.url,
            "price": price,
            "gap": gap,
            "prev_price": prev_price,
            "prev_checked": prev_checked,
            "prev_gap": prev_gap,
            "price_trend": price_trend,
            "stock_status": snapshot.get("stock_status"),
            "shipping_estimate": snapshot.get("shipping_estimate"),
            "shipping_days": snapshot.get("shipping_days"),
            "shipping_cost": snapshot.get("shipping_cost"),
            "discount": snapshot.get("discount"),
            "review_count": review_count,
            "review_velocity": review_velocity,
            "warranty_years": snapshot.get("warranty_years"),
        })
        update_competitor(comp.id, last_price=price, last_checked=checked_at)

    summary = build_audit_summary(product, client_price, competitor_rows, checked_at)
    payload = build_audit_payload(product, client_price, competitor_rows, checked_at, client_snapshot=client_snapshot)
    return summary, payload


def run_audit(scraper, product_id):
    summary, _payload = collect_audit_data(scraper, product_id)
    return summary


def _sanitize_pdf_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    cleaned = value.replace("\u2014", "-")
    cleaned = re.sub(r"\\mathbb\{[^}]*\}", "", cleaned)
    cleaned = cleaned.replace("\\mathbb", "")
    cleaned = re.sub(r"\$(?!\d)", "", cleaned)
    return cleaned.encode("latin-1", "replace").decode("latin-1")


def _wrap_pdf_line(pdf, text, max_width):
    if pdf.get_string_width(text) <= max_width:
        return [text]
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        if not current:
            candidate = word
        else:
            candidate = f"{current} {word}"
        if pdf.get_string_width(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if pdf.get_string_width(word) <= max_width:
            current = word
            continue
        # Break long tokens (e.g., URLs) by character width.
        remainder = word
        while remainder:
            chunk = ""
            for i in range(1, len(remainder) + 1):
                candidate = remainder[:i]
                if pdf.get_string_width(candidate) <= max_width:
                    chunk = candidate
                else:
                    break
            if not chunk:
                # Fallback to avoid infinite loop; force a single character.
                chunk = remainder[0]
            lines.append(chunk)
            remainder = remainder[len(chunk):]
    if current:
        lines.append(current)
    return lines


def _render_wrapped_lines(pdf, text, max_width, line_height, link=None):
    for wrapped in _wrap_pdf_line(pdf, text, max_width):
        if link:
            pdf.cell(0, line_height, wrapped, ln=1, link=link)
        else:
            pdf.cell(0, line_height, wrapped, ln=1)


def _ensure_space(pdf, height):
    if pdf.get_y() + height > pdf.h - pdf.b_margin:
        pdf.add_page()


def _section_title(pdf, text, brand_color):
    _ensure_space(pdf, 12)
    pdf.set_text_color(*brand_color)
    pdf.set_font("Times", "B", 13)
    pdf.cell(0, 8, text, ln=1)
    pdf.set_draw_color(*brand_color)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)


def _render_paragraph(pdf, text, max_width, line_height=5):
    safe = _sanitize_pdf_text(text)
    for wrapped in _wrap_pdf_line(pdf, safe, max_width):
        pdf.cell(0, line_height, wrapped, ln=1)


def _render_bullets(pdf, items, max_width, line_height=5):
    for item in items:
        safe = _sanitize_pdf_text(item)
        lines = _wrap_pdf_line(pdf, safe, max_width)
        if not lines:
            continue
        pdf.cell(4, line_height, "-", ln=0)
        pdf.cell(0, line_height, lines[0], ln=1)
        for line in lines[1:]:
            pdf.cell(4, line_height, "", ln=0)
            pdf.cell(0, line_height, line, ln=1)
    pdf.ln(2)


def _is_highlight_gap(row, max_gap_comp=None):
    if not max_gap_comp:
        return False
    return row.get("name") == max_gap_comp.get("name") and row.get("gap") == max_gap_comp.get("gap")


def _build_executive_summary(payload):
    bullets = []
    product_name = payload["product_name"]
    client_price = payload["client_price"]
    closest = payload.get("closest")
    max_gap = payload.get("max_gap_comp")
    biggest_move = payload.get("biggest_move")

    if client_price is not None and payload.get("competitors"):
        prices = [row["price"] for row in payload["competitors"] if row.get("price") is not None]
        if prices and client_price > max(prices):
            gap_val = _format_price_round(closest["gap"]) if closest else PLACEHOLDER_NONE
            comp_name = closest["name"] if closest else "nearest rival"
            qualifier = "significant" if closest and closest.get("gap") and closest["gap"] >= 150 else "meaningful"
            bullets.append(
                f"Price Leader: {product_name} is the most expensive by a {qualifier} margin ({gap_val} above {comp_name})."
            )
        elif prices and client_price < min(prices):
            bullets.append(f"Price Leader: {product_name} is the lowest priced among tracked rivals.")
        else:
            bullets.append(f"Price Position: {product_name} is mid-market among tracked rivals.")

    if max_gap and max_gap.get("gap") is not None:
        status_label = max_gap.get("status", {}).get("label", "Risk")
        bullets.append(
            f"Largest gap: {_format_price_round(max_gap['gap'])} vs {max_gap['name']} ({status_label})."
        )

    if biggest_move and biggest_move.get("prev_price") is not None:
        delta = biggest_move["price"] - biggest_move["prev_price"]
        direction = "down" if delta < 0 else "up"
        bullets.append(
            f"Latest movement: {biggest_move['name']} moved {direction} {_format_price_round(abs(delta))} since last check."
        )

    if payload.get("cheapest") and payload["cheapest"].get("gap") is not None:
        bullets.append(
            f"Priority action: close the {_format_price_round(payload['cheapest']['gap'])} gap versus {payload['cheapest']['name']}."
        )

    return bullets[:5] if bullets else ["No pricing deltas available yet. Run another check for trend visibility."]


def _build_so_what(payload):
    max_gap = payload.get("max_gap_comp")
    if max_gap and max_gap.get("gap") is not None:
        gap_value = _format_price_round(max_gap["gap"])
        return (
            f"The {gap_value} gap between {payload['product_name']} and {max_gap['name']} "
            "is likely driving higher price sensitivity at checkout, especially for ad-driven traffic."
        )
    return "The current price position suggests immediate conversion risk if rivals discount further."


def _build_recommendations(payload):
    cheapest = payload.get("cheapest")
    target_comp = cheapest["name"] if cheapest else "the lowest-priced rival"
    hero_feature = _infer_hero_feature(payload["product_name"])
    marketing_target = None
    for row in payload.get("competitors", []):
        if "hinomi" in row["name"].lower():
            marketing_target = row
            break
    if marketing_target is None:
        marketing_target = cheapest
    premium = None
    if marketing_target and marketing_target.get("gap") is not None:
        premium = marketing_target["gap"]
    premium_hint = _format_premium_hint(premium)
    marketing_gap = (
        premium_hint
        if premium_hint != PLACEHOLDER_NONE
        else _format_price_round(premium) if premium else PLACEHOLDER_NONE
    )
    return [
        f"Tactical: Consider a limited-time price-match bundle to close the {target_comp} advantage.",
        f"Marketing: Refine paid social copy to spotlight {hero_feature} and justify the {marketing_gap} premium "
        f"over {marketing_target['name'] if marketing_target else target_comp}.",
    ]


def _build_data_context(payload):
    notes = [
        "Historical Trends: The Guardian monitor tracks rivals continuously to detect short-term discounting.",
    ]
    cheapest = payload.get("cheapest")
    if cheapest and cheapest.get("price") is not None and payload.get("client_price") is not None:
        delta_if_cut = cheapest["price"] * 0.05
        notes.append(
            f"Profit Risk: A 5% price cut by {cheapest['name']} (~{_format_price_round(delta_if_cut)}) "
            "can reduce conversion efficiency and shift demand toward competitors."
        )
    hero_feature = _infer_hero_feature(payload["product_name"])
    notes.append(
        f"Value Proposition Mapping: {payload['product_name']} is anchored on {hero_feature}; rivals typically position basic lumbar support. "
        "The premium can be defended, but it remains sensitive to widening price gaps."
    )
    return notes


def _render_cover_page(pdf, payload, theme, logo_path):
    pdf.add_page()
    header_height = 20

    logo_x = pdf.l_margin
    logo_y = 6
    logo_w = 24
    if logo_path and os.path.exists(logo_path):
        pdf.image(logo_path, x=logo_x, y=logo_y, w=logo_w)
    else:
        pdf.set_draw_color(*theme["text"])
        pdf.rect(logo_x, logo_y, logo_w, 12)
        pdf.set_text_color(*theme["text"])
        pdf.set_font("Times", size=8)
        pdf.set_xy(logo_x, logo_y + 5)
        pdf.cell(logo_w, 4, "logo.png", align="C")

    pdf.set_draw_color(*theme["accent"])
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, header_height, pdf.w - pdf.r_margin, header_height)

    pdf.set_text_color(*theme["accent"])
    pdf.set_font("Times", "B", 16)
    pdf.set_xy(pdf.l_margin, header_height + 10)
    pdf.cell(0, 8, "VantageFlow Competitive Pricing Report", ln=1)

    pdf.set_text_color(*theme["text"])
    pdf.set_font("Times", "B", 24)
    pdf.cell(0, 12, _sanitize_pdf_text(payload["product_name"]), ln=1)

    pdf.set_text_color(80, 80, 80)
    pdf.set_font("Times", size=11)
    pdf.cell(0, 6, f"Generated {payload['checked_at_label']} (UTC)", ln=1)

    if payload.get("price_position_label"):
        pdf.ln(6)
        pdf.set_font("Times", "B", 13)
        pdf.set_text_color(*theme["accent"])
        pdf.cell(0, 7, _sanitize_pdf_text(payload["price_position_label"]), ln=1)


def _render_price_gap_table(pdf, payload, theme, max_width):
    competitors = payload.get("competitors", [])
    if not competitors:
        _render_paragraph(pdf, "No competitor pricing data available.", max_width)
        return
    name_w = 80.0
    price_w = 40.0
    gap_w = 40.0
    total_w = name_w + price_w + gap_w
    if total_w > max_width:
        scale = max_width / total_w
        name_w *= scale
        price_w *= scale
        gap_w *= scale
        total_w = max_width

    headers = ["Competitor", "Their Price", "Price Gap"]
    line_height = _line_height(10)
    estimated = line_height * (len(competitors) * 2 + 4)
    _ensure_space(pdf, estimated)

    pdf.set_font("Times", "B", 11)
    pdf.set_text_color(*theme["accent"])
    header_y = pdf.get_y()
    header_height = max(
        _estimate_text_height(pdf, headers[0], name_w, line_height),
        _estimate_text_height(pdf, headers[1], price_w, line_height),
        _estimate_text_height(pdf, headers[2], gap_w, line_height),
    )
    _draw_multicell(pdf, pdf.l_margin, header_y, name_w, line_height, headers[0])
    _draw_multicell(pdf, pdf.l_margin + name_w, header_y, price_w, line_height, headers[1], align="R")
    _draw_multicell(pdf, pdf.l_margin + name_w + price_w, header_y, gap_w, line_height, headers[2], align="R")

    pdf.set_draw_color(*theme["accent"])
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, header_y + header_height, pdf.l_margin + total_w, header_y + header_height)
    pdf.set_y(header_y + header_height + 2)

    pdf.set_font("Times", size=10)
    pdf.set_text_color(*theme["text"])
    pdf.set_draw_color(*theme["line"])
    pdf.set_line_width(0.2)
    max_gap_comp = payload.get("max_gap_comp")
    x = pdf.l_margin
    y = pdf.get_y()
    for idx, row in enumerate(competitors):
        fill = theme["row_alt"] if idx % 2 else theme["panel"]
        pdf.set_fill_color(*fill)

        name = _sanitize_pdf_text(row["name"])
        price = _format_price_precise(row.get("price"))
        gap = _format_price_precise(row.get("gap"))

        row_height = max(
            _estimate_text_height(pdf, name, name_w, line_height),
            _estimate_text_height(pdf, price, price_w, line_height),
            _estimate_text_height(pdf, gap, gap_w, line_height),
        )
        pdf.rect(x, y, total_w, row_height, "F")

        _draw_multicell(pdf, x, y, name_w, line_height, name)
        _draw_multicell(pdf, x + name_w, y, price_w, line_height, price, align="R")
        _draw_multicell(pdf, x + name_w + price_w, y, gap_w, line_height, gap, align="R")

        if _is_highlight_gap(row, max_gap_comp):
            pdf.set_draw_color(*theme["accent"])
            pdf.set_line_width(1.4)
            pdf.rect(x + name_w + price_w, y, gap_w, row_height)
            pdf.set_draw_color(*theme["line"])
            pdf.set_line_width(0.2)

        y += row_height

    pdf.set_y(y + 4)


def _setup_mono_font(pdf, base_dir):
    candidates = [
        ("RobotoMono", "RobotoMono-Regular.ttf", "RobotoMono-Bold.ttf"),
        ("SpaceMono", "SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf"),
        ("JetBrainsMono", "JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"),
    ]
    for name, regular_file, bold_file in candidates:
        regular = os.path.join(base_dir, regular_file)
        bold = os.path.join(base_dir, bold_file)
        if os.path.exists(regular):
            pdf.add_font(name, "", regular, uni=True)
            if os.path.exists(bold):
                pdf.add_font(name, "B", bold, uni=True)
            return name
    return "Courier"


def _render_at_a_glance_strip(pdf, payload, theme, x, y, w):
    bar_height = 6
    pdf.set_draw_color(*theme["line"])
    pdf.set_line_width(0.4)
    pdf.rect(x, y, w, bar_height)

    status_line = _status_summary(payload)
    checked_at = payload.get("checked_at")
    date_label = checked_at.strftime("%Y-%m-%d") if isinstance(checked_at, datetime) else PLACEHOLDER_NONE

    pdf.set_text_color(*theme["text"])
    pdf.set_font(theme["font"], "B", 8)
    pdf.set_xy(x + 4, y + 1)
    pdf.cell(0, 4, _sanitize_pdf_text(f"STATUS: {status_line} | DATE: {date_label}"))
    return y + bar_height + 4


def _render_id_strip(pdf, payload, theme, logo_path, x, y, w):
    header_size = 10
    line_height = _line_height(header_size)
    right_indent = _right_indent_mm()
    logo_w = 18
    logo_h = 10
    has_logo = logo_path and os.path.exists(logo_path)
    if has_logo:
        pdf.image(logo_path, x=x, y=y, w=logo_w)

    text_x = x
    text_y = y + (logo_h + 2 if has_logo else 0)
    max_width = w - right_indent
    max_gap = payload.get("max_gap_comp")
    price_delta = _format_signed_precise(max_gap.get("gap")) if max_gap else PLACEHOLDER_NONE
    product_slug = (payload.get("product_name") or "CLIENT").upper().replace(" ", "_")
    header_line = (
        f"VANTAGEFLOW // INTEL_REPORT: {product_slug} "
        f"// PRICE_DELTA: {price_delta}"
    )

    pdf.set_text_color(*theme["text"])
    pdf.set_font(theme["font"], "B", header_size)
    pdf.set_xy(text_x, text_y)
    _draw_multicell(pdf, text_x, text_y, max_width, line_height, _sanitize_pdf_text(header_line))

    checked_at = payload.get("checked_at")
    timestamp = checked_at.strftime("%Y-%m-%d %H:%M UTC") if isinstance(checked_at, datetime) else PLACEHOLDER_NONE
    guardian_line = f"SCAN_COMPLETED: {timestamp} // NODE: US-EAST-1"
    pdf.set_font(theme["font"], size=9)
    _draw_multicell(pdf, text_x, pdf.get_y(), max_width, _line_height(9), _sanitize_pdf_text(guardian_line))

    return pdf.get_y() + 6


def _draw_module_box(pdf, x, y, w, h, theme, title):
    pdf.set_draw_color(*theme["line"])
    pdf.set_line_width(0.4)
    pdf.rect(x, y, w, h)
    pdf.set_text_color(*theme["text"])
    pdf.set_font(theme["font"], "B", 9)
    pdf.set_xy(x + 6, y + 4)
    right_indent = _right_indent_mm()
    max_width = w - 6 - right_indent
    safe_title = _truncate_text(pdf, _sanitize_pdf_text(title), max_width)
    pdf.cell(0, 4, safe_title)


def _render_competition_box(pdf, box, theme, x, y, w, h):
    pdf.set_draw_color(*theme["line"])
    pdf.set_line_width(0.4)
    pdf.rect(x, y, w, h)

    padding = 5
    right_indent = _right_indent_mm()
    cursor_y = y + 4
    title_size = 9
    body_size = 9
    line_height = _line_height(body_size)
    max_width = w - padding * 2 - right_indent

    pdf.set_text_color(*theme["text"])
    pdf.set_font(theme["font"], "B", title_size)
    title = _sanitize_pdf_text(box.get("name") or "")
    trend = box.get("trend")
    if trend:
        title = f"{title} [{trend}]"
    title_width = min(80.0, max_width)
    title_height = _draw_multicell(pdf, x + padding, cursor_y, title_width, line_height, title)
    cursor_y += title_height + 1

    price = _format_price_precise(box.get("price"))
    gap = _format_signed_precise(box.get("gap"))
    price_label = f"PRICE: {price}"

    pdf.set_font(theme["font"], size=body_size)
    pdf.set_text_color(*theme["text"])

    if box.get("show_gap", True):
        gap_label = f"GAP: {gap}"
        target_price_w = 40.0
        target_gap_w = 40.0
        gutter = 4.0
        if target_price_w + target_gap_w + gutter > max_width:
            available = max_width - gutter
            if available <= 0:
                price_width = max_width / 2
                gap_width = max_width / 2
                gutter = 0
            else:
                scale = available / (target_price_w + target_gap_w)
                price_width = target_price_w * scale
                gap_width = target_gap_w * scale
        else:
            price_width = target_price_w
            gap_width = target_gap_w

        row_height = max(
            _estimate_text_height(pdf, price_label, price_width, line_height),
            _estimate_text_height(pdf, gap_label, gap_width, line_height),
        )

        pdf.set_xy(x + padding, cursor_y)
        _draw_multicell(pdf, x + padding, cursor_y, price_width, line_height, price_label)

        gap_x = x + padding + price_width + gutter
        if box.get("highlight"):
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(gap_x, cursor_y - 1, gap_width, row_height + 2, "F")
            pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_text_color(*theme["text"])
        _draw_multicell(pdf, gap_x, cursor_y, gap_width, line_height, gap_label)
        pdf.set_text_color(*theme["text"])
        cursor_y += row_height + 1.5
    else:
        used = _draw_multicell(pdf, x + padding, cursor_y, max_width, line_height, price_label)
        cursor_y += used + 1.5

    if box.get("show_details", True):
        stock = box.get("stock_status")
        if _is_blank(stock):
            stock = PLACEHOLDER_MANUAL
        shipping = _format_shipping_label(box)
        ship_cost = box.get("shipping_cost")
        ship_cost_label = PLACEHOLDER_NONE if ship_cost is None else ("FREE" if ship_cost == 0 else _format_price_precise(ship_cost))
        stock_line = f"Stock: {stock}"
        ship_line = f"Delivery: {shipping}"
        ship_cost_line = f"Ship Cost: {ship_cost_label}"
        promo = box.get("discount")
        if _is_blank(promo):
            promo = PLACEHOLDER_PENDING
        promo_line = f"Promo: {promo}"
        reviews_line = _format_review_line(box)

        pdf.set_font(theme["font"], size=body_size - 0.5)
        for line in (stock_line, ship_line, ship_cost_line, promo_line, reviews_line):
            if cursor_y + line_height > y + h - 3:
                break
            used = _draw_multicell(pdf, x + padding, cursor_y, max_width, line_height, _sanitize_pdf_text(line))
            cursor_y += used


def _render_competitor_cards(pdf, payload, theme, x, y, w, h):
    boxes = _build_competition_boxes(payload)
    gutter = 6
    card_height = (h - gutter) / 2
    for idx, box in enumerate(boxes):
        card_y = y + idx * (card_height + gutter)
        _render_competition_box(pdf, box, theme, x, card_y, w, card_height)


def _render_client_pulse(pdf, payload, theme, x, y, w, h):
    box = {
        "name": "CLIENT PRICE",
        "price": payload.get("client_price"),
        "gap": None,
        "stock_status": None,
        "shipping_estimate": None,
        "shipping_days": None,
        "discount": None,
        "review_count": None,
        "review_velocity": None,
        "highlight": False,
        "show_details": False,
        "show_gap": False,
    }
    _render_competition_box(pdf, box, theme, x, y, w, h)


def _render_competitor_snapshots(pdf, payload, theme, x, y, w, h):
    _draw_module_box(pdf, x, y, w, h, theme, "COMPETITOR SNAPSHOTS")
    rows = _top_competitor_rows(payload, limit=2)
    max_gap_comp = payload.get("max_gap_comp")

    pdf.set_font(theme["font"], size=9)
    line_height = 8
    right_indent = _right_indent_mm()
    inner_w = w - 12 - right_indent
    name_w = 80.0
    gap_w = 40.0
    total_w = name_w + gap_w
    if total_w > inner_w:
        scale = inner_w / total_w if total_w else 1.0
        name_w *= scale
        gap_w *= scale
    cursor_y = y + 16
    for row in rows:
        name = _sanitize_pdf_text(row.get("name") or "Unknown")
        price = _format_price_precise(row.get("price"))
        gap = row.get("gap")
        if gap is None:
            gap = _compute_gap(payload.get("client_price"), row.get("price"))
        gap_text = f"Gap: {_format_signed_precise(gap)}"
        name_text = f"{name}: {price}"

        row_height = max(
            _estimate_text_height(pdf, name_text, name_w, line_height),
            _estimate_text_height(pdf, gap_text, gap_w, line_height),
        )

        pdf.set_text_color(*theme["text"])
        _draw_multicell(pdf, x + 6, cursor_y, name_w, line_height, name_text)

        gap_x = x + 6 + name_w
        if _is_highlight_gap(row, max_gap_comp):
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(gap_x, cursor_y - 1, gap_w, row_height + 2, "F")
            pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_text_color(*theme["text"])

        _draw_multicell(pdf, gap_x, cursor_y, gap_w, line_height, gap_text)
        pdf.set_text_color(*theme["text"])

        cursor_y += row_height + 2


def _render_leak_analysis(pdf, payload, theme, x, y, w, h):
    _draw_module_box(pdf, x, y, w, h, theme, "LEAK ANALYSIS")
    max_gap = payload.get("max_gap_comp")
    summary = []

    if max_gap and max_gap.get("gap") is not None:
        gap_value = _format_price_precise(max_gap["gap"])
        comp_name = _sanitize_pdf_text(max_gap.get("name") or "a primary competitor")
        summary.append(
            f"The price gap of {gap_value} vs {comp_name} is big enough to cause drop-off "
            "when shoppers compare tabs."
        )
        summary.append(
            "Big gaps hit hardest at the comparison step, right before checkout."
        )
    else:
        summary.append("Insufficient pricing data to assess comparison friction risk.")

    if max_gap and max_gap.get("gap") is not None:
        daily_leak, monthly_leak, rate = _estimate_ad_leak_from_gap(max_gap.get("gap"))
        if rate is not None:
            summary.append(f"Defection estimate: {rate * 100:.0f}% at the current gap.")
        if monthly_leak is not None and monthly_leak > 0:
            summary.append(
                f"Estimated Monthly Ad Leak: {_format_price_precise(monthly_leak)}. "
                "Your current gap is helping rivals win customers."
            )

    rows = _build_competition_boxes(payload)
    for row in rows:
        stock = row.get("stock_status")
        shipping = _format_shipping_label(row)
        promo = row.get("discount")
        if stock and not _is_placeholder_text(stock) and stock.lower().startswith("out"):
            summary.append(
                f"{row.get('name')} is out of stock, which lowers price pressure."
            )
        if shipping and not _is_placeholder_text(shipping):
            summary.append(f"{row.get('name')} shipping estimate: {shipping}.")
        if promo and not _is_placeholder_text(promo):
            summary.append(f"{row.get('name')} promo found: {promo}.")

    client_warranty = payload.get("client_warranty_years")
    if max_gap and max_gap.get("warranty_years") and client_warranty:
        diff = client_warranty - max_gap.get("warranty_years")
        if diff > 0:
            summary.append(f"Premium supported by +{diff}yr warranty advantage.")
        elif diff < 0:
            summary.append(f"Warranty gap: {abs(diff)}yr shorter than {max_gap.get('name')}.")

    if max_gap and max_gap.get("review_velocity") is not None:
        velocity = max_gap.get("review_velocity")
        if velocity >= 5:
            velocity_label = "High"
        elif velocity >= 2:
            velocity_label = "Moderate"
        else:
            velocity_label = "Low"
        summary.append(
            f"Rival velocity: {velocity_label}. {max_gap.get('name')} adds ~{velocity:.0f} reviews/day."
        )

    client_ship = payload.get("client_shipping_cost")
    if max_gap and max_gap.get("shipping_cost") is not None and client_ship is not None:
        true_delta = _compute_gap(
            (payload.get("client_price") or 0) + client_ship,
            (max_gap.get("price") or 0) + max_gap.get("shipping_cost")
        )
        summary.append(
            f"True delta after shipping: {_format_signed_precise(true_delta)}."
        )

    velocity = _market_velocity_summary(payload)
    summary.append(velocity)

    body_size = 9
    line_height = _line_height(body_size)
    right_indent = _right_indent_mm()
    max_width = w - 12 - right_indent

    recommendation = "Recommendation: Maintain guardrails while Guardian continues daily monitoring."
    if max_gap and max_gap.get("gap") is not None and max_gap.get("gap") > 200:
        hinomi_price = None
        for row in payload.get("competitors", []):
            if "hinomi" in (row.get("name") or "").lower():
                hinomi_price = row.get("price")
                break
        hinomi_price_label = "$599.00"
        if hinomi_price is not None:
            hinomi_price_label = _format_price_precise(hinomi_price)
            if hinomi_price_label == PLACEHOLDER_SCAN:
                hinomi_price_label = "$599.00"
        recommendation = (
            "Recommendation: Implement a 48h Price Shield to match Hinomi's current "
            f"{hinomi_price_label} position"
        )

    rec_title = "STRATEGIC RECOMMENDATION"
    rec_title_size = 8
    rec_padding = 3
    pdf.set_font(theme["font"], "B", rec_title_size)
    rec_title_height = _line_height(rec_title_size)
    pdf.set_font(theme["font"], size=body_size)
    rec_body_height = _estimate_text_height(pdf, recommendation, max_width - rec_padding * 2, line_height)
    rec_box_height = rec_title_height + rec_body_height + rec_padding * 2 + 2
    rec_box_x = x + 6
    rec_box_y = y + h - rec_box_height - 6
    if rec_box_y < y + 20:
        rec_box_y = y + 20

    pdf.set_font(theme["font"], size=body_size)
    pdf.set_xy(x + 6, y + 16)
    summary_limit = rec_box_y - 2
    for statement in summary:
        if pdf.get_y() + line_height > summary_limit:
            break
        if "Estimated Monthly Ad Leak" in statement:
            pdf.set_text_color(*theme["accent"])
        else:
            pdf.set_text_color(*theme["text"])
        used = _draw_multicell(pdf, x + 6, pdf.get_y(), max_width, line_height, _sanitize_pdf_text(statement))
        if used == 0:
            pdf.ln(line_height)

    pdf.set_draw_color(*theme["accent"])
    pdf.set_line_width(0.6)
    pdf.rect(rec_box_x, rec_box_y, max_width, rec_box_height)
    pdf.set_text_color(*theme["accent"])
    pdf.set_font(theme["font"], "B", rec_title_size)
    _draw_multicell(
        pdf,
        rec_box_x + rec_padding,
        rec_box_y + rec_padding,
        max_width - rec_padding * 2,
        rec_title_height,
        _sanitize_pdf_text(rec_title),
    )
    pdf.set_text_color(*theme["text"])
    pdf.set_font(theme["font"], size=body_size)
    _draw_multicell(
        pdf,
        rec_box_x + rec_padding,
        rec_box_y + rec_padding + rec_title_height,
        max_width - rec_padding * 2,
        line_height,
        _sanitize_pdf_text(recommendation),
    )


def _render_market_velocity(pdf, payload, theme, x, y, w, h):
    _draw_module_box(pdf, x, y, w, h, theme, "MARKET VELOCITY")
    summary = _market_velocity_summary(payload)
    pdf.set_text_color(*theme["text"])
    body_size = 9
    line_height = _line_height(body_size)
    right_indent = _right_indent_mm()
    pdf.set_font(theme["font"], size=body_size)
    pdf.set_xy(x + 6, y + 16)
    max_width = w - 12 - right_indent
    _draw_multicell(pdf, x + 6, y + 16, max_width, line_height, _sanitize_pdf_text(summary))


def _render_footer(pdf, theme, x, y, w):
    text = (
        "Automated price-match triggers are available for your Shopify store. "
        "Contact VantageFlow to enable Shield Mode."
    )
    size = 8
    line_height = _line_height(size)
    pdf.set_font(theme["font"], size=size)
    pdf.set_text_color(*theme["text"])
    _draw_multicell(pdf, x, y, w - _right_indent_mm(), line_height, _sanitize_pdf_text(text))


def _render_war_room_layout(pdf, payload, theme, logo_path):
    pdf.add_page()
    available_width = pdf.w - pdf.l_margin - pdf.r_margin
    content_width = available_width
    content_x = pdf.l_margin + (available_width - content_width) / 2
    cursor_y = pdf.t_margin

    cursor_y = _render_id_strip(pdf, payload, theme, logo_path, content_x, cursor_y, content_width)

    gutter = 6
    page_bottom = pdf.h - pdf.b_margin
    footer_space = _line_height(8) * 1.6

    module_height = 44
    _render_client_pulse(pdf, payload, theme, content_x, cursor_y, content_width, module_height)
    cursor_y += module_height + gutter

    _render_market_velocity(pdf, payload, theme, content_x, cursor_y, content_width, module_height)
    cursor_y += module_height

    cursor_y += 15
    pdf.set_xy(content_x, cursor_y)

    cards_height = _estimate_competitor_cards_height(pdf, payload, theme, content_width, gutter)
    _render_competitor_cards(pdf, payload, theme, content_x, cursor_y, content_width, cards_height)
    cursor_y += cards_height + gutter

    bottom_height = page_bottom - footer_space - cursor_y
    if bottom_height < 40:
        bottom_height = 40
        cursor_y = max(cursor_y, page_bottom - footer_space - bottom_height)

    _render_leak_analysis(pdf, payload, theme, content_x, cursor_y, content_width, bottom_height)

    footer_y = page_bottom - footer_space + _line_height(8) * 0.1
    _render_footer(pdf, theme, content_x, footer_y, content_width)

def _draw_price_delta_chart(pdf, payload, max_width):
    chart_height = 45
    _ensure_space(pdf, chart_height + 16)

    chart_comp = payload.get("max_gap_comp") or payload.get("cheapest") or None
    pdf.set_font("Times", "B", 11)
    pdf.set_text_color(20, 20, 20)
    title = "Price Delta Over Time"
    if chart_comp:
        title = f"Price Delta Over Time - {chart_comp['name']}"
    pdf.cell(0, 6, _sanitize_pdf_text(title), ln=1)

    x = pdf.l_margin
    y = pdf.get_y() + 4
    w = max_width
    h = chart_height
    pdf.set_draw_color(200, 200, 200)
    pdf.rect(x, y, w, h)
    pdf.set_draw_color(80, 80, 80)
    pdf.line(x, y + h, x + w, y + h)
    pdf.line(x, y, x, y + h)

    if chart_comp:
        prev_gap = chart_comp.get("prev_gap")
        current_gap = chart_comp.get("gap")
        if prev_gap is None:
            prev_gap = current_gap if current_gap is not None else 0.0
        if current_gap is None:
            current_gap = prev_gap if prev_gap is not None else 0.0
        series = [
            (_format_checked_label(chart_comp.get("prev_checked")), prev_gap),
            ("Now", current_gap),
        ]
    else:
        series = [("Now", 0.0)]

    values = [point[1] for point in series]
    min_val = min(values) if values else 0.0
    max_val = max(values) if values else 0.0
    if min_val == max_val:
        min_val -= 1
        max_val += 1
    span = max_val - min_val

    pdf.set_draw_color(30, 120, 120)
    pdf.set_fill_color(30, 120, 120)
    prev_point = None
    for idx, (label, value) in enumerate(series):
        if len(series) > 1:
            px = x + (idx / (len(series) - 1)) * w
        else:
            px = x + w * 0.5
        py = y + h - ((value - min_val) / span) * h
        if prev_point:
            pdf.line(prev_point[0], prev_point[1], px, py)
        pdf.ellipse(px - 1.2, py - 1.2, 2.4, 2.4, style="F")
        prev_point = (px, py)

        pdf.set_font("Times", size=8)
        pdf.set_text_color(60, 60, 60)
        pdf.set_xy(px - 10, y + h + 1.5)
        pdf.cell(20, 4, _sanitize_pdf_text(label), align="C")

    pdf.set_font("Times", size=8)
    pdf.set_text_color(80, 80, 80)
    pdf.set_xy(x + 2, y + 2)
    pdf.cell(0, 4, f"Max: {_format_price_round(max_val)}")
    pdf.set_xy(x + 2, y + h - 5)
    pdf.cell(0, 4, f"Min: {_format_price_round(min_val)}")
    pdf.set_y(y + h + 10)


def _render_executive_summary(pdf, payload, max_width, brand_color):
    _section_title(pdf, "Executive Summary", brand_color)
    pdf.set_font("Times", size=11)
    pdf.set_text_color(30, 30, 30)
    bullets = _build_executive_summary(payload)
    _render_bullets(pdf, bullets, max_width)


def _render_so_what(pdf, payload, max_width, brand_color):
    _section_title(pdf, "Implications", brand_color)
    pdf.set_font("Times", size=11)
    pdf.set_text_color(30, 30, 30)
    _render_paragraph(pdf, _build_so_what(payload), max_width)
    pdf.ln(2)


def _render_recommendations(pdf, payload, max_width, brand_color):
    _section_title(pdf, "Recommended Actions", brand_color)
    pdf.set_font("Times", size=11)
    pdf.set_text_color(30, 30, 30)
    _render_bullets(pdf, _build_recommendations(payload), max_width)


def _render_data_context(pdf, payload, max_width, brand_color):
    _section_title(pdf, "Data Context and ROI", brand_color)
    pdf.set_font("Times", size=11)
    pdf.set_text_color(30, 30, 30)
    _render_bullets(pdf, _build_data_context(payload), max_width)


def _write_summary_pdf(summary, output_path):
    if FPDF is None:
        raise RuntimeError("Missing dependency: install fpdf2 to write PDF output.")
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    font_name = "Times"
    font_size = 12
    line_height = 6
    url_color = (0, 102, 204)
    url_pattern = re.compile(r"(https?://\S+)")
    pdf.set_font(font_name, size=font_size)
    max_width = pdf.w - pdf.l_margin - pdf.r_margin
    for line in summary.splitlines():
        safe_line = _sanitize_pdf_text(line)
        if not safe_line.strip():
            pdf.ln(line_height)
            continue
        has_url = url_pattern.search(safe_line)
        if not has_url:
            pdf.set_text_color(0, 0, 0)
            pdf.set_font(font_name, size=font_size)
            _render_wrapped_lines(pdf, safe_line, max_width, line_height)
            continue
        cursor = 0
        for match in url_pattern.finditer(safe_line):
            pre = safe_line[cursor:match.start()]
            if pre.strip():
                pdf.set_text_color(0, 0, 0)
                pdf.set_font(font_name, size=font_size)
                _render_wrapped_lines(pdf, pre.strip(), max_width, line_height)
            raw_url = match.group(0)
            trailing = ""
            while raw_url and raw_url[-1] in ".,);]}>":
                trailing = raw_url[-1] + trailing
                raw_url = raw_url[:-1]
            if raw_url:
                pdf.set_text_color(*url_color)
                pdf.set_font(font_name, style="U", size=font_size)
                _render_wrapped_lines(pdf, raw_url, max_width, line_height, link=raw_url)
            if trailing:
                pdf.set_text_color(0, 0, 0)
                pdf.set_font(font_name, size=font_size)
                _render_wrapped_lines(pdf, trailing, max_width, line_height)
            cursor = match.end()
        tail = safe_line[cursor:]
        if tail.strip():
            pdf.set_text_color(0, 0, 0)
            pdf.set_font(font_name, size=font_size)
            _render_wrapped_lines(pdf, tail.strip(), max_width, line_height)
    pdf.output(output_path)


def write_audit_pdf(audit_payload, output_path, logo_path="vantage-flow-logo.png"):
    if FPDF is None:
        raise RuntimeError("Missing dependency: install fpdf2 to write PDF output.")
    if isinstance(audit_payload, str):
        return _write_summary_pdf(audit_payload, output_path)

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    if logo_path and not os.path.isabs(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), logo_path)

    theme = {
        "background": (5, 10, 14),
        "text": (255, 255, 255),
        "accent": (0, 235, 255),
        "line": (51, 51, 51),
        "panel": (18, 18, 18),
        "row_alt": (22, 22, 22),
        "logo_path": logo_path,
        "font": "Courier",
    }

    pdf = VantagePDF(theme)
    theme["font"] = _setup_mono_font(pdf, os.path.dirname(__file__))
    safe_margin = 12.7
    pdf.set_auto_page_break(auto=True, margin=safe_margin)
    pdf.set_margins(safe_margin, safe_margin, safe_margin)

    _render_war_room_layout(pdf, audit_payload, theme, logo_path)

    pdf.output(output_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a pricing audit and print a summary.")
    parser.add_argument("--product-id", type=int, required=True)
    parser.add_argument(
        "--pdf",
        nargs="?",
        const="auto",
        help="Write a PDF summary. Optionally pass a file path; defaults to auto-named file in cwd.",
    )
    args = parser.parse_args()

    scraper = PriceScraper()
    summary, payload = collect_audit_data(scraper, args.product_id)
    print(summary)
    if args.pdf:
        if args.pdf == "auto":
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            output_path = f"audit_{args.product_id}_{timestamp}.pdf"
        else:
            output_path = args.pdf
        write_audit_pdf(payload, output_path)
        print(f"PDF written to {output_path}")
