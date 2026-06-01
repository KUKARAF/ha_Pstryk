"""Data update coordinator for Pstryk price prognosis (peak/dip sensors)."""
import logging
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import API_URL, PRICING_ENDPOINT
from .api_client import PstrykAPIClient

_LOGGER = logging.getLogger(__name__)


class PstrykPrognosisCoordinator(DataUpdateCoordinator):
    """Fetches tomorrow's TGE prices and computes peak/dip hours."""

    def __init__(self, hass, api_client: PstrykAPIClient):
        self.api_client = api_client
        self._unsub = None
        super().__init__(hass, _LOGGER, name="pstryk_prognosis")

    async def _async_update_data(self):
        """Fetch tomorrow's prices and return peak morning, peak evening, and dip hour."""
        today_local = dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0)
        window_end_local = today_local + timedelta(days=2)
        start_utc = dt_util.as_utc(today_local)
        end_utc = dt_util.as_utc(window_end_local)

        url = (
            f"{API_URL}"
            f"{PRICING_ENDPOINT.format(start=start_utc.strftime('%Y-%m-%dT%H:%M:%SZ'), end=end_utc.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
        )

        try:
            data = await self.api_client.fetch(url)
        except Exception as err:
            raise UpdateFailed(f"Error fetching prognosis data: {err}") from err

        tomorrow = (dt_util.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        prices = []
        for frame in data.get("frames", []):
            pricing = frame.get("metrics", {}).get("pricing", {})
            if pricing.get("tge_price") is None:
                continue
            raw_gross = pricing.get("price_gross")
            if raw_gross is None:
                continue
            start_dt = dt_util.parse_datetime(frame.get("start", ""))
            if start_dt is None:
                continue
            local_start = dt_util.as_local(start_dt)
            if local_start.strftime("%Y-%m-%d") != tomorrow:
                continue
            prices.append({"start": local_start, "price_gross": float(raw_gross)})

        _LOGGER.debug("Prognosis: %d tomorrow frames with valid tge_price", len(prices))

        def find_peak(start_h, end_h):
            candidates = [p for p in prices if start_h <= p["start"].hour < end_h]
            return max(candidates, key=lambda p: p["price_gross"]) if candidates else None

        def find_dip():
            return min(prices, key=lambda p: p["price_gross"]) if prices else None

        def fmt(entry):
            if entry is None:
                return None
            return {
                "start": entry["start"].strftime("%Y-%m-%dT%H:%M:%S"),
                "price_gross": round(entry["price_gross"], 4),
            }

        return {
            "peak_morning": fmt(find_peak(6, 13)),
            "peak_evening": fmt(find_peak(16, 22)),
            "dip": fmt(find_dip()),
            "date": tomorrow,
            "fetched_at": dt_util.now().isoformat(),
        }

    def _next_poll_time(self, now):
        """Return the next scheduled poll time (every 30 min between 15:00 and 23:00)."""
        if now.hour < 15:
            return now.replace(hour=15, minute=0, second=0, microsecond=0)
        if now.hour < 23:
            next_minute = (now.minute // 30 + 1) * 30
            if next_minute >= 60:
                return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            return now.replace(minute=next_minute, second=0, microsecond=0)
        return (now + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)

    def schedule_next_update(self):
        """Schedule the next poll via async_track_point_in_time."""
        if self._unsub:
            self._unsub()
            self._unsub = None

        next_time = self._next_poll_time(dt_util.now())
        _LOGGER.debug("Next prognosis poll at %s", next_time.strftime("%Y-%m-%d %H:%M:%S"))
        self._unsub = async_track_point_in_time(
            self.hass, self._handle_update, dt_util.as_utc(next_time)
        )

    async def _handle_update(self, _):
        """Run a fresh fetch then re-schedule."""
        try:
            await self.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Prognosis update failed: %s", err)
        self.schedule_next_update()

    def cancel(self):
        """Cancel the scheduled update callback."""
        if self._unsub:
            self._unsub()
            self._unsub = None
