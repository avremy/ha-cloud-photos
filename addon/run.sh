#!/command/with-contenv bash
set -e

echo "[cloud-photos] starting v0.1.0…"

# Token plumbing (supports both old and new supervisor naming)
if [ -z "${SUPERVISOR_TOKEN:-}" ] && [ -n "${HASSIO_TOKEN:-}" ]; then
  export SUPERVISOR_TOKEN="$HASSIO_TOKEN"
fi
[ -n "${SUPERVISOR_TOKEN:-}" ] && echo "[cloud-photos] SUPERVISOR_TOKEN: set" || echo "[cloud-photos] SUPERVISOR_TOKEN: MISSING"

# Mirror the token into /run/s6/container_environment so /config/scripts/update_all.sh
# still works unchanged.
mkdir -p /run/s6/container_environment
if [ -n "${SUPERVISOR_TOKEN:-}" ] && [ ! -s /run/s6/container_environment/SUPERVISOR_TOKEN ]; then
  printf '%s' "$SUPERVISOR_TOKEN" > /run/s6/container_environment/SUPERVISOR_TOKEN
fi

# Pull add-on options into env vars for the Python process.
if [ -f /data/options.json ]; then
  export ICLOUD_USERNAME="$(jq -r '.icloud_username // ""' /data/options.json)"
  export ICLOUD_PASSWORD="$(jq -r '.icloud_password // ""' /data/options.json)"
  export ICLOUD_ALBUM="$(jq -r '.icloud_album // "Slideshow"' /data/options.json)"
  echo "[cloud-photos] icloud user=${ICLOUD_USERNAME:-MISSING} album=${ICLOUD_ALBUM:-MISSING} password=$([ -n "$ICLOUD_PASSWORD" ] && echo set || echo MISSING)"
fi

mkdir -p /config/www /config/.slideshow_triggers /config/www/cloud_photos /config/www/cloud_photos/photos /config/.icloudpd_config

cd /opt
exec python3 -m cloud_photos.server
