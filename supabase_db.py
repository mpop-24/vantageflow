import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import httpx


@dataclass
class CompetitorTrack:
    id: int
    product_id: int
    name: str
    url: str
    last_price: Optional[float] = None
    last_checked: Optional[str] = None


@dataclass
class ClientProduct:
    id: int
    product_name: str
    base_url: str
    slack_channel_id: str
    slack_team_id: Optional[str] = None
    client_price: Optional[float] = None
    competitors: List[CompetitorTrack] = field(default_factory=list)


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _get_headers() -> dict:
    key = _get_env("SUPABASE_SERVICE_ROLE_KEY")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _base_url() -> str:
    supabase_url = _get_env("SUPABASE_URL").rstrip("/")
    return f"{supabase_url}/rest/v1"


def _request(method: str, path: str, params=None, json=None):
    url = f"{_base_url()}/{path.lstrip('/')}"
    headers = _get_headers()
    response = httpx.request(method, url, headers=headers, params=params, json=json, timeout=15)
    response.raise_for_status()
    if response.status_code == 204:
        return None
    return response.json()


def _parse_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _map_competitor(row) -> CompetitorTrack:
    return CompetitorTrack(
        id=row["id"],
        product_id=row.get("product_id"),
        name=row.get("name"),
        url=row.get("url"),
        last_price=_parse_float(row.get("last_price")),
        last_checked=row.get("last_checked"),
    )


def _map_product(row) -> ClientProduct:
    competitors = [ _map_competitor(comp) for comp in row.get("competitortrack", []) ]
    return ClientProduct(
        id=row["id"],
        product_name=row.get("product_name"),
        base_url=row.get("base_url"),
        slack_channel_id=row.get("slack_channel_id"),
        slack_team_id=row.get("slack_team_id"),
        client_price=_parse_float(row.get("client_price")),
        competitors=competitors,
    )


def list_client_products(team_id: Optional[str] = None) -> List[ClientProduct]:
    params = {
        "select": "id,product_name,base_url,slack_channel_id,slack_team_id,client_price,competitortrack(id,product_id,name,url,last_price,last_checked)",
        "order": "id.asc",
    }
    if team_id:
        params["slack_team_id"] = f"eq.{team_id}"
    rows = _request("GET", "clientproduct", params=params)
    return [ _map_product(row) for row in rows ]


def get_client_product(product_id) -> Optional[ClientProduct]:
    params = {
        "select": "id,product_name,base_url,slack_channel_id,slack_team_id,client_price,competitortrack(id,product_id,name,url,last_price,last_checked)",
        "id": f"eq.{product_id}",
        "limit": 1,
    }
    rows = _request("GET", "clientproduct", params=params)
    if not rows:
        return None
    return _map_product(rows[0])


def update_competitor(comp_id: int, last_price=None, last_checked: Optional[datetime] = None):
    payload = {}
    if last_price is not None:
        payload["last_price"] = last_price
    if last_checked is not None:
        payload["last_checked"] = last_checked.isoformat() + "Z"
    if not payload:
        return None
    params = {"id": f"eq.{comp_id}"}
    return _request("PATCH", "competitortrack", params=params, json=payload)


def update_client_product(product_id, client_price=None):
    payload = {}
    if client_price is not None:
        payload["client_price"] = client_price
    if not payload:
        return None
    params = {"id": f"eq.{product_id}"}
    return _request("PATCH", "clientproduct", params=params, json=payload)
