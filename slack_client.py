import os
import logging
import httpx

logger = logging.getLogger(__name__)

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


def send_slack_message(channel, text, blocks=None, token=None):
    token = token or os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is not set")

    payload = {
        "channel": channel,
        "text": text,
    }
    if blocks:
        payload["blocks"] = blocks

    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = httpx.post(
            SLACK_POST_MESSAGE_URL,
            json=payload,
            headers=headers,
            timeout=10,
        )
    except Exception as exc:
        logger.exception("Slack API request failed")
        raise RuntimeError("Slack API request failed") from exc

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError("Slack API returned invalid JSON") from exc

    if not response.is_success or not data.get("ok"):
        error = data.get("error") if isinstance(data, dict) else "unknown_error"
        raise RuntimeError(f"Slack API error: {error}")

    return data
