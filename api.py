import hmac
import hashlib
import json
import os
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import httpx

from slack_handlers import handle_prices_command, handle_product_selected

app = FastAPI()


def _parse_form_body(body):
    data = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] for key, values in data.items()}


def _handle_slack_url_verification(body):
    try:
        payload = json.loads(body)
    except Exception:
        return None
    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge")
        return JSONResponse(content={"challenge": challenge})
    return None


def _verify_slack_request(headers, body):
    secret = os.getenv("SLACK_SIGNING_SECRET")
    if not secret:
        return True
    timestamp = headers.get("X-Slack-Request-Timestamp")
    signature = headers.get("X-Slack-Signature")
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False
    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    digest = hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


async def _post_response_url(response_url, payload):
    if not response_url:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(response_url, json=payload)
    except Exception:
        return


@app.post("/slack/prices")
async def slack_prices(request: Request):
    body = await request.body()
    verification = _handle_slack_url_verification(body)
    if verification:
        return verification
    if not _verify_slack_request(request.headers, body):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")
    payload = _parse_form_body(body)
    team_id = payload.get("team_id")
    response = handle_prices_command(team_id)
    return JSONResponse(content=response)


@app.post("/slack/actions")
async def slack_actions(request: Request):
    body = await request.body()
    verification = _handle_slack_url_verification(body)
    if verification:
        return verification
    if not _verify_slack_request(request.headers, body):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")
    form = _parse_form_body(body)
    payload = form.get("payload")
    if not payload:
        return PlainTextResponse("Missing payload", status_code=400)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return PlainTextResponse("Invalid payload", status_code=400)

    actions = data.get("actions") or []
    action = actions[0] if actions else {}
    if action.get("action_id") == "prices_select_product":
        selected = action.get("selected_option") or {}
        value = selected.get("value")
        if not value:
            return PlainTextResponse("Missing product id", status_code=400)
        response = handle_product_selected(value)
        response_url = data.get("response_url")
        if response_url:
            await _post_response_url(response_url, response)
            return PlainTextResponse("OK", status_code=200)
        return JSONResponse(content=response)

    return PlainTextResponse("No action", status_code=200)


@app.post("/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    verification = _handle_slack_url_verification(body)
    if verification:
        return verification
    if not _verify_slack_request(request.headers, body):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")
    return PlainTextResponse("OK", status_code=200)
