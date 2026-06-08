"""Apply PR #1325 to pyicloud_ipd/sms.py — handles Apple's new trustedPhoneNumbers
location (bridgeInitiateData). Idempotent."""
import os, sys

try:
    import pyicloud_ipd
except ImportError as e:
    print(f"!! pyicloud_ipd not importable: {e}", file=sys.stderr); sys.exit(1)

sms_py = os.path.join(os.path.dirname(pyicloud_ipd.__file__), "sms.py")
print(f"Patching {sms_py}")

src = open(sms_py).read()

# Already patched?
if "bridgeInitiateData" in src:
    print("Already patched, skipping.")
    sys.exit(0)

old = '''    numbers: Sequence[Mapping[str, Any]] = (
        parser.sms_data.get("direct", {})
        .get("twoSV", {})
        .get("phoneNumberVerification", {})
        .get("trustedPhoneNumbers", [])
    )'''

new = '''    # Patched per https://github.com/icloud-photos-downloader/icloud_photos_downloader/pull/1325
    # Apple moved trustedPhoneNumbers under bridgeInitiateData around iOS 26.4.
    numbers: Sequence[Mapping[str, Any]] = (
        parser.sms_data.get("direct", {})
        .get("twoSV", {})
        .get("bridgeInitiateData", {})
        .get("phoneNumberVerification", {})
        .get("trustedPhoneNumbers", [])
        or parser.sms_data.get("direct", {})
        .get("twoSV", {})
        .get("phoneNumberVerification", {})
        .get("trustedPhoneNumbers", [])
    )'''

if old not in src:
    print("!! PATCH ANCHOR NOT FOUND in sms.py — pyicloud_ipd source changed?", file=sys.stderr)
    print("---", file=sys.stderr); print(src, file=sys.stderr)
    sys.exit(1)

open(sms_py, 'w').write(src.replace(old, new))
print("Patched OK")
