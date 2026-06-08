# Cloud Photos

A Home Assistant add-on + companion custom integration that syncs photos from
cloud providers into a local album and serves them as a fullscreen slideshow
and gallery inside HA.

Today it ships with one provider — **iCloud** (via [icloudpd][icloudpd]).
[Google Photos][google-photos] is planned.

[icloudpd]: https://github.com/icloud-photos-downloader/icloud_photos_downloader
[google-photos]: https://developers.google.com/photos

## What's in the repo

```
cloud_photos/
  addon/                 Home Assistant add-on (the syncer + HTTP API)
  custom_components/     Companion HA integration (auto-install glue, services)
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

## Custom component (companion)

> **Status:** Phase 1 ships a stub. The auto-install logic lands in Phase 3.

Once enabled, the integration will:
- Register the Lovelace resource
- Create the slideshow dashboard view
- Expose `cloud_photos.sync`, `cloud_photos.reset`, `cloud_photos.reauth`
  services that drive the add-on's REST API
- Surface sensors for last-sync time, photo count, auth state

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
  host_network), then polls the image list until it updates

## Image-list generation

The add-on owns its own image-list pipeline now —
`cloud_photos.jobs.generate_image_list` scans `/config/www/cloud_photos/photos/`,
converts HEIC → JPG, generates `<name>_thumb.jpg` (600px wide, JPEG q85)
alongside each original, and writes `_image_list.json` with both `full` and
`thumb` URLs grouped by `YYYY-MM`.

Run it ad-hoc:

```bash
docker exec addon_local_cloud_photos \
  python3 -m cloud_photos.jobs.generate_image_list
```

The legacy `/config/scripts/generate_slideshow_yaml.py` on the live HA is
NOT used anymore. See [`MIGRATION.md`](MIGRATION.md) for the runbook that
retires it.

## Cutover from `ha_task_api`

If you're migrating an existing HA install away from the old `ha_task_api`
add-on, follow [`MIGRATION.md`](MIGRATION.md). It covers the three URL
swaps (slideshow card resource, gallery iframe url, photo storage path) and
the rollback procedure.

## Installation

### Add-on (manual, until the repo is published)

1. Copy `addon/` into `/addons/cloud_photos/` on your HA host.
2. Settings → Add-ons → Add-on Store → ⋮ → Reload.
3. Find **Cloud Photos** in the local add-ons section → Install.
4. Configure your iCloud username, password (for first-time MFA), and album
   name (defaults to `Slideshow`).
5. Start the add-on. Open its panel and click **Re-authenticate** to do the
   one-time iCloud login + MFA via icloudpd's webui.
6. After auth succeeds, the daily sync (or manual **Sync** button) will start
   downloading photos.

### Integration

Phase 3 will add HACS install + a config flow. For now the directory is a
placeholder.

## Configuration options (add-on)

| Option            | Required | Default      | Description |
|-------------------|----------|--------------|-------------|
| `icloud_username` | yes      | —            | iCloud Apple ID email |
| `icloud_password` | first    | —            | Only needed for initial login; cookie is cached after |
| `icloud_album`    | no       | `Slideshow`  | iCloud album to sync (must be a **regular** album, not a shared one) |

## Endpoints (add-on REST API)

```
GET  /                static admin UI
GET  /gallery         photo gallery (HTML)
GET  /health          { ok, version, running_job, … }
GET  /last-sync       last sync outcome + counts
GET  /sync-log?n=200  tail of sync.log

POST /sync            incremental sync (uses cached cookie)
POST /reset           wipe + full re-download
POST /update-all      run /config/scripts/update_all.sh

POST /auth/start      launch icloudpd --auth-only (MFA webui)
GET  /auth/status     poll: idle | running | success | error
POST /auth/cancel     kill the auth child

/icloud/*             reverse proxy to icloudpd's webui (port 8080)
```

Reachable via HA Ingress at the add-on panel, or directly on the host at
`http://<ha-host>:8099/`.

## License

MIT — see [`LICENSE`](LICENSE).
