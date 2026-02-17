from datetime import datetime
import json
import logging
import os

from supabase_db import list_client_products, update_competitor, update_client_product

logger = logging.getLogger(__name__)
DEFAULT_STATE_FILE = ".guardian_state.json"
STATE_KEY = "initial_alert_channels_by_product"


def _prices_changed(old_price, new_price):
    if new_price is None:
        return False
    if old_price is None:
        return True
    return old_price != new_price


def _state_file_path():
    return os.getenv("GUARDIAN_STATE_FILE", DEFAULT_STATE_FILE)


def _load_initial_alert_state():
    path = _state_file_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Failed to load guardian state from %s", path)
        return {}

    if not isinstance(payload, dict):
        return {}

    rows = payload.get(STATE_KEY, {})
    if not isinstance(rows, dict):
        return {}

    normalized = {}
    for product_id, channel_id in rows.items():
        key = str(product_id).strip()
        if not key or not isinstance(channel_id, str) or not channel_id.strip():
            continue
        normalized[key] = channel_id.strip()
    return normalized


def _save_initial_alert_state(state):
    path = _state_file_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {STATE_KEY: state}
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.replace(temp_path, path)
    except Exception:
        logger.exception("Failed to save guardian state to %s", path)


def _initial_alert_reason(product, state):
    channel = (product.slack_channel_id or "").strip()
    if not channel:
        return None
    key = str(product.id)
    previous_channel = state.get(key)
    if previous_channel is None:
        return "new_product"
    if previous_channel != channel:
        return "channel_changed"
    return None


def check_all_prices(scraper, alert_fn, initial_alert_fn=None):
    products = list_client_products()
    initial_alert_state = _load_initial_alert_state() if initial_alert_fn else {}
    state_changed = False
    seen_product_ids = set()

    for product in products:
        key = str(product.id)
        seen_product_ids.add(key)
        client_price = scraper.get_price(product.base_url)
        if client_price is None:
            logger.warning(
                "Client price unavailable for %s (%s)",
                product.product_name,
                product.base_url,
            )
        else:
            update_client_product(product.id, client_price=client_price)

        reason = _initial_alert_reason(product, initial_alert_state) if initial_alert_fn else None
        if reason:
            try:
                initial_alert_fn(
                    channel=product.slack_channel_id,
                    product_name=product.product_name,
                    reason=reason,
                    client_p=client_price,
                    competitor_count=len(product.competitors),
                    product_url=product.base_url,
                )
            except Exception:
                logger.exception(
                    "Failed to send initial monitoring alert for %s (%s)",
                    product.product_name,
                    product.base_url,
                )
            else:
                initial_alert_state[key] = product.slack_channel_id.strip()
                state_changed = True

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

    if initial_alert_fn:
        stale_keys = [key for key in initial_alert_state.keys() if key not in seen_product_ids]
        if stale_keys:
            for key in stale_keys:
                initial_alert_state.pop(key, None)
            state_changed = True
        if state_changed:
            _save_initial_alert_state(initial_alert_state)
