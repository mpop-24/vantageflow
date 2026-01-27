from main import PriceScraper
from slack_ui import build_product_select, build_competitors_view, build_all_products_view
from supabase_db import list_client_products, get_client_product

_scraper = PriceScraper()


def handle_prices_command(team_id=None):
    products = list_client_products(team_id)
    return build_product_select(products)


def handle_all_products_command(team_id=None):
    products = list_client_products(team_id)
    product_rows = []
    for product in products:
        client_price = _scraper.get_price(product.base_url)
        product_rows.append({"product": product, "client_price": client_price})
    return build_all_products_view(product_rows)


def handle_product_selected(product_id):
    product = get_client_product(product_id)
    if not product:
        return {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": "Product not found.",
        }
    client_price = _scraper.get_price(product.base_url)
    return build_competitors_view(product, client_price)
