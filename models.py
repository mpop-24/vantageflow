from __future__ import annotations

from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship


class ClientProduct(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_name: str
    base_url: str
    slack_channel_id: str
    slack_team_id: Optional[str] = None

    competitors: List["CompetitorTrack"] = Relationship(back_populates="product")


class CompetitorTrack(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="clientproduct.id")
    name: str
    url: str
    last_price: Optional[float] = None
    last_checked: Optional[datetime] = None

    product: Optional[ClientProduct] = Relationship(back_populates="competitors")
