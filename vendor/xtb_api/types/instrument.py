"""Instrument and quote type definitions."""

from pydantic import BaseModel


class InstrumentSymbol(BaseModel):
    """Complete instrument symbol definition from XTB."""

    name: str
    quote_id: int
    instrument_id: int
    id_asset_class: int
    display_name: str
    description: str
    full_description: str = ""
    group_id: int
    search_group: str = ""
    precision: int = 2
    lot_min: float = 1.0
    lot_step: float = 1.0
    instrument_tag: str | None = None
    has_depth: bool | None = None


class Quote(BaseModel):
    """Real-time quote/tick data."""

    symbol: str
    ask: float
    bid: float
    spread: float
    high: float | None = None
    low: float | None = None
    time: int | None = None


class InstrumentSearchResult(BaseModel):
    """Instrument search result."""

    symbol: str
    instrument_id: int
    name: str
    description: str
    asset_class: str
    symbol_key: str
