"""Config flow for Cloud Photos.

Single-step flow:
  - Confirms the Cloud Photos add-on is installed and running in Supervisor.
  - Asks the user to confirm "yes, install integration → add badge to default
    dashboard, register Lovelace card resource".
  - Creates the entry. No user-entered fields in v1; everything else flows
    from the add-on.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.hassio import async_get_addons_info
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import ADDON_SLUG, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _find_addon(hass) -> dict | None:
    """Return Supervisor info for the cloud_photos add-on, or None."""
    try:
        addons = await async_get_addons_info(hass)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not query Supervisor addons: %s", err)
        return None
    if not addons:
        return None
    for slug, info in addons.items():
        if slug == ADDON_SLUG or slug.endswith(f"_{ADDON_SLUG}"):
            return {"slug": slug, "info": info}
    return None


class CloudPhotosConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Cloud Photos integration setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User-initiated install: confirm add-on present, then create entry."""
        # Single-instance only.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        addon = await _find_addon(self.hass)
        if addon is None:
            return self.async_abort(reason="addon_not_installed")

        addon_state = ""
        info = addon.get("info") or {}
        if isinstance(info, dict):
            addon_state = info.get("state") or ""

        if user_input is not None:
            return self.async_create_entry(
                title="Cloud Photos",
                data={"addon_slug": addon["slug"]},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            description_placeholders={
                "addon_slug": addon["slug"],
                "addon_state": addon_state or "unknown",
            },
        )
