def _format_price(value):
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _format_signed(value):
    if value is None:
        return "n/a"
    sign = ""
    if value > 0:
        sign = "+"
    elif value < 0:
        sign = "-"
    return f"{sign}${abs(value):.2f}"


def build_price_alert_message(
    product_name,
    comp_name,
    old_p,
    new_p,
    client_p=None,
    competitor_url=None,
):
    if old_p is None:
        change_word = "updated"
    elif new_p < old_p:
        change_word = "dropped"
    elif new_p > old_p:
        change_word = "rose"
    else:
        change_word = "updated"

    gap = None
    if client_p is not None and new_p is not None:
        gap = client_p - new_p

    headline = f"⚠️ Alert: {comp_name} {change_word} to {_format_price(new_p)}"
    details = f"Was {_format_price(old_p)} → Now {_format_price(new_p)}"
    if client_p is not None:
        details += f"\nYour price: {_format_price(client_p)} • Gap: {_format_signed(gap)}"

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{product_name}* competitor update\n{details}",
            },
        }
    ]

    if competitor_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Product"},
                        "url": competitor_url,
                    }
                ],
            }
        )

    return {
        "text": headline,
        "blocks": blocks,
    }


def build_product_select(products):
    if not products:
        return {
            "response_type": "ephemeral",
            "text": "No products configured yet.",
        }

    options = []
    for product in products:
        options.append(
            {
                "text": {"type": "plain_text", "text": product.product_name},
                "value": str(product.id),
            }
        )

    return {
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Which product do you want to audit?*",
                },
            },
            {
                "type": "actions",
                "block_id": "prices_product_select",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": "prices_select_product",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select a product",
                        },
                        "options": options,
                    }
                ],
            },
        ],
    }


def build_competitors_view(product):
    if not product.competitors:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{product.product_name}*\nNo competitors configured.",
                },
            }
        ]
    else:
        lines = []
        for comp in product.competitors:
            lines.append(f"• *{comp.name}* — {_format_price(comp.last_price)}")
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{product.product_name}* Competitors\n\n" + "\n".join(lines),
                },
            }
        ]

    return {
        "response_type": "ephemeral",
        "replace_original": True,
        "text": f"{product.product_name} competitors",
        "blocks": blocks,
    }


def build_all_products_view(products):
    if not products:
        return {
            "response_type": "ephemeral",
            "text": "No products configured yet.",
        }

    lines = []
    for product in products:
        lines.append(f"*{product.product_name}*")
        if not product.competitors:
            lines.append("• No competitors configured.")
            continue
        for comp in product.competitors:
            lines.append(f"• {comp.name} — {_format_price(comp.last_price)}")
        lines.append("")

    text = "\n".join(lines).strip()
    truncated = False
    if len(text) > 2900:
        text = text[:2900].rsplit("\n", 1)[0]
        truncated = True
    if truncated:
        text += "\n\n_Trimmed for length. Refine filters or query a single product._"

    return {
        "response_type": "ephemeral",
        "text": "All products and competitors",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ],
    }
