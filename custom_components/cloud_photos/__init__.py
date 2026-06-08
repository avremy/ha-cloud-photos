"""Cloud Photos — companion HA integration.

After the Cloud Photos add-on is installed and running, install this
integration once and HA picks up:

  - the slideshow-card.js Lovelace resource (registered as an extra JS URL,
    no manual resource entry needed)
  - the gallery badge on the user's default dashboard
  - services: cloud_photos.sync_now / reset_slideshow / regenerate_thumbnails
  - sensors: sensor.cloud_photos_last_sync, sensor.cloud_photos_sync_status

The integration is single-instance and config-flow-only — no YAML setup.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.hassio import (
    async_get_addon_info,
    async_get_addons_info,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .addon_api import AddonAPI, AddonUnreachable
from .const import (
    ADDON_SLUG,
    DOMAIN,
    SERVICE_REGENERATE_THUMBNAILS,
    SERVICE_RESET_SLIDESHOW,
    SERVICE_SYNC_NOW,
    SLIDESHOW_CARD_FILE,
    STATIC_URL_PREFIX,
    WWW_DEPLOY_DIR,
)
from .dashboard import inject_badge, remove_badge

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class CloudPhotosRuntime:
    """Per-entry runtime state, hung off `hass.data[DOMAIN][entry_id]`."""

    api: AddonAPI
    addon_slug: str  # full Supervisor slug, e.g. "local_cloud_photos"
    addon_version: str
    addon_hostname: str | None


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain. No YAML — config-flow only."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def _discover_addon(hass: HomeAssistant) -> dict | None:
    """Find the Cloud Photos add-on in Supervisor's addon list.

    Local add-ons get a "local_" prefix; repo-installed add-ons get a hash
    prefix. We match on slug suffix.
    """
    try:
        addons = await async_get_addons_info(hass)
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Could not query Supervisor for add-ons: %s", err)
        return None
    if not addons:
        return None
    for slug, info in addons.items():
        if slug == ADDON_SLUG or slug.endswith(f"_{ADDON_SLUG}"):
            # `async_get_addon_info` returns richer info (hostname, ingress_url, etc.)
            try:
                detail = await async_get_addon_info(hass, slug)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Found add-on %s but couldn't fetch details: %s", slug, err
                )
                detail = info if isinstance(info, dict) else {}
            return {"slug": slug, "info": info, "detail": detail or {}}
    return None


async def _register_frontend_resources(hass: HomeAssistant, version: str) -> None:
    """Expose the add-on's deployed assets at a stable URL and register the
    slideshow-card JS as a Lovelace resource.

    The add-on writes its assets to /config/www/cloud_photos/ on every boot.
    HA already serves that directory at /local/cloud_photos/ — but we also
    mount it at /cloud_photos_static/ for a clean integration-owned URL, and
    use that as the resource URL so it's clearly "ours" and can carry a
    version-busting query string.
    """
    target_dir = WWW_DEPLOY_DIR
    # If the add-on hasn't deployed yet (first boot race), fall back to
    # mounting the directory anyway — files will appear when the add-on starts.
    if not os.path.isdir(target_dir):
        _LOGGER.warning(
            "Cloud Photos: %s does not exist yet — start the add-on so it can "
            "deploy its assets",
            target_dir,
        )
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError:
            pass

    # Modern HA exposes register_static_path on hass.http. Some versions
    # provide async_register_static_paths(); fall back gracefully.
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL_PREFIX, target_dir, cache_headers=False
                )
            ]
        )
    except ImportError:
        # Older HA: synchronous register_static_path
        hass.http.register_static_path(STATIC_URL_PREFIX, target_dir, cache_headers=False)

    js_url = f"{STATIC_URL_PREFIX}/{SLIDESHOW_CARD_FILE}?v={version}"
    add_extra_js_url(hass, js_url)
    _LOGGER.info("Cloud Photos: registered Lovelace resource %s", js_url)


_SERVICE_TO_METHOD: dict[str, str] = {
    SERVICE_SYNC_NOW: "sync",
    SERVICE_RESET_SLIDESHOW: "reset",
    SERVICE_REGENERATE_THUMBNAILS: "regenerate_thumbnails",
}


async def _invoke_addon(hass: HomeAssistant, service: str) -> None:
    """Resolve the runtime and call the AddonAPI method bound to `service`."""
    data = hass.data.get(DOMAIN, {})
    runtimes = [v for v in data.values() if isinstance(v, CloudPhotosRuntime)]
    if not runtimes:
        raise HomeAssistantError(
            "Cloud Photos integration is not configured. "
            "Add it in Settings → Devices & Services first."
        )
    runtime = runtimes[0]
    method = getattr(runtime.api, _SERVICE_TO_METHOD[service])
    try:
        result = await method()
        _LOGGER.info("Cloud Photos service %s: %s", service, result)
    except AddonUnreachable as err:
        raise HomeAssistantError(
            f"Cloud Photos: {err}. Is the add-on running?"
        ) from err


def _register_services(hass: HomeAssistant) -> None:
    """Register the three services exposed by the integration.

    Idempotent — re-registering is a no-op once the service is present.
    """
    for service in _SERVICE_TO_METHOD:
        if hass.services.has_service(DOMAIN, service):
            continue

        async def _handler(call: ServiceCall, _svc: str = service) -> None:
            await _invoke_addon(hass, _svc)

        hass.services.async_register(DOMAIN, service, _handler)


def _unregister_services(hass: HomeAssistant) -> None:
    for svc in (SERVICE_SYNC_NOW, SERVICE_RESET_SLIDESHOW, SERVICE_REGENERATE_THUMBNAILS):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Cloud Photos config entry."""
    hass.data.setdefault(DOMAIN, {})

    discovered = await _discover_addon(hass)
    if discovered is None:
        # Add-on not installed — config flow should have caught this, but
        # someone could uninstall the add-on after the integration was set up.
        raise HomeAssistantError(
            "Cloud Photos add-on is not installed in Supervisor. "
            "Install the add-on first, then re-add this integration."
        )

    slug = discovered["slug"]
    detail = discovered["detail"]
    hostname = detail.get("hostname") if isinstance(detail, dict) else None
    version = (
        detail.get("version")
        if isinstance(detail, dict)
        else None
    ) or "0.0.0"

    session = async_get_clientsession(hass)
    api = AddonAPI(session, hostname=hostname)

    # Cheap liveness probe — avoid blowing up setup if the add-on is still
    # starting; the sensors will recover on their first poll.
    try:
        health = await api.health()
        version = health.get("version") or version
    except AddonUnreachable as err:
        _LOGGER.warning(
            "Cloud Photos add-on not reachable yet (%s) — continuing setup; "
            "sensors will poll once the add-on is up",
            err,
        )

    runtime = CloudPhotosRuntime(
        api=api,
        addon_slug=slug,
        addon_version=version,
        addon_hostname=hostname,
    )
    hass.data[DOMAIN][entry.entry_id] = runtime

    # 1. Register the Lovelace card JS so users don't need a manual resource.
    await _register_frontend_resources(hass, version)

    # 2. Forward to the sensor platform.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 3. Inject the gallery badge into the default dashboard (idempotent).
    await inject_badge(hass)

    # 4. Register services (idempotent).
    _register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options updates — currently a no-op (no options yet)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry — undo everything async_setup_entry did."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove our badge from the default dashboard.
    try:
        await remove_badge(hass)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Cloud Photos: could not remove badge on unload: %s", err)

    # Drop the runtime; if this was the last entry, unregister services.
    data = hass.data.get(DOMAIN, {})
    data.pop(entry.entry_id, None)
    if not any(isinstance(v, CloudPhotosRuntime) for v in data.values()):
        _unregister_services(hass)

    # NOTE: we deliberately do NOT unregister the static path / extra JS URL.
    # HA has no public API to remove an `add_extra_js_url`, and re-adding the
    # integration would just re-add it. The user can restart HA if they
    # really want it gone after uninstall.

    return unload_ok
