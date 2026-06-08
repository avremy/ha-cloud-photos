"""Scan the photos dir and emit `_image_list.json` (with thumbnails).

This is the in-repo replacement for the live host's legacy
`/config/scripts/generate_slideshow_yaml.py`. The new path:

    photos    /config/www/cloud_photos/photos/
    list      /config/www/cloud_photos/_image_list.json
    thumbs    photos/<name>_thumb.jpg  (alongside originals)

Steps per run:

1. Convert any `*.HEIC` / `*.heic` to `*.jpg` (deletes the HEIC after).
2. For each image, ensure a `<name>_thumb.jpg` exists (600px wide, JPEG q85).
3. Sort by mtime descending (newest first), bucket by `YYYY-MM` for the
   gallery's date-group headers, and write `_image_list.json` with both
   `full` and `thumb` URLs plus an mtime ISO timestamp.

Public entry point is `run(photos_dir, list_path, log) -> int` (returncode).
Also runnable as `python3 -m cloud_photos.jobs.generate_image_list` for
ad-hoc invocation from the SSH add-on or a cron.
"""
import datetime
import json
import os
import sys

PHOTOS_DIR_DEFAULT = "/config/www/cloud_photos/photos"
LIST_PATH_DEFAULT  = "/config/www/cloud_photos/_image_list.json"
URL_PREFIX         = "/local/cloud_photos/photos"

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
THUMB_SUFFIX = "_thumb.jpg"
THUMB_WIDTH  = 600
THUMB_QUALITY = 85


def _ensure_pillow(log):
    try:
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        log("[gen] !! Pillow not installed — thumbnails + HEIC conversion skipped")
        return False


def _ensure_heif(log):
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return True
    except ImportError:
        log("[gen] !! pillow-heif not installed — HEIC files will be left as-is")
        return False


def _convert_heic(photos_dir, log):
    """Convert every *.HEIC/*.heic in photos_dir to .jpg; delete the HEIC."""
    from PIL import Image
    n = 0
    for name in os.listdir(photos_dir):
        if not name.lower().endswith((".heic",)):
            continue
        src = os.path.join(photos_dir, name)
        dst = os.path.join(photos_dir, os.path.splitext(name)[0] + ".jpg")
        if os.path.exists(dst):
            try:
                os.remove(src)
            except OSError:
                pass
            continue
        try:
            with Image.open(src) as im:
                im.convert("RGB").save(dst, "JPEG", quality=90)
            os.remove(src)
            n += 1
        except Exception as e:
            log(f"[gen] !! HEIC convert {name}: {e}")
    if n:
        log(f"[gen] converted {n} HEIC -> JPG")


def _thumb_path(full_path):
    base, _ = os.path.splitext(full_path)
    return base + THUMB_SUFFIX


def _is_thumb(name):
    return name.lower().endswith(THUMB_SUFFIX)


def _ensure_thumb(full_path, log):
    """Create the thumb if missing or stale. Returns True if a thumb now exists."""
    thumb = _thumb_path(full_path)
    try:
        if os.path.exists(thumb) and os.path.getmtime(thumb) >= os.path.getmtime(full_path):
            return True
    except OSError:
        pass
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(full_path) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w > THUMB_WIDTH:
                new_h = int(h * (THUMB_WIDTH / w))
                im = im.resize((THUMB_WIDTH, new_h), Image.LANCZOS)
            im.save(thumb, "JPEG", quality=THUMB_QUALITY, optimize=True)
        return True
    except Exception as e:
        log(f"[gen] !! thumb {os.path.basename(full_path)}: {e}")
        return False


def run(photos_dir=PHOTOS_DIR_DEFAULT, list_path=LIST_PATH_DEFAULT, log=print):
    """Generate `_image_list.json`. Returns 0 on success, non-zero on failure."""
    if not os.path.isdir(photos_dir):
        log(f"[gen] !! photos_dir missing: {photos_dir}")
        return 2

    have_pillow = _ensure_pillow(log)
    if have_pillow:
        _ensure_heif(log)
        _convert_heic(photos_dir, log)

    entries = []
    for name in sorted(os.listdir(photos_dir)):
        if _is_thumb(name):
            continue
        if not name.lower().endswith(IMAGE_EXTS):
            continue
        full = os.path.join(photos_dir, name)
        if not os.path.isfile(full):
            continue
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        thumb_ok = _ensure_thumb(full, log) if have_pillow else False
        thumb_name = os.path.splitext(name)[0] + THUMB_SUFFIX
        entries.append({
            "name":  name,
            "full":  f"{URL_PREFIX}/{name}",
            "thumb": f"{URL_PREFIX}/{thumb_name}" if thumb_ok else f"{URL_PREFIX}/{name}",
            "mtime": mtime,
            "iso":   datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            "group": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m"),
        })

    # Newest first.
    entries.sort(key=lambda e: e["mtime"], reverse=True)

    # Back-compat: the old slideshow-card.js reads `data.images` as a flat list
    # of URL strings. Keep that key for the slideshow, add `photos` for the
    # new gallery.
    payload = {
        "version":   2,
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "count":     len(entries),
        "images":    [e["full"] for e in entries],
        "photos":    entries,
    }

    os.makedirs(os.path.dirname(list_path), exist_ok=True)
    tmp = list_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, list_path)
    log(f"[gen] wrote {len(entries)} entries to {list_path}")
    return 0


def main(argv):
    photos = argv[1] if len(argv) > 1 else PHOTOS_DIR_DEFAULT
    listp  = argv[2] if len(argv) > 2 else LIST_PATH_DEFAULT
    return run(photos, listp, log=print)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
