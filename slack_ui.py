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


def _gap_text(client_price, competitor_price):
    if client_price is None or competitor_price is None:
        return "Gap: n/a"
    diff = client_price - competitor_price
    if diff > 0:
        return f"Gap: {_format_price(diff)} more expensive"
    if diff < 0:
        return f"Gap: {_format_price(abs(diff))} cheaper"
    return "Gap: Price Matched"


def build_price_alert_message(
    product_name,
    comp_name,
    old_p,
    new_p,
    client_p=None,
    competitor_url=None,
    product_url=None,
    image_url=None,
    sku=None,
):
    if new_p is None:
        headline = "Price Change Alert"
    else:
        headline = "Price Undercut Alert" if (client_p is not None and new_p < client_p) else "Price Change Alert"

    gap_text = _gap_text(client_p, new_p)

    product_lines = [product_name]
    if sku:
        product_lines.append(f"SKU: {sku}")
    product_subtitle = "Your Product" if product_lines else None

    header_block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"⚠️ {headline}"},
    }

    product_text = product_name
    if product_subtitle:
        product_text = f"{product_name}\n{product_subtitle}"
        if sku:
            product_text = f"{product_name}\n{product_subtitle} • SKU: {sku}"

    product_block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": product_text},
    }
    if image_url:
        product_block["accessory"] = {
            "type": "image",
            "image_url": image_url,
            "alt_text": product_name,
        }

    price_fields = [
        {"type": "mrkdwn", "text": f"Your Price\n{_format_price(client_p)}"},
        {"type": "mrkdwn", "text": f"Competitor Price\n{_format_price(new_p)}"},
        {"type": "mrkdwn", "text": f"Gap\n{gap_text.replace('Gap: ', '')}"},
    ]

    prices_block = {
        "type": "section",
        "fields": price_fields,
    }

    context_block = {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"Competitor: {comp_name}"}],
    }

    blocks = [header_block, product_block, prices_block, context_block]

    if competitor_url or product_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Product"},
                        "url": competitor_url or product_url,
                    }
                ],
            }
        )

    return {
        "text": f"{headline}: {comp_name} now {_format_price(new_p)}",
        "attachments": [
            {
                "color": "#7a1e1e",
                "blocks": blocks,
            }
        ],
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
                    "text": "Which product do you want to audit?",
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
    client_price = product.client_price
    if not product.competitors:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{product.product_name}\n"
                        f"{_format_price(client_price)}\n"
                        "Competitors\n"
                        "No competitors configured."
                    ),
                },
            }
        ]
    else:
        lines = []
        lines.append(f"{_format_price(client_price)}")
        lines.append("Competitors")
        for comp in product.competitors:
            gap = _gap_text(client_price, comp.last_price)
            lines.append(f"• {comp.name} — {_format_price(comp.last_price)} — {gap}")
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{product.product_name}\n" + "\n".join(lines),
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
        client_price = product.client_price
        lines.append(f"{product.product_name}")
        lines.append(f"{_format_price(client_price)}")
        lines.append("Competitors")
        if not product.competitors:
            lines.append("• No competitors configured.")
            continue
        for comp in product.competitors:
            gap = _gap_text(client_price, comp.last_price)
            lines.append(f"• {comp.name} — {_format_price(comp.last_price)} — {gap}")
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
