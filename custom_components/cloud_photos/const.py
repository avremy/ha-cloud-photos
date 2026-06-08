"""Constants for the Cloud Photos integration."""
from __future__ import annotations

DOMAIN = "cloud_photos"

# Add-on slug. Local add-ons get a "local_" prefix in Supervisor; a remote
# repo install would be "<repo_hash>_cloud_photos". We treat any suffix match
# as ours.
ADDON_SLUG = "cloud_photos"
ADDON_PORT = 8099  # host_network ingress port on the add-on

# Where the add-on deploys its static assets (gallery.html, slideshow-card.js)
# inside HA's /config/www/. We re-serve this through our own URL prefix so the
# slideshow-card JS gets a versioned, integration-managed path.
WWW_DEPLOY_DIR = "/config/www/cloud_photos"
STATIC_URL_PREFIX = "/cloud_photos_static"
SLIDESHOW_CARD_FILE = "slideshow-card.js"

# Default Lovelace dashboard URL path (storage-mode key). HA stores the user's
# default dashboard under `.storage/lovelace`; its URL path is "/lovelace".
DEFAULT_DASHBOARD_URL_PATH = "lovelace"

# Marker key written into the badge so re-running setup is idempotent and
# unload knows what it owns.
BADGE_MARKER = "cloud_photos_gallery_badge"

# Polling interval for sensors.
POLL_INTERVAL_SECONDS = 60

# Default services exposed to the user.
SERVICE_SYNC_NOW = "sync_now"
SERVICE_RESET_SLIDESHOW = "reset_slideshow"
SERVICE_REGENERATE_THUMBNAILS = "regenerate_thumbnails"
