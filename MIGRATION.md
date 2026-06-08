# Cutover runbook — `ha_task_api` → `cloud_photos`

This is the procedure to swap Avremy's live HA from the legacy
`local_ha_task_api` add-on (slideshow living under `/config/www/slideshow/`)
to the new `cloud_photos` add-on (slideshow under
`/config/www/cloud_photos/photos/`). Run from inside the **SSH add-on** on the
HA host (10.6.13.250). Everything is reversible — backups are made at each
step.

## Pre-flight

```bash
# Env
export SUPERVISOR_TOKEN=$(cat /run/s6/container_environment/SUPERVISOR_TOKEN)
HA=http://homeassistant.local:8123
SUP=http://supervisor

# Sanity
curl -sS -H "Authorization: Bearer $SUPERVISOR_TOKEN" $SUP/info | jq .data.version
curl -sS -o /dev/null -w "ha:%{http_code}\n" $HA
```

## 0. Backup everything we touch

```bash
ts=$(date +%Y%m%d-%H%M%S)
mkdir -p /backup/cutover-$ts
cp /config/www/slideshow-card.js              /backup/cutover-$ts/
cp /config/.storage/lovelace_resources        /backup/cutover-$ts/
cp /config/.storage/lovelace.dashboard_slideshow /backup/cutover-$ts/
cp -r /addons/ha_task_api                     /backup/cutover-$ts/ha_task_api.bak
```

## 1. Install the new add-on

```bash
# Copy the addon source into HA's local add-ons dir
rsync -av /share/cloud_photos/addon/   /addons/cloud_photos/

# Reload the store so HA sees it
curl -sS -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  $SUP/store/reload

# Install (build)
curl -sS -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  $SUP/addons/local_cloud_photos/install

# Configure options (carry-over from ha_task_api)
curl -sS -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"options":{"icloud_username":"<USER>","icloud_password":"","icloud_album":"Slideshow"}}' \
  $SUP/addons/local_cloud_photos/options

# Reuse the cached icloudpd cookie — no re-MFA required
# (cookie dir is shared at /config/.icloudpd_config)

# Start
curl -sS -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  $SUP/addons/local_cloud_photos/start

# Watch the boot log
curl -sS -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  $SUP/addons/local_cloud_photos/logs | tail -50
```

Expected log lines:
```
[cloud-photos] starting v0.1.0…
booting v0.1.0
[deploy] gallery.html -> /config/www/cloud_photos/gallery.html
[deploy] slideshow-card.js -> /config/www/cloud_photos/slideshow-card.js
listening on 127.0.0.1:8888 (legacy/host)
listening on 0.0.0.0:8099 (ingress)
ready
```

## 2. Migrate the photos

Cleanest is to move (not copy — saves disk):

```bash
mkdir -p /config/www/cloud_photos/photos
# Move every image (skip the generated index files we keep distinct now)
shopt -s nullglob
mv /config/www/slideshow/*.jpg  /config/www/cloud_photos/photos/ 2>/dev/null
mv /config/www/slideshow/*.JPG  /config/www/cloud_photos/photos/ 2>/dev/null
mv /config/www/slideshow/*.jpeg /config/www/cloud_photos/photos/ 2>/dev/null
mv /config/www/slideshow/*.png  /config/www/cloud_photos/photos/ 2>/dev/null
mv /config/www/slideshow/*.heic /config/www/cloud_photos/photos/ 2>/dev/null
mv /config/www/slideshow/*.HEIC /config/www/cloud_photos/photos/ 2>/dev/null
```

Trigger a first generate (creates `_image_list.json` + all thumbnails):

```bash
curl -sS -X POST $HA:8099/sync   # via host_network
# …or run the generator directly from inside the cloud_photos container:
docker exec addon_local_cloud_photos \
  python3 -m cloud_photos.jobs.generate_image_list
```

Verify:
```bash
curl -sS $HA/local/cloud_photos/_image_list.json | jq '{count, sample: .photos[:2]}'
curl -sS -o /dev/null -w "%{http_code}\n" $HA/local/cloud_photos/gallery.html
curl -sS -o /dev/null -w "%{http_code}\n" $HA/local/cloud_photos/slideshow-card.js
```

## 3. Swap the Lovelace resource (cutover item 3)

The old resource URL `/local/slideshow-card.js?v=7` is gone — the file moved.
We need to **add a new resource** at `/local/cloud_photos/slideshow-card.js?v=1`
**and remove** the old entry. The Lovelace resource API:

```bash
# List current resources to find the old one's resource_id
curl -sS -H "Authorization: Bearer <LL_LONG_LIVED_TOKEN>" \
  -H "Content-Type: application/json" \
  $HA/api/services/frontend/get_resources 2>/dev/null
# (If the REST helper isn't exposed, edit /config/.storage/lovelace_resources directly.)
```

Easiest: edit the storage file (HA Core restart is needed afterwards anyway).
The file is a single JSON document; the `data.items` array holds resources.

```bash
cp /config/.storage/lovelace_resources /config/.storage/lovelace_resources.bak.pre-cloud_photos
python3 <<'PY'
import json, pathlib
p = pathlib.Path("/config/.storage/lovelace_resources")
doc = json.loads(p.read_text())
items = doc["data"]["items"]
# Drop any reference to the legacy URL
items = [r for r in items if not r["url"].startswith("/local/slideshow-card.js")]
# Add the new one if missing
if not any(r["url"].startswith("/local/cloud_photos/slideshow-card.js") for r in items):
    items.append({"id": "cloud_photos", "type": "module",
                  "url": "/local/cloud_photos/slideshow-card.js?v=1"})
doc["data"]["items"] = items
p.write_text(json.dumps(doc, indent=2))
print("ok")
PY
```

## 4. Swap the gallery iframe URL (cutover item 2)

In `/config/.storage/lovelace.dashboard_slideshow`, the `gallery` view's
single iframe card has `url: /local/slideshow/gallery.html`. Repoint:

```bash
cp /config/.storage/lovelace.dashboard_slideshow \
   /config/.storage/lovelace.dashboard_slideshow.bak.pre-cloud_photos
sed -i 's|/local/slideshow/gallery.html|/local/cloud_photos/gallery.html|g' \
   /config/.storage/lovelace.dashboard_slideshow
```

Verify the diff:
```bash
diff /config/.storage/lovelace.dashboard_slideshow.bak.pre-cloud_photos \
     /config/.storage/lovelace.dashboard_slideshow
```

## 5. Restart HA Core

Storage-file edits are cold; Core has to reload.

```bash
ha core restart
# Wait
until curl -fsS -o /dev/null $HA; do sleep 2; done
echo "core back"
```

## 6. Retire the old add-on + script (cutover item 1)

The legacy generator `/config/scripts/generate_slideshow_yaml.py` is no
longer in the path — `cloud_photos.jobs.generate_image_list` replaces it. To
catch any external caller still pointing at the old script, replace it with a
forwarder (don't just delete — the daily automation might still reference it):

```bash
cp /config/scripts/generate_slideshow_yaml.py /backup/cutover-$ts/

cat > /config/scripts/generate_slideshow_yaml.py <<'PY'
#!/usr/bin/env python3
"""Legacy entry point — forwarded to cloud_photos.jobs.generate_image_list.

Kept so the daily automation + any external cron keeps working after the
cloud_photos cutover. Safe to delete once nothing else references it.
"""
import subprocess, sys
sys.exit(subprocess.call([
    "docker", "exec", "addon_local_cloud_photos",
    "python3", "-m", "cloud_photos.jobs.generate_image_list",
]))
PY
chmod +x /config/scripts/generate_slideshow_yaml.py
```

(This forwarder is only callable from contexts with Docker — i.e. from inside
the SSH add-on or the worker, NOT from inside the HA Core container. That's
the same constraint as everything else in this codebase. If you'd rather
truly retire it, point HA's `shell_command.generate_slideshow_list` directly
at the trigger-file worker.)

Stop & uninstall the legacy add-on:

```bash
curl -sS -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  $SUP/addons/local_ha_task_api/stop
curl -sS -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  $SUP/addons/local_ha_task_api/uninstall
```

## 7. Post-cutover verification

```bash
# 1. Static assets all live in the new home
for url in /local/cloud_photos/gallery.html \
           /local/cloud_photos/slideshow-card.js \
           /local/cloud_photos/_image_list.json; do
  code=$(curl -sS -o /dev/null -w "%{http_code}" $HA$url)
  echo "$code  $url"
done

# 2. Image list looks healthy
curl -sS $HA/local/cloud_photos/_image_list.json \
  | jq '{count, version, sample: .photos[0]}'

# 3. Slideshow card resource is registered
grep -o '"url":"[^"]*"' /config/.storage/lovelace_resources

# 4. Slideshow dashboard iframe URL is updated
grep -o '"url":"[^"]*"' /config/.storage/lovelace.dashboard_slideshow | head

# 5. Gallery page actually renders
# Open on iPad: http://homeassistant.local:8123/slideshow/gallery
# Should show the PhotoSwipe-powered grid, sticky month headers,
# and a 🔄 button top-right.
```

## Rollback

If anything looks wrong:

```bash
# Restore the three Lovelace files + the old generator
cp /backup/cutover-$ts/lovelace_resources           /config/.storage/
cp /backup/cutover-$ts/lovelace.dashboard_slideshow /config/.storage/
cp /backup/cutover-$ts/slideshow-card.js            /config/www/
cp /backup/cutover-$ts/generate_slideshow_yaml.py   /config/scripts/  # if you forwarded

# Photos: move them back
mv /config/www/cloud_photos/photos/*.jpg /config/www/slideshow/ 2>/dev/null
# …repeat for other extensions

# Stop new addon, start old
curl -sS -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  $SUP/addons/local_cloud_photos/stop
curl -sS -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  $SUP/addons/local_ha_task_api/start

ha core restart
```

## Cleanup (a week later, after we trust the new setup)

```bash
rm -rf /config/www/slideshow                  # empty by now
rm /config/scripts/generate_slideshow_yaml.py # the forwarder
rm -rf /addons/ha_task_api                    # already uninstalled, just disk reclaim
```
