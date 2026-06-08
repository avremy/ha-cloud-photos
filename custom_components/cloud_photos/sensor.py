"""Cloud Photos sensors — last-sync timestamp + sync status.

Both sensors share a single DataUpdateCoordinator that polls the add-on's
`/last-sync` endpoint once a minute.
"""
from __future__ import annotations

import datetime
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .addon_api import AddonUnreachable
from .const import DOMAIN, POLL_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


SENSOR_LAST_SYNC = SensorEntityDescription(
    key="last_sync",
    name="Cloud Photos last sync",
    device_class=SensorDeviceClass.TIMESTAMP,
)

SENSOR_SYNC_STATUS = SensorEntityDescription(
    key="sync_status",
    name="Cloud Photos sync status",
    icon="mdi:cloud-sync",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the two sensors."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    api = runtime.api

    async def _poll() -> dict[str, Any]:
        try:
            return await api.last_sync()
        except AddonUnreachable as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"unexpected error polling add-on: {err}") from err

    coordinator: DataUpdateCoordinator[dict[str, Any]] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="cloud_photos_addon",
        update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
        update_method=_poll,
    )
    await coordinator.async_config_entry_first_refresh()

    async_add_entities(
        [
            LastSyncSensor(coordinator, entry),
            SyncStatusSensor(coordinator, entry),
        ]
    )


def _parse_ts(value: str | None) -> datetime.datetime | None:
    """Parse an ISO-ish timestamp from the add-on into a tz-aware datetime.

    The add-on emits naive `datetime.now().isoformat(timespec='seconds')`
    timestamps in the host timezone. We treat them as UTC for the timestamp
    sensor (HA will display in local) — if precision matters, the user can
    look at sync.log directly.
    """
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


class _CloudPhotosSensor(
    CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity
):
    """Shared base for the two cloud_photos sensors."""

    _attr_has_entity_name = False  # use the explicit name in description
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"


class LastSyncSensor(_CloudPhotosSensor):
    """Timestamp of the most recent finished sync run."""

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, SENSOR_LAST_SYNC)

    @property
    def native_value(self) -> datetime.datetime | None:
        data = self.coordinator.data or {}
        return _parse_ts(data.get("finished_at"))


class SyncStatusSensor(_CloudPhotosSensor):
    """String status: never_run | running | success | success_with_errors | failed | unknown."""

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, SENSOR_SYNC_STATUS)

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        return data.get("status") or "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {}
        for key in (
            "started_at",
            "files_downloaded",
            "error",
            "running_job",
        ):
            if data.get(key) is not None:
                attrs[key] = data[key]
        return attrs
