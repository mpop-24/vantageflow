from datetime import datetime
from sqlmodel import Session

from db import get_engine
from main import PriceScraper
from models import ClientProduct


def _format_price(value):
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _format_signed(value):
    if value is None:
        return "n/a"
    sign = ""
    if value > 0:
        sign = "+"
    elif value < 0:
        sign = "-"
    return f"{sign}${abs(value):.2f}"


def build_audit_summary(product, client_price, competitor_rows, checked_at):
    lines = [
        f"Audit: {product.product_name}",
        f"Checked: {checked_at.isoformat()}Z",
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
                f" — {row['url']}"
            )

    return "\n".join(lines)


def run_audit(engine, scraper, product_id):
    checked_at = datetime.utcnow()
    with Session(engine) as session:
        product = session.get(ClientProduct, product_id)
        if not product:
            raise RuntimeError("Product not found")

        client_price = scraper.get_price(product.base_url)
        competitor_rows = []

        for comp in product.competitors:
            price = scraper.get_price(comp.url)
            if price is None:
                continue
            competitor_rows.append({
                "name": comp.name,
                "url": comp.url,
                "price": price,
            })
            comp.last_price = price
            comp.last_checked = checked_at
            session.add(comp)

        session.commit()

    return build_audit_summary(product, client_price, competitor_rows, checked_at)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a pricing audit and print a summary.")
    parser.add_argument("--product-id", type=int, required=True)
    args = parser.parse_args()

    engine = get_engine()
    scraper = PriceScraper()
    summary = run_audit(engine, scraper, args.product_id)
    print(summary)
