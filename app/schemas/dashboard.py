"""Dashboard aggregate response contracts."""

from datetime import date

from pydantic import BaseModel


class DashboardDailyBucket(BaseModel):
    date: date
    total: int
    allowed: int
    blocked: int


class DashboardTopLink(BaseModel):
    short_code: str | None
    short_link_note: str | None
    total: int


class DashboardTopReferer(BaseModel):
    referer: str
    total: int


class DashboardResponse(BaseModel):
    total: int
    allowed: int
    blocked: int
    unique_ips: int
    daily: list[DashboardDailyBucket]
    top_links: list[DashboardTopLink]
    top_referers: list[DashboardTopReferer]
