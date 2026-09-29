"""Persistent instrument-ID cache for symbol lookups.

Downstream consumers (e.g. trading bots) need to map a symbol to the XTB
instrument ID that the gRPC trading endpoint requires. Looking this up on
every trade is slow; caching it in memory only loses the mapping on restart.

This module persists the mapping to disk as JSON so restarts stay cheap.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from xtb_api.types.instrument import InstrumentSearchResult

logger = logging.getLogger(__name__)


class _SearchClient(Protocol):
    async def search_instrument(self, query: str) -> list[InstrumentSearchResult]: ...


class InstrumentRegistry:
    """Symbol → instrument-ID cache, persisted as JSON.

    Example::

        from xtb_api import XTBClient, InstrumentRegistry

        client = XTBClient(email=..., password=..., account_number=...)
        await client.connect()

        reg = InstrumentRegistry("data/instrument_ids.json")
        matched = await reg.populate(client, ["CIG.PL", "AAPL.US"])
        print(f"Matched {len(matched)} instruments")

        # Later:
        instrument_id = reg.get("CIG.PL")  # 12345
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._ids: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load instrument IDs from %s: %s", self._path, exc)
            return
        if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, int) for k, v in raw.items()):
            logger.warning("Instrument ID file %s has unexpected structure, ignoring", self._path)
            return
        self._ids = raw
        logger.info("Loaded %d instrument IDs from %s", len(self._ids), self._path)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._ids, indent=2, sort_keys=True))

    def get(self, symbol: str) -> int | None:
        """Return the cached instrument ID for `symbol`, or None."""
        return self._ids.get(symbol)

    def set(self, symbol: str, instrument_id: int) -> None:
        """Cache `symbol → instrument_id` and persist to disk immediately."""
        self._ids[symbol] = instrument_id
        self._save()

    @property
    def ids(self) -> dict[str, int]:
        """Read-only view of the full cache."""
        return dict(self._ids)

    async def populate(
        self,
        client: _SearchClient,
        symbols: Iterable[str],
    ) -> dict[str, int]:
        """Download the full instrument list via `client.search_instrument` and
        match every requested symbol against it. Persist the matches.

        Matches are case-insensitive. If a symbol like `BRK.B.US` does not
        appear in the downloaded list, fall back to the dot-less variant
        (`BRKB.US`).

        Symbols are stored under the caller's original casing; subsequent
        calls to `get()` must use the same case.

        Returns a dict of the matches written during this call (does not
        include pre-existing entries).
        """
        # Prime the client's in-memory cache (first call downloads all 11,888+
        # instruments under the WS client's lock). Then read the full cache
        # directly — search_instrument() only returns up to 100 filtered matches,
        # which isn't enough for matching against a full universe.
        await client.search_instrument("a")

        ws = getattr(client, "ws", client)
        raw = getattr(ws, "_symbols_cache", None)
        if isinstance(raw, list) and raw:
            all_results: list[InstrumentSearchResult] = list(raw)
        else:
            # Fallback when caller isn't an XTBClient (e.g., pure protocol implementer).
            all_results = await client.search_instrument("a")

        index = {r.symbol.upper(): r.instrument_id for r in all_results}

        matched: dict[str, int] = {}
        for sym in symbols:
            key = sym.upper()
            if key in index:
                matched[sym] = index[key]
                continue
            # Dot-less fallback: BRK.B.US → BRKB.US. Skip symbols with no inner
            # dots (e.g. AAPL.US) where the dot-less variant would be identical.
            base, _, suffix = key.rpartition(".")
            if base and "." in base:
                alt = base.replace(".", "") + "." + suffix
                if alt in index:
                    matched[sym] = index[alt]

        self._ids.update(matched)
        self._save()
        return matched
