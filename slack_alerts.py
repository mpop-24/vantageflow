from slack_client import send_slack_message


def _format_price(value):
    if value is None:
        return "n/a"
    return f"${value:.2f}"


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
    if client_p is None or new_p is None:
        text = f"Price Change! {comp_name} is now {_format_price(new_p)}."
    else:
        diff = client_p - new_p
        text = (
            f"Price Change! {comp_name} is now {_format_price(new_p)}. "
            f"You are {_format_price(diff)} more expensive than them."
        )
    return send_slack_message(channel=channel, text=text)
