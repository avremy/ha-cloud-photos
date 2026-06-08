"""Cloud Photos HA integration — stub.

Phase 3 will populate this with:
- one-shot install: register Lovelace resource, create dashboard view,
  push the slideshow-card config.
- service entities: `cloud_photos.sync`, `cloud_photos.reset`,
  `cloud_photos.reauth` that drive the add-on's REST API.
- sensors: last sync time, photo count, auth state.

For now it's a placeholder so the repo layout matches the planned final shape.
"""
DOMAIN = "cloud_photos"


async def async_setup(hass, config):
    return True
