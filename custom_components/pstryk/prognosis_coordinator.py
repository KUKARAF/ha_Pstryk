"""Coordinator for Pstryk price prognosis - polls every 30 min from 15:00."""
import logging
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util
from .const import API_URL, DOMAIN
from .api_client import PstrykAPIClient

_LOGGER = logging.getLogger(__name__)

# Fetch window: today midnight → tomorrow midnight (48h covers full D+1)
_WINDOW_DAYS = 2
# Poll between 15:00 and 23:00 local time every 30 minutes
_POLL_START_HOUR = 15
_POLL_END_HOUR = 23
_POLL_INTERVAL_MINUTES = 30


class PstrykPrognosisCoordinator(DataUpdateCoordinator):
    """Fetches hourly price prognosis and exposes it as a structured list."""

    def __init__(self, hass, api_client: PstrykAPIClient):
        self.api_client = api_client
        self._unsub = None
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_prognosis")

    async def _async_update_data(self) -> dict:
        now_local = dt_util.now()
        today_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = today_midnight + timedelta(days=_WINDOW_DAYS)

        start_str = dt_util.as_utc(today_midnight).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = dt_util.as_utc(window_end).strftime("%Y-%m-%dT%H:%M:%SZ")

        url = (
            f"{API_URL}meter-data/unified-metrics/"
            f"?metrics=pricing&resolution=hour"
            f"&window_start={start_str}&window_end={end_str}"
        )

        try:
            data = await self.api_client.fetch(url)
        except Exception as err:
            raise UpdateFailed(f"Prognosis fetch failed: {err}") from err

        prices = []
        for frame in data.get("frames", []):
            pricing = frame.get("metrics", {}).get("pricing", {})
            tge_price = pricing.get("tge_price")
            if tge_price is None:
                continue
            prices.append({
                "start": frame["start"],
                "end": frame["end"],
                "tge_price": round(tge_price, 4),
                "price_net": pricing.get("price_net"),
                "price_gross": pricing.get("price_gross"),
                "is_cheap": pricing.get("is_cheap"),
                "is_expensive": pricing.get("is_expensive"),
            })

        _LOGGER.debug(
            "Prognosis fetch: %d frames with valid tge_price (window %s – %s)",
            len(prices), start_str, end_str,
        )
        return {"prices": prices, "fetched_at": now_local.isoformat()}

    def schedule_next_update(self):
        """Schedule next poll: every 30 min between 15:00–23:00 local time."""
        if self._unsub:
            self._unsub()
            self._unsub = None

        now = dt_util.now()
        next_run = self._next_poll_time(now)

        _LOGGER.debug(
            "Prognosis: next poll at %s", next_run.strftime("%Y-%m-%d %H:%M:%S")
        )
        self._unsub = async_track_point_in_time(
            self.hass, self._handle_update, dt_util.as_utc(next_run)
        )

    def _next_poll_time(self, now):
        """Return the next :00 or :30 boundary inside [15:00, 23:00), or 15:00 tomorrow."""
        if now.hour < _POLL_START_HOUR:
            return now.replace(hour=_POLL_START_HOUR, minute=0, second=0, microsecond=0)

        if now.hour >= _POLL_END_HOUR:
            return (now + timedelta(days=1)).replace(
                hour=_POLL_START_HOUR, minute=0, second=0, microsecond=0
            )

        # Advance to the next 30-min boundary
        if now.minute < _POLL_INTERVAL_MINUTES:
            candidate = now.replace(minute=_POLL_INTERVAL_MINUTES, second=0, microsecond=0)
        else:
            candidate = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        # If that pushes us past the end hour, wait until tomorrow
        if candidate.hour >= _POLL_END_HOUR:
            return (now + timedelta(days=1)).replace(
                hour=_POLL_START_HOUR, minute=0, second=0, microsecond=0
            )

        return candidate

    async def _handle_update(self, _):
        await self.async_request_refresh()
        self.schedule_next_update()

    def cancel(self):
        """Cancel any pending scheduled update."""
        if self._unsub:
            self._unsub()
            self._unsub = None
