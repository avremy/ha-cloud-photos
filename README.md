# Cloud Photos

A Home Assistant add-on **+ companion custom integration** that syncs photos
from cloud providers into a local album and serves them as a fullscreen
slideshow and gallery inside HA.

Today it ships with one provider — **iCloud** (via [icloudpd][icloudpd]).
[Google Photos][google-photos] is planned.

[icloudpd]: https://github.com/icloud-photos-downloader/icloud_photos_downloader
[google-photos]: https://developers.google.com/photos

## Install (the short version)

After phase 3 the flow is:

1. **Add this repo** to Supervisor → Add-on Store (⋮ → Repositories → paste
   `https://github.com/avremy/ha-cloud-photos`).
2. **Install the Cloud Photos add-on**, configure iCloud creds, start it.
3. **Install the Cloud Photos integration** in Settings → Devices & Services
   → Add Integration → "Cloud Photos".
4. Done.

That last step picks up the running add-on, registers the slideshow-card,
adds a **Cloud Photos** badge to your default dashboard linking to the
gallery, and exposes services + sensors.

> _Screenshot placeholder — add-on panel + integration setup dialog + badge
> on dashboard._

## What's in the repo

```
cloud_photos/
  addon/                 Home Assistant add-on (the syncer + HTTP API)
  custom_components/
    cloud_photos/        Companion HA integration (auto-install + services)
  hacs.json              HACS metadata for the integration
  repository.yaml        HA add-on repository metadata
```

## Add-on: what it does

- Authenticates against the cloud provider (interactive once; cached session)
- Downloads new photos on a schedule or on demand
- Converts HEIC → JPG so the browser can display them
- Maintains an image-list JSON consumed by the slideshow card
- Serves an admin UI + a gallery page via Home Assistant Ingress
- Deploys its bundled web assets (`gallery.html`, `slideshow-card.js`) to
  `/config/www/cloud_photos/` so HA can serve them at stable `/local/`
  URLs — the add-on stays source-of-truth without depending on fragile
  ingress sub-paths for iframe content.

## Integration: what it does

After install, the integration:

- **Registers the Lovelace card.** `slideshow-card.js` is mounted at
  `/cloud_photos_static/slideshow-card.js?v=<addon_version>` and added via
  `frontend.add_extra_js_url()` — no manual resource entry needed in
  Settings → Dashboards → Resources.
- **Adds a gallery badge** to your default storage-mode dashboard (URL path
  `lovelace`). The badge has a marker so re-running setup never
  double-injects, and uninstall removes only badges that carry it.
- **Exposes three services**:
  - `cloud_photos.sync_now` — fetch new photos using the cached session.
  - `cloud_photos.reset_slideshow` — wipe + full re-download.
  - `cloud_photos.regenerate_thumbnails` — rescan + rebuild thumbs +
    refresh `_image_list.json`.
- **Adds two sensors**:
  - `sensor.cloud_photos_last_sync` — timestamp of the last finished sync.
  - `sensor.cloud_photos_sync_status` — `never_run | running | success |
    success_with_errors | failed | unknown`, with attributes for
    `started_at`, `files_downloaded`, `error`, and `running_job`.

### Notes / limitations

- **Default dashboard only.** The badge is added only to the user's default
  storage-mode dashboard. Other dashboards are left alone. If the default
  dashboard is still in strategy mode (HA hasn't generated a storage copy),
  the integration logs a notice and the badge isn't added — you can add it
  manually, or edit the dashboard once to convert it to storage mode and
  reload the integration.
- **Add-on must be installed first.** The integration's config flow
  detects the add-on via Supervisor. If the add-on isn't installed, the
  flow aborts with an actionable error.
- **Single instance.** One config entry per HA install. Reloading the
  integration is the supported way to pick up an add-on upgrade.

## Gallery (phase 2)

`addon/static/gallery.html` is a touch-first photo gallery served by the
add-on at `/local/cloud_photos/gallery.html` (after the deploy hook copies
it to HA's static dir).

Features:
- **[PhotoSwipe v5](https://photoswipe.com/)** vendored at
  `addon/static/vendor/photoswipe/` (no CDN — works on tailnet)
- **CSS-grid responsive masonry** — column width scales from 110px on phone
  to 200px on desktop; no JS layout library
- **Lazy loading** — every thumbnail has `loading="lazy"` and
  `decoding="async"`
- **Sticky monthly date headers** — photos grouped by `YYYY-MM` (from
  mtime), header sticks to top of viewport while scrolling that month
- **Pre-generated thumbnails** — 600px wide, JPEG q85, stored alongside
  originals as `<name>_thumb.jpg`. Gallery loads thumbs; PhotoSwipe loads
  the full image on tap.
- **🔄 Refresh button** — POSTs `/sync` to the add-on (host port 8099 via
  host_network), then polls the image list until it updates.

## Image-list generation

The add-on owns its own image-list pipeline now —
`cloud_photos.jobs.generate_image_list` scans `/config/www/cloud_photos/photos/`,
converts HEIC → JPG, generates `<name>_thumb.jpg` (600px wide, JPEG q85)
alongside each original, and writes `_image_list.json` with both `full` and
`thumb` URLs grouped by `YYYY-MM`.

Run it ad-hoc:

```bash
# Via the integration's service (easiest, from Developer Tools or automations):
service: cloud_photos.regenerate_thumbnails

# Or directly from the add-on container:
docker exec addon_local_cloud_photos \
  python3 -m cloud_photos.jobs.generate_image_list
```

The legacy `/config/scripts/generate_slideshow_yaml.py` on the live HA is
NOT used anymore. See [`MIGRATION.md`](MIGRATION.md) for the runbook that
retires it.

## Cutover from `ha_task_api`

If you're migrating an existing HA install away from the old `ha_task_api`
add-on, follow [`MIGRATION.md`](MIGRATION.md). It covers the three URL
swaps (slideshow card resource, gallery iframe url, photo storage path),
the phase-3 integration install, and the rollback procedure.

## Configuration options (add-on)

| Option            | Required | Default      | Description |
|-------------------|----------|--------------|-------------|
| `icloud_username` | yes      | —            | iCloud Apple ID email |
| `icloud_password` | first    | —            | Only needed for initial login; cookie is cached after |
| `icloud_album`    | no       | `Slideshow`  | iCloud album to sync (must be a **regular** album, not a shared one) |

## Endpoints (add-on REST API)

```
GET  /                       static admin UI
GET  /gallery                photo gallery (HTML)
GET  /health                 { ok, version, running_job, … }
GET  /last-sync              last sync outcome + counts
GET  /sync-log?n=200         tail of sync.log

POST /sync                   incremental sync (uses cached cookie)
POST /reset                  wipe + full re-download
POST /update-all             run /config/scripts/update_all.sh
POST /regenerate-thumbnails  rescan + rebuild thumbs + image list

POST /auth/start             launch icloudpd --auth-only (MFA webui)
GET  /auth/status            poll: idle | running | success | error
POST /auth/cancel            kill the auth child

/icloud/*                    reverse proxy to icloudpd's webui (port 8080)
```

Reachable via HA Ingress at the add-on panel, or directly on the host at
`http://<ha-host>:8099/`.

## License

MIT — see [`LICENSE`](LICENSE).
