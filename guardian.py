import logging
import os
import time

from db import get_engine
from main import PriceScraper
from slack_alerts import send_price_alert
from worker import check_all_prices


def _interval_seconds():
    value = os.getenv("CHECK_INTERVAL_HOURS", "4")
    try:
        hours = float(value)
    except ValueError:
        hours = 4.0
    return max(hours, 0.1) * 3600


def run_once():
    engine = get_engine()
    scraper = PriceScraper()
    check_all_prices(engine, scraper, send_price_alert)


def run_forever():
    interval = _interval_seconds()
    while True:
        run_once()
        time.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mode = os.getenv("GUARDIAN_MODE", "once").lower()
    if mode == "forever":
        run_forever()
    else:
        run_once()
