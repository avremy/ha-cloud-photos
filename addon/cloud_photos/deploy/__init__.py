"""Deploy bundled web assets to HA's `/config/www/cloud_photos/`.

The addon ships `gallery.html` and `slideshow-card.js` inside its container
(`/opt/static/`). HA's frontend can only serve files from `/config/www/`
(reachable at `/local/`). Ingress sub-paths are not a reliable iframe target
(see Homie's 2026-06-08 lesson: `/hassio/ingress/<slug>/*` returns 404 from
HA's web server; only the frontend SPA router resolves those URLs).

So at addon startup we copy our bundled assets into `/config/www/cloud_photos/`,
where they're reachable as `/local/cloud_photos/<file>`. The addon stays
source-of-truth; HA's static dir is just a deploy target.

Idempotent: overwrites on every startup so upgrades pick up the new bytes.
"""
import os
import shutil

# Top-level files we deploy from /opt/static/ to /config/www/cloud_photos/.
DEPLOYED_FILES = (
    "gallery.html",
    "slideshow-card.js",
)

# Whole subtrees we deploy (recursive copy). Used for vendored libraries —
# PhotoSwipe etc.
DEPLOYED_DIRS = (
    "vendor",
)


def deploy(static_dir, target_dir, log):
    """Copy bundled assets from static_dir into target_dir.

    Idempotent: files are overwritten, the vendor dir is fully refreshed.
    `log(msg)` is a callback the caller provides for status logging.
    """
    os.makedirs(target_dir, exist_ok=True)

    for name in DEPLOYED_FILES:
        src = os.path.join(static_dir, name)
        dst = os.path.join(target_dir, name)
        if not os.path.isfile(src):
            log(f"[deploy] skip {name}: not found in {static_dir}")
            continue
        try:
            shutil.copyfile(src, dst)
            log(f"[deploy] {name} -> {dst}")
        except OSError as e:
            log(f"[deploy] !! {name} failed: {e}")

    for name in DEPLOYED_DIRS:
        src = os.path.join(static_dir, name)
        dst = os.path.join(target_dir, name)
        if not os.path.isdir(src):
            log(f"[deploy] skip dir {name}: not found in {static_dir}")
            continue
        try:
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            log(f"[deploy] {name}/ -> {dst}/")
        except OSError as e:
            log(f"[deploy] !! {name}/ failed: {e}")
