"""Data update coordinator for Pstryk price prognosis (future buy prices)."""
import logging
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import API_URL, PRICING_ENDPOINT
from .api_client import PstrykAPIClient

_LOGGER = logging.getLogger(__name__)

STATISTIC_ID = "pstryk:future_buy_price"


class PstrykPrognosisCoordinator(DataUpdateCoordinator):
    """Fetches day-ahead TGE prices and injects them as HA external statistics."""

    def __init__(self, hass, api_client: PstrykAPIClient):
        self.api_client = api_client
        self._unsub = None
        super().__init__(hass, _LOGGER, name="pstryk_prognosis")

    async def _async_update_data(self):
        """Fetch pricing frames filtered to tge_price != None and inject as statistics."""
        today_local = dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0)
        window_end_local = today_local + timedelta(days=2)
        start_utc = dt_util.as_utc(today_local)
        end_utc = dt_util.as_utc(window_end_local)

        url = (
            f"{API_URL}"
            f"{PRICING_ENDPOINT.format(start=start_utc.strftime('%Y-%m-%dT%H:%M:%SZ'), end=end_utc.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
        )
        _LOGGER.debug("Fetching prognosis data from %s", url)

        try:
            data = await self.api_client.fetch(url)
        except Exception as err:
            raise UpdateFailed(f"Error fetching prognosis data: {err}") from err

        frames = data.get("frames", [])
        _LOGGER.warning("Prognosis: received %d frames from API", len(frames))

        prices = []
        for frame in frames:
            # Mirror existing coordinator: check frame directly first, then metrics.pricing
            pricing = frame.get("metrics", {}).get("pricing", {})
            tge_price = frame.get("tge_price", pricing.get("tge_price"))
            if tge_price is None:
                continue
            raw_gross = frame.get("price_gross", pricing.get("price_gross"))
            if raw_gross is None:
                continue
            try:
                price_gross = round(float(str(raw_gross).replace(",", ".")), 4)
            except (ValueError, TypeError):
                continue
            start = frame.get("start", "")
            if not start:
                continue
            prices.append({"start": start, "price_gross": price_gross})

        _LOGGER.warning("Prognosis: %d frames passed tge_price filter", len(prices))

        if prices:
            self._inject_statistics(prices)

        return {"prices": prices, "fetched_at": dt_util.now().isoformat()}

    def _inject_statistics(self, prices):
        """Push hourly price data into HA recorder as external statistics."""
        from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
        from homeassistant.components.recorder.statistics import async_add_external_statistics

        try:
            from homeassistant.components.recorder.models import StatisticMeanType
            mean_type_kwargs = {"mean_type": StatisticMeanType.ARITHMETIC}
        except ImportError:
            mean_type_kwargs = {}

        metadata = StatisticMetaData(
            has_mean=True,
            has_sum=False,
            name="Pstryk Future Buy Price",
            source="pstryk",
            statistic_id=STATISTIC_ID,
            unit_of_measurement="PLN/kWh",
            unit_class=None,
            **mean_type_kwargs,
        )
        stats = []
        for p in prices:
            dt = dt_util.parse_datetime(p["start"])
            if dt is None:
                continue
            stats.append(StatisticData(start=dt_util.as_utc(dt), mean=p["price_gross"]))

        async_add_external_statistics(self.hass, metadata, stats)
        _LOGGER.debug("Injected %d statistic entries for %s", len(stats), STATISTIC_ID)

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
        _LOGGER.debug(
            "Next prognosis poll at %s", next_time.strftime("%Y-%m-%d %H:%M:%S")
        )
        self._unsub = async_track_point_in_time(
            self.hass, self._handle_update, dt_util.as_utc(next_time)
        )

    async def _handle_update(self, _):
        """Run a fresh fetch then re-schedule."""
        _LOGGER.debug("Prognosis scheduled poll triggered")
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
