"""Rolling 24h price cache — survives calendar-day boundaries and HA restarts."""
import logging
import json
import os
import asyncio
from datetime import timedelta
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class PstrykPriceCache:
    """In-memory + disk-backed cache of buy prices, keyed by local ISO start string."""

    def __init__(self, hass):
        self.hass = hass
        self._data: dict[str, float] = {}
        self._cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_price_history.json")

    async def async_load_cache(self):
        """Populate self._data from disk cache if present."""
        cached = await self._load_cache()
        if cached:
            self._data = cached
            _LOGGER.debug("Price cache: loaded %d entries from disk", len(self._data))

    async def _load_cache(self) -> dict[str, float] | None:
        if not os.path.exists(self._cache_file):
            return None

        def _read():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except OSError as err:
                _LOGGER.warning("Price cache: failed to read cache: %s", err)
                return None

        return await asyncio.to_thread(_read)

    async def _save_cache(self, data: dict[str, float]):
        def _write():
            try:
                with open(self._cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except OSError as err:
                _LOGGER.warning("Price cache: failed to write cache: %s", err)

        await asyncio.to_thread(_write)

    def update(self, prices: list[dict]) -> None:
        """Merge new price entries into the cache, prune old ones, and persist to disk."""
        for entry in prices:
            start = entry.get("start")
            price = entry.get("price")
            if start is None or price is None:
                continue
            self._data[start] = price

        self._prune(dt_util.now())
        self.hass.async_create_task(self._save_cache(self._data))

    def _prune(self, now) -> None:
        """Drop entries whose start is more than 24h in the past."""
        cutoff = now - timedelta(hours=24)
        for start in list(self._data.keys()):
            start_dt = dt_util.parse_datetime(start)
            if start_dt is None:
                self._data.pop(start, None)
                continue
            local_start = dt_util.as_local(start_dt)
            if local_start < cutoff:
                self._data.pop(start, None)

    def next_24h_window(self, now) -> list[tuple[str, float]]:
        """Return (start, price) pairs with start in [now, now + 24h), sorted ascending."""
        window_end = now + timedelta(hours=24)
        window = []
        for start, price in self._data.items():
            start_dt = dt_util.parse_datetime(start)
            if start_dt is None:
                continue
            local_start = dt_util.as_local(start_dt)
            if now <= local_start < window_end:
                window.append((start, price))

        window.sort(key=lambda item: item[0])
        return window

    def get_cheapest(self, now) -> tuple[str, float] | None:
        """Return the (start, price) pair with the lowest price in the next 24h window."""
        window = self.next_24h_window(now)
        if not window:
            return None
        return min(window, key=lambda item: item[1])

    def get_most_expensive(self, now) -> tuple[str, float] | None:
        """Return the (start, price) pair with the highest price in the next 24h window."""
        window = self.next_24h_window(now)
        if not window:
            return None
        return max(window, key=lambda item: item[1])
