"""Inject / remove the Cloud Photos gallery badge on the default dashboard.

We're careful here: only touch the user's **default storage-mode** dashboard
(URL path "lovelace"). If the user is still on the auto-generated default
(strategy mode), `async_load()` raises `ConfigNotFound` and we skip — we
won't materialise a storage dashboard the user didn't ask for.

The badge carries a marker (`cloud_photos_gallery_badge: True`). On unload
we remove ONLY badges carrying that marker; we never touch user-added
badges.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import BADGE_MARKER, DEFAULT_DASHBOARD_URL_PATH

_LOGGER = logging.getLogger(__name__)


def _make_badge() -> dict[str, Any]:
    """Build the badge dict we add to the default dashboard.

    Uses the entity-type badge pointing at our sync-status sensor, with a
    tap action that navigates to the existing slideshow gallery view.
    """
    return {
        "type": "entity",
        "entity": "sensor.cloud_photos_sync_status",
        "name": "Cloud Photos",
        "icon": "mdi:cloud-sync",
        "tap_action": {
            "action": "navigate",
            "navigation_path": "/slideshow/gallery",
        },
        BADGE_MARKER: True,
    }


def _get_default_dashboard(hass: HomeAssistant):
    """Return the LovelaceStorage object for the user's default dashboard.

    Returns None if the lovelace component isn't ready or the default
    dashboard isn't storage-managed.
    """
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        return None
    # Modern HA (2024+): `LovelaceData` dataclass with `.dashboards` mapping.
    # Older HA: dict with "dashboards" key. Handle both shapes.
    dashboards = getattr(lovelace_data, "dashboards", None)
    if dashboards is None and isinstance(lovelace_data, dict):
        dashboards = lovelace_data.get("dashboards", {})
    if not dashboards:
        return None
    # Default dashboard key is None in some versions, "lovelace" in others.
    return dashboards.get(DEFAULT_DASHBOARD_URL_PATH) or dashboards.get(None)


async def inject_badge(hass: HomeAssistant) -> bool:
    """Add our gallery badge to the first view of the default dashboard.

    Returns True if the badge is now present (either newly added or already
    there), False if we couldn't touch the dashboard at all.
    """
    dashboard = _get_default_dashboard(hass)
    if dashboard is None:
        _LOGGER.info(
            "Cloud Photos: lovelace dashboards not available; skipping badge injection"
        )
        return False

    try:
        config = await dashboard.async_load(False)
    except Exception as err:  # ConfigNotFound or other  # noqa: BLE001
        _LOGGER.info(
            "Cloud Photos: default dashboard is not storage-mode (%s) — "
            "user can add the gallery badge manually if desired",
            err.__class__.__name__,
        )
        return False

    views = config.get("views") or []
    if not views:
        _LOGGER.info("Cloud Photos: default dashboard has no views; skipping badge")
        return False

    first_view = views[0]
    badges = list(first_view.get("badges") or [])

    if any(
        isinstance(b, dict) and b.get(BADGE_MARKER) is True
        for b in badges
    ):
        _LOGGER.debug("Cloud Photos: badge already present, no change")
        return True

    badges.append(_make_badge())
    first_view["badges"] = badges
    config["views"] = [first_view, *views[1:]]

    await dashboard.async_save(config)
    _LOGGER.info("Cloud Photos: gallery badge added to default dashboard")
    return True


async def remove_badge(hass: HomeAssistant) -> bool:
    """Remove the gallery badge from the default dashboard, if present.

    Only badges carrying our marker are removed; user-added badges are left
    alone. Returns True if anything changed.
    """
    dashboard = _get_default_dashboard(hass)
    if dashboard is None:
        return False
    try:
        config = await dashboard.async_load(False)
    except Exception:  # noqa: BLE001
        return False

    views = config.get("views") or []
    changed = False
    for view in views:
        badges = view.get("badges") or []
        kept = [
            b for b in badges
            if not (isinstance(b, dict) and b.get(BADGE_MARKER) is True)
        ]
        if len(kept) != len(badges):
            view["badges"] = kept
            changed = True

    if changed:
        await dashboard.async_save(config)
        _LOGGER.info("Cloud Photos: gallery badge removed from default dashboard")
    return changed
