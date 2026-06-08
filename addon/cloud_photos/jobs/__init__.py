"""Background jobs for cloud_photos.

Each module owns one job kind that the HTTP server invokes:

- `generate_image_list` — scan the photos dir, convert HEIC → JPG, generate
  thumbnails, and write the `_image_list.json` consumed by gallery.html and
  the slideshow card.
"""
