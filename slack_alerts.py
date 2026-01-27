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
    if new_p is None:
        text = f"PRICE CHANGE | {comp_name} price updated."
    elif client_p is None:
        text = f"PRICE CHANGE | {comp_name} is now {_format_price(new_p)}."
    else:
        diff = client_p - new_p
        if diff > 0:
            insight = f"You are {_format_price(diff)} more expensive than them."
        elif diff < 0:
            insight = f"You are {_format_price(abs(diff))} cheaper than them."
        else:
            insight = "You are price-matched."
        text = f"Price Change! {comp_name} is now {_format_price(new_p)}. {insight}"
    return send_slack_message(channel=channel, text=text)
