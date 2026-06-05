from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum

class AlertCondition(str, Enum):
    ABOVE = 'above'
    BELOW = 'below'

def normalize_symbol(value: str) -> str:
    value = (value or "").strip().upper()
    if not value:
        raise ValueError("symbol is required")
    return value
# --- Request Models ---
class HoldingCreate(BaseModel):
    symbol: str
    name: Optional[str] = ''
    quantity: float = Field(ge=0)
    avg_cost: float = Field(ge=0)
    currency: str = 'USD'

    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_symbol(value)

class HoldingUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[float] = Field(default=None, ge=0)
    avg_cost: Optional[float] = Field(default=None, ge=0)

class PriceAlertCreate(BaseModel):
    symbol: str
    condition: AlertCondition
    target_price: float = Field(ge=0)

    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_symbol(value)

class WatchlistAdd(BaseModel):
    symbol: str
    name: Optional[str] = ''

    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_symbol(value)

class NewsFilter(BaseModel):
    symbol: Optional[str] = None
    is_read: Optional[bool] = None
    limit: int = Field(default=50, ge=1, le=500)

class AuthRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=4, max_length=256)

class ApiSettingsUpdate(BaseModel):
    finnhub_api_key: Optional[str] = ''

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=4, max_length=256)

class UsernameUpdateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)

# --- Response Models ---
class HoldingResponse(BaseModel):
    id: Optional[int] = None
    symbol: str
    name: Optional[str] = ''
    quantity: float
    avg_cost: float
    currency: str
    current_price: Optional[float] = None
    current_value: Optional[float] = None
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    added_at: Optional[str] = None
    updated_at: Optional[str] = None

class AlertResponse(BaseModel):
    id: Optional[int] = None
    symbol: str
    condition: str
    target_price: float
    current_price: Optional[float] = None
    triggered: bool = False
    triggered_at: Optional[str] = None
    active: bool = True
    created_at: Optional[str] = None

class WatchlistItem(BaseModel):
    id: Optional[int] = None
    symbol: str
    name: Optional[str] = ''
    current_price: Optional[float] = None
    price_change: Optional[float] = None
    price_change_percent: Optional[float] = None
    added_at: Optional[str] = None

class PricePoint(BaseModel):
    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None

class NewsItem(BaseModel):
    id: Optional[int] = None
    symbol: Optional[str] = None
    title: str
    summary: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = None
    published_at: Optional[str] = None
    is_read: bool = False

class PortfolioSnapshot(BaseModel):
    id: Optional[int] = None
    timestamp: Optional[str] = None
    total_value: Optional[float] = None
    total_cost: Optional[float] = None
    total_pnl: Optional[float] = None
    total_pnl_percent: Optional[float] = None
    holdings_count: Optional[int] = None

class DashboardData(BaseModel):
    total_value: float = 0
    total_cost: float = 0
    total_pnl: float = 0
    total_pnl_percent: float = 0
    holdings_count: int = 0
    watchlist_count: int = 0
    alert_count: int = 0
    top_performer: Optional[str] = None
    top_performer_pnl: Optional[float] = None
    worst_performer: Optional[str] = None
    worst_performer_pnl: Optional[float] = None
    best_alerts: List[AlertResponse] = Field(default_factory=list)
    recent_news: List[NewsItem] = Field(default_factory=list)
