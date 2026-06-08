"""Talk to the Cloud Photos add-on over HTTP.

The add-on runs with `host_network: true`, so its ports are reachable on the
HA host's network. From inside the HA Core container, we resolve the add-on
via the Supervisor-reported `hostname` first (which is what Supervised DNS
serves), and fall back to a couple of well-known host names.

Endpoints used:
    GET  /health             liveness + version
    GET  /last-sync          last sync outcome (status, timestamps, counts)
    POST /sync               kick off an incremental sync
    POST /reset              full re-download
    POST /regenerate-thumbnails  rescan + rebuild thumbs + image list
"""
from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp
import async_timeout

from .const import ADDON_PORT

_LOGGER = logging.getLogger(__name__)

# Hostnames to try, in order. The first that responds wins and gets cached
# on the runtime data.
_HOST_CANDIDATES: tuple[str, ...] = (
    # Supervisor-side DNS for the add-on. Local add-ons resolve as
    # "local-<slug>" (hyphens, not underscores).
    "local-cloud-photos",
    # Host_network add-ons land on the HA host's network namespace. mDNS
    # resolves these from HA Core in HAOS / Supervised installs.
    "homeassistant.local",
    "homeassistant",
    # Supervisor's host gateway (works in containerised HA setups).
    "172.30.32.1",
)

# How long we'll wait for the add-on. The add-on is local; if it doesn't
# answer in 5s something is wrong.
DEFAULT_TIMEOUT = 5.0
# Sync jobs return immediately (202 accepted) and run in the background,
# but the add-on can be busy with another job, in which case it 429s.


class AddonUnreachable(RuntimeError):
    """Raised when we cannot find a working host:port for the add-on."""


class AddonAPI:
    """Tiny REST client for the Cloud Photos add-on."""

    def __init__(self, session: aiohttp.ClientSession, hostname: str | None = None):
        self._session = session
        self._base_url: str | None = None
        if hostname:
            self._base_url = f"http://{hostname}:{ADDON_PORT}"

    @property
    def base_url(self) -> str | None:
        return self._base_url

    async def _resolve(self) -> str:
        """Find a working base URL, caching it on first success."""
        if self._base_url:
            return self._base_url
        last_err: Exception | None = None
        for host in _HOST_CANDIDATES:
            url = f"http://{host}:{ADDON_PORT}/health"
            try:
                async with async_timeout.timeout(DEFAULT_TIMEOUT):
                    async with self._session.get(url) as resp:
                        if resp.status == 200:
                            self._base_url = f"http://{host}:{ADDON_PORT}"
                            _LOGGER.debug("cloud_photos add-on reachable at %s", self._base_url)
                            return self._base_url
            except (aiohttp.ClientError, OSError, TimeoutError) as err:
                last_err = err
                continue
        raise AddonUnreachable(
            f"Could not reach Cloud Photos add-on on port {ADDON_PORT} "
            f"(tried: {', '.join(_HOST_CANDIDATES)}). Last error: {last_err}"
        )

    async def health(self) -> dict[str, Any]:
        base = await self._resolve()
        async with async_timeout.timeout(DEFAULT_TIMEOUT):
            async with self._session.get(f"{base}/health") as resp:
                resp.raise_for_status()
                return await resp.json()

    async def last_sync(self) -> dict[str, Any]:
        base = await self._resolve()
        async with async_timeout.timeout(DEFAULT_TIMEOUT):
            async with self._session.get(f"{base}/last-sync") as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _post(self, path: str) -> dict[str, Any]:
        base = await self._resolve()
        async with async_timeout.timeout(DEFAULT_TIMEOUT):
            async with self._session.post(f"{base}{path}") as resp:
                # 202 accepted (job started), 429 busy (existing job running)
                if resp.status not in (200, 202, 429):
                    resp.raise_for_status()
                try:
                    return await resp.json()
                except aiohttp.ContentTypeError:
                    return {"status": "ok"}

    async def sync(self) -> dict[str, Any]:
        return await self._post("/sync")

    async def reset(self) -> dict[str, Any]:
        return await self._post("/reset")

    async def regenerate_thumbnails(self) -> dict[str, Any]:
        return await self._post("/regenerate-thumbnails")


def supervisor_token() -> str | None:
    """Return the Supervisor token if HA Core is running in a supervised env.

    Currently informational only — we use direct host networking to talk to
    the add-on. Kept here so future versions can route through Supervisor's
    ingress proxy if host_network is removed from the add-on.
    """
    return os.environ.get("SUPERVISOR_TOKEN")
