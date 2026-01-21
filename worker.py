from sqlmodel import Session, select
from models import ClientProduct


def check_all_prices(engine, scraper, alert_fn):
    with Session(engine) as session:
        products = session.exec(select(ClientProduct)).all()

        for product in products:
            client_price = scraper.get_price(product.base_url)

            for comp in product.competitors:
                current_comp_price = scraper.get_price(comp.url)
                if current_comp_price is None:
                    continue

                if comp.last_price != current_comp_price:
                    alert_fn(
                        channel=product.slack_channel_id,
                        product_name=product.product_name,
                        comp_name=comp.name,
                        old_p=comp.last_price,
                        new_p=current_comp_price,
                        client_p=client_price,
                    )
                    comp.last_price = current_comp_price
                    session.add(comp)

        session.commit()
