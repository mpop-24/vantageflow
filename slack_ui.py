def _format_price(value):
    if value is None:
        return "n/a"
    return f"${value:.2f}"


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
                    "text": f"*{product.product_name}* competitors\n" + "\n".join(lines),
                },
            }
        ]

    return {
        "response_action": "update",
        "blocks": blocks,
    }
