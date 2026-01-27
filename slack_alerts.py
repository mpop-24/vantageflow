from slack_client import send_slack_message
from slack_ui import build_price_alert_message


def send_price_alert(
    channel,
    product_name,
    comp_name,
    old_p,
    new_p,
    client_p=None,
    competitor_url=None,
    product_url=None,
):
    message = build_price_alert_message(
        product_name=product_name,
        comp_name=comp_name,
        old_p=old_p,
        new_p=new_p,
        client_p=client_p,
        competitor_url=competitor_url,
        product_url=product_url,
    )
    return send_slack_message(
        channel=channel,
        text=message["text"],
        attachments=message.get("attachments"),
    )
