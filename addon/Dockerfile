ARG BUILD_FROM
FROM $BUILD_FROM

# Build deps for pillow-heif (libheif needs C compiler); curl/jq/tzdata for
# shell scripts (update_all.sh in particular); git for pip-installing from source.
RUN apk add --no-cache \
        bash python3 py3-pip jq curl tzdata git \
        libheif libjpeg-turbo zlib \
    && apk add --no-cache --virtual .build-deps \
        gcc musl-dev python3-dev libheif-dev libjpeg-turbo-dev zlib-dev linux-headers \
    # Install icloudpd from SOURCE (not the pip-bundled binary) so the
    # pyicloud_ipd library is patchable. Pin to v1.32.2 tag for reproducibility.
    && pip3 install --no-cache-dir --break-system-packages \
        "icloudpd @ git+https://github.com/icloud-photos-downloader/icloud_photos_downloader.git@v1.32.2" \
        Pillow \
        pillow-heif \
    && apk del .build-deps

# Apply PR #1325 patch to pyicloud_ipd/sms.py
COPY cloud_photos/providers/icloud/patch_sms.py /tmp/patch_sms.py
RUN python3 /tmp/patch_sms.py && rm /tmp/patch_sms.py

# Remove git (no longer needed) — apk del above removed build-deps, this removes git too.
RUN apk del git

COPY run.sh /run.sh
COPY cloud_photos/ /opt/cloud_photos/
COPY static/ /opt/static/
RUN chmod a+x /run.sh

CMD ["/run.sh"]
