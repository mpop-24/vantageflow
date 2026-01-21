from sqlmodel import Session, select
from models import ClientProduct
from slack_ui import build_product_select, build_competitors_view


def handle_prices_command(engine, team_id=None):
    with Session(engine) as session:
        query = select(ClientProduct)
        if team_id:
            query = query.where(ClientProduct.slack_team_id == team_id)
        products = session.exec(query).all()
    return build_product_select(products)


def handle_product_selected(engine, product_id):
    with Session(engine) as session:
        product = session.get(ClientProduct, product_id)
        if not product:
            return {
                "response_action": "update",
                "text": "Product not found.",
            }
        return build_competitors_view(product)
