from slack_ui import build_product_select, build_competitors_view
from supabase_db import list_client_products, get_client_product


def handle_prices_command(team_id=None):
    products = list_client_products(team_id)
    return build_product_select(products)


def handle_product_selected(product_id):
    product = get_client_product(product_id)
    if not product:
        return {
            "response_type": "ephemeral",
            "replace_original": True,
            "text": "Product not found.",
        }
    return build_competitors_view(product)
