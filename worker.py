from datetime import datetime
import logging

from supabase_db import list_client_products, update_competitor

logger = logging.getLogger(__name__)


def _prices_changed(old_price, new_price):
    if new_price is None:
        return False
    if old_price is None:
        return True
    return old_price != new_price


def check_all_prices(scraper, alert_fn):
    products = list_client_products()

    for product in products:
        client_price = scraper.get_price(product.base_url)
        if client_price is None:
            logger.warning(
                "Client price unavailable for %s (%s)",
                product.product_name,
                product.base_url,
            )

        for comp in product.competitors:
            current_comp_price = scraper.get_price(comp.url)
            if current_comp_price is None:
                logger.warning(
                    "Competitor price unavailable for %s (%s)",
                    comp.name,
                    comp.url,
                )
                continue

            now = datetime.utcnow()
            if _prices_changed(comp.last_price, current_comp_price):
                try:
                    alert_fn(
                        channel=product.slack_channel_id,
                        product_name=product.product_name,
                        comp_name=comp.name,
                        old_p=comp.last_price,
                        new_p=current_comp_price,
                        client_p=client_price,
                        competitor_url=comp.url,
                        product_url=product.base_url,
                    )
                except Exception:
                    logger.exception(
                        "Failed to send alert for %s (%s)",
                        comp.name,
                        comp.url,
                    )
                update_competitor(
                    comp.id,
                    last_price=current_comp_price,
                    last_checked=now,
                )
            else:
                logger.info(
                    "No change for %s (%s): %s",
                    comp.name,
                    comp.url,
                    current_comp_price,
                )
                update_competitor(comp.id, last_checked=now)
