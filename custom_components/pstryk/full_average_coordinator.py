"""Full average price coordinator — fetches today's server-computed average buy price."""
import logging
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from .const import API_URL, DAILY_AVERAGE_ENDPOINT, DOMAIN, DEFAULT_RETRY_ATTEMPTS, DEFAULT_RETRY_DELAY
from .api_client import PstrykAPIClient

_LOGGER = logging.getLogger(__name__)


class PstrykFullAveragePriceCoordinator(DataUpdateCoordinator):
    """Fetches today's server-side average gross buy price once per hour."""

    def __init__(self, hass, api_client: PstrykAPIClient, retry_attempts=None, retry_delay=None):
        self.api_client = api_client
        self.retry_attempts = retry_attempts if retry_attempts is not None else DEFAULT_RETRY_ATTEMPTS
        self.retry_delay = retry_delay if retry_delay is not None else DEFAULT_RETRY_DELAY
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_full_average", update_interval=timedelta(hours=1))

    async def _async_update_data(self):
        now = dt_util.now()
        start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        endpoint = DAILY_AVERAGE_ENDPOINT.format(
            start=dt_util.as_utc(start_local).strftime("%Y-%m-%dT%H:%M:%SZ"),
            end=dt_util.as_utc(end_local).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        url = f"{API_URL}{endpoint}"

        _LOGGER.debug("Full average: fetching today's average price from %s", url)
        data = await self.api_client.fetch(url, max_retries=self.retry_attempts, base_delay=self.retry_delay)

        avg = data.get("summary", {}).get("pricing", {}).get("price_gross_avg")
        if avg is None:
            raise UpdateFailed("No average price available yet")

        result = {
            "average_price": round(float(avg), 2),
            "fetched_at": now.isoformat(),
        }
        _LOGGER.info("Full average: today's average price is %.2f PLN/kWh", result["average_price"])
        return result
