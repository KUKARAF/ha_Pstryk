"""TGE coordinator — fetches tomorrow's hourly electricity prices at 15:30 CET."""
import logging
import json
import os
import asyncio
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util
from homeassistant.core import callback
from .const import API_URL, PRICING_ENDPOINT, DOMAIN, DEFAULT_RETRY_ATTEMPTS, DEFAULT_RETRY_DELAY
from .api_client import PstrykAPIClient

_LOGGER = logging.getLogger(__name__)


class TGECoordinator(DataUpdateCoordinator):
    """Fetches tomorrow's TGE prices once daily at 15:30 CET and caches to disk."""

    def __init__(self, hass, api_client: PstrykAPIClient, retry_attempts=None, retry_delay=None):
        self.api_client = api_client
        self.retry_attempts = retry_attempts if retry_attempts is not None else DEFAULT_RETRY_ATTEMPTS
        self.retry_delay = retry_delay if retry_delay is not None else DEFAULT_RETRY_DELAY
        self._unsub_daily = None
        self._cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_tge.json")
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_tge")

    async def _async_update_data(self):
        now = dt_util.now()
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=2)
        endpoint = PRICING_ENDPOINT.format(
            start=dt_util.as_utc(start_local).strftime("%Y-%m-%dT%H:%M:%SZ"),
            end=dt_util.as_utc(end_local).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        url = f"{API_URL}{endpoint}"

        data = await self.api_client.fetch(url, max_retries=self.retry_attempts, base_delay=self.retry_delay)

        prices = []
        for frame in data.get("frames", []):
            pricing = frame.get("metrics", {}).get("pricing", {})
            price_gross = pricing.get("price_gross")
            if price_gross is None:
                continue
            start_dt = dt_util.parse_datetime(frame["start"])
            if start_dt is None:
                continue
            local_start = dt_util.as_local(start_dt)
            if local_start.strftime("%Y-%m-%d") != tomorrow:
                continue
            prices.append({
                "start": local_start.isoformat(),
                "price_gross": round(float(price_gross), 4),
            })

        if len(prices) < 20:
            raise UpdateFailed(f"Only {len(prices)} prices for tomorrow — TGE data not published yet")

        result = {
            "date": tomorrow,
            "fetched_at": now.isoformat(),
            "prices_tomorrow": prices,
        }
        await self._save_cache(result)
        _LOGGER.info("TGE: fetched %d prices for %s", len(prices), tomorrow)
        return result

    async def async_load_cache(self):
        """Populate self.data from disk cache if it covers tomorrow's date."""
        cached = await self._load_cache()
        if not cached:
            return
        tomorrow = (dt_util.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        if cached.get("date") == tomorrow and cached.get("prices_tomorrow"):
            self.data = cached
            self.last_update_success = True
            _LOGGER.debug("TGE: loaded %d prices for %s from cache", len(cached["prices_tomorrow"]), tomorrow)

    async def _load_cache(self):
        if not os.path.exists(self._cache_file):
            return None

        def _read():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except OSError as err:
                _LOGGER.warning("TGE: failed to read cache: %s", err)
                return None

        return await asyncio.to_thread(_read)

    async def _save_cache(self, data: dict):
        def _write():
            try:
                with open(self._cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except OSError as err:
                _LOGGER.warning("TGE: failed to write cache: %s", err)

        await asyncio.to_thread(_write)

    def schedule_next_update(self):
        """Schedule next fetch at 15:30 local time (tomorrow if already past today's slot)."""
        if self._unsub_daily:
            self._unsub_daily()
            self._unsub_daily = None

        now = dt_util.now()
        next_run = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)

        _LOGGER.debug("TGE: next fetch scheduled at %s", next_run.strftime("%Y-%m-%d %H:%M:%S %Z"))
        self._unsub_daily = async_track_point_in_time(
            self.hass, self._handle_update, dt_util.as_utc(next_run)
        )

    @callback
    def _handle_update(self, _now):
        """Fired at 15:30 — refresh and reschedule."""
        self.hass.async_create_task(self._do_update())

    async def _do_update(self):
        await self.async_refresh()
        self.schedule_next_update()

    def cancel(self):
        if self._unsub_daily:
            self._unsub_daily()
            self._unsub_daily = None
