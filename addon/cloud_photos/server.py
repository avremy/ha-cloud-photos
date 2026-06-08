#!/usr/bin/env python3
"""
Cloud Photos add-on — HTTP server.

(Renamed + repackaged from the v3.2.0 `ha_task_api/ha_api.py` server. Provider-
specific code now lives under `cloud_photos.providers.<name>`; today the only
provider is `icloud`.)

iCloud auth uses icloudpd's own --mfa-provider webui mode. The icloudpd webui
listens on 127.0.0.1:8080 inside the container; we reverse-proxy it through
our own ingress at /icloud/. The user enters password + MFA once;
icloudpd writes a session cookie that lasts ~30 days. We never store the
password ourselves.

Endpoints:
    GET  /                       static UI (admin)
    GET  /gallery                photo gallery
    GET  /static/*               static assets
    GET  /health                 service status

    POST /sync                   incremental download using cached cookie
    POST /reset                  wipe + full download using cached cookie
    POST /update-all             /config/scripts/update_all.sh

    POST /auth/start             launch interactive icloudpd --auth-only
    GET  /auth/status            poll: idle | running | success | error
    POST /auth/cancel            kill the auth child if stuck

    /icloud/*                    transparent reverse proxy to icloudpd:8080
                                 (this is where icloudpd's own MFA UI lives)
"""
import datetime
import http.client
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import threading
from urllib.parse import urlparse

from . import __version__
from .providers import icloud as icloud_provider
from .deploy import deploy as deploy_assets
from .jobs import generate_image_list as gen_list

PORT          = 8888
BIND          = "127.0.0.1"
INGRESS_PORT  = 8099
WWW_DEPLOY_DIR = "/config/www/cloud_photos"
PHOTOS_DIR    = os.path.join(WWW_DEPLOY_DIR, "photos")
IMAGE_LIST    = os.path.join(WWW_DEPLOY_DIR, "_image_list.json")
LOGFILE       = "/config/www/api.log"
SYNC_LOG      = os.path.join(WWW_DEPLOY_DIR, "sync.log")
COOKIE_DIR    = "/config/.icloudpd_config"
STATIC_DIR    = "/opt/static"

# Back-compat alias — many call sites still refer to "the slideshow dir" but
# we now stash photos under /config/www/cloud_photos/photos.
SLIDESHOW_DIR = PHOTOS_DIR

RESET_KEEP = set()  # photos dir only holds photos; nothing to keep on reset

def env(k, d=""):
    return os.environ.get(k, d)

_log_lock     = threading.Lock()
_job_lock     = threading.Lock()
_job_running  = None

# ---- logging --------------------------------------------------------------

def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    try:
        with _log_lock, open(LOGFILE, "a") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"!! log write failed: {e}", flush=True)

def synclog(msg):
    try:
        os.makedirs(WWW_DEPLOY_DIR, exist_ok=True)
        with open(SYNC_LOG, "a") as f:
            f.write(msg.rstrip() + "\n")
    except Exception as e:
        log(f"!! sync.log write failed: {e}")

# ---- slideshow ops --------------------------------------------------------

def _run_log(cmd, env_=None):
    """Run cmd, stream stdout+stderr into sync.log."""
    log(f"$ {cmd[0]} {' '.join(a for a in cmd[1:] if not a.startswith('-')) or '…'}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env_)
    for line in proc.stdout:
        synclog(line)
    proc.wait()
    return proc.returncode

def cleanup_videos():
    n = 0
    if not os.path.isdir(PHOTOS_DIR):
        return
    for f in os.listdir(PHOTOS_DIR):
        if f.lower().endswith((".mov", ".mp4", ".m4v")):
            try: os.remove(os.path.join(PHOTOS_DIR, f)); n += 1
            except OSError: pass
    if n: log(f"cleaned up {n} video file(s)")

def regenerate_image_list():
    """Scan PHOTOS_DIR, refresh thumbnails, write IMAGE_LIST. Returns rc."""
    return gen_list.run(PHOTOS_DIR, IMAGE_LIST, synclog)

def wipe_slideshow_files():
    n = 0
    if not os.path.isdir(PHOTOS_DIR):
        return
    for f in os.listdir(PHOTOS_DIR):
        if f in RESET_KEEP: continue
        p = os.path.join(PHOTOS_DIR, f)
        try:
            if os.path.isfile(p) or os.path.islink(p): os.unlink(p); n += 1
            elif os.path.isdir(p): shutil.rmtree(p); n += 1
        except OSError as e:
            log(f"!! couldn't delete {p}: {e}")
    log(f"wiped {n} file(s)")

# ---- job runners ----------------------------------------------------------

def run_sync_job():
    if not _job_lock.acquire(blocking=False):
        log("sync: busy"); return
    global _job_running; _job_running = "sync"
    try:
        synclog("=" * 42)
        synclog(f"Sync started: {datetime.datetime.now()}")
        synclog("=" * 42)
        log("=== SYNC started ===")
        rc = icloud_provider.sync(SLIDESHOW_DIR, COOKIE_DIR, synclog)
        cleanup_videos()
        regenerate_image_list()
        synclog(f"Sync completed (icloudpd rc={rc}): {datetime.datetime.now()}\n")
        log(f"=== SYNC completed (icloudpd rc={rc}) ===")
    except Exception as e:
        log(f"!! sync ERROR: {e}"); synclog(f"ERROR: {e}")
    finally:
        _job_running = None; _job_lock.release()

def run_reset_job():
    if not _job_lock.acquire(blocking=False):
        log("reset: busy"); return
    global _job_running; _job_running = "reset"
    try:
        synclog("=" * 42)
        synclog(f"FULL RESET started: {datetime.datetime.now()}")
        synclog("=" * 42)
        log("=== RESET started ===")
        wipe_slideshow_files()
        rc = icloud_provider.sync(SLIDESHOW_DIR, COOKIE_DIR, synclog)
        cleanup_videos()
        regenerate_image_list()
        synclog(f"Full reset completed (icloudpd rc={rc}): {datetime.datetime.now()}\n")
        log(f"=== RESET completed (icloudpd rc={rc}) ===")
    except Exception as e:
        log(f"!! reset ERROR: {e}"); synclog(f"ERROR: {e}")
    finally:
        _job_running = None; _job_lock.release()

def run_update_all_job():
    if not _job_lock.acquire(blocking=False):
        log("update-all: busy"); return
    global _job_running; _job_running = "update-all"
    try:
        log("=== UPDATE-ALL started ===")
        rc = _run_log(["/bin/bash", "/config/scripts/update_all.sh"])
        log(f"=== UPDATE-ALL completed (rc={rc}) ===")
    except Exception as e:
        log(f"!! update-all ERROR: {e}")
    finally:
        _job_running = None; _job_lock.release()

def run_regenerate_thumbs_job():
    """Rescan PHOTOS_DIR + rebuild thumbs + image list. No network I/O."""
    if not _job_lock.acquire(blocking=False):
        log("regenerate-thumbnails: busy"); return
    global _job_running; _job_running = "regenerate-thumbnails"
    try:
        synclog("=" * 42)
        synclog(f"Regenerate thumbnails started: {datetime.datetime.now()}")
        synclog("=" * 42)
        log("=== REGEN-THUMBS started ===")
        rc = regenerate_image_list()
        synclog(f"Regenerate thumbnails completed (rc={rc}): {datetime.datetime.now()}\n")
        log(f"=== REGEN-THUMBS completed (rc={rc}) ===")
    except Exception as e:
        log(f"!! regenerate-thumbnails ERROR: {e}"); synclog(f"ERROR: {e}")
    finally:
        _job_running = None; _job_lock.release()

def last_sync_status():
    """Parse sync.log and return the most recent run's outcome."""
    out = {
        "status": "never_run",       # never_run | running | success | failed | unknown
        "finished_at": None,
        "started_at": None,
        "files_downloaded": 0,
        "error": None,
        "running_job": _job_running,
    }
    try:
        with open(SYNC_LOG, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return out
    if not lines:
        return out
    # Walk backwards to find the last "Sync completed" / "Full reset completed",
    # and forward from there to find the matching start + counts.
    last_start_idx = -1
    last_end_idx = -1
    last_end_rc = None
    for i in range(len(lines) - 1, -1, -1):
        l = lines[i]
        if last_end_idx == -1 and ("completed" in l.lower() and "icloudpd rc=" in l):
            last_end_idx = i
            try:
                last_end_rc = int(l.split("rc=", 1)[1].split(")", 1)[0])
            except (IndexError, ValueError):
                last_end_rc = None
            try:
                import re as _re
                m = _re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", l)
                if m: out["finished_at"] = m.group(1)
            except Exception: pass
        if last_start_idx == -1 and ("Sync started" in l or "FULL RESET started" in l):
            last_start_idx = i
            import re as _re
            m = _re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", l)
            if m: out["started_at"] = m.group(1)
        if last_start_idx != -1 and last_end_idx != -1:
            break

    end = last_end_idx if last_end_idx != -1 else len(lines)
    start = last_start_idx if last_start_idx != -1 else 0
    out["files_downloaded"] = sum(1 for l in lines[start:end] if "Downloaded " in l)

    errors = [l.strip() for l in lines[start:end] if "ERROR" in l]
    if errors:
        out["error"] = errors[-1]

    if _job_running:
        out["status"] = "running"
    elif last_end_idx == -1:
        if last_start_idx != -1: out["status"] = "unknown"
    elif last_end_rc == 0 and not errors:
        out["status"] = "success"
    elif last_end_rc == 0 and errors:
        out["status"] = "success_with_errors"
    else:
        out["status"] = "failed"

    return out

JOBS = {
    "/sync":                  ("sync",                  run_sync_job),
    "/reset":                 ("reset",                 run_reset_job),
    "/update-all":            ("update-all",            run_update_all_job),
    "/regenerate-thumbnails": ("regenerate-thumbnails", run_regenerate_thumbs_job),
}

# ---- reverse proxy to icloudpd webui --------------------------------------

PROXY_PREFIX = "/icloud/"

def proxy_to_icloudpd(handler, method):
    """Forward handler's request to 127.0.0.1:8080, rewriting absolute paths in
    HTML/JS/HX responses so the iframe content stays under our /icloud/ prefix."""
    src_path = handler.path
    if not src_path.startswith(PROXY_PREFIX):
        handler._json(400, {"error": "bad proxy path"}); return
    dst_path = "/" + src_path[len(PROXY_PREFIX):]
    if not dst_path or dst_path == "/" : dst_path = "/"

    length = int(handler.headers.get("Content-Length", "0") or 0)
    body = handler.rfile.read(length) if length > 0 else None

    drop = {"host", "connection", "content-length", "transfer-encoding",
            "accept-encoding", "upgrade", "proxy-connection",
            "x-ingress-path", "x-forwarded-for", "x-forwarded-host",
            "x-forwarded-proto", "x-hass-source"}
    fwd_headers = {k: v for k, v in handler.headers.items() if k.lower() not in drop}

    try:
        conn = http.client.HTTPConnection("127.0.0.1", icloud_provider.ICLOUDPD_PORT, timeout=30)
        conn.request(method, dst_path, body=body, headers=fwd_headers)
        upstream = conn.getresponse()
    except (ConnectionRefusedError, OSError) as e:
        handler._json(502, {"error": "icloudpd webui not running",
                            "hint": "POST /auth/start first",
                            "detail": str(e)}); return

    ctype = (upstream.getheader("Content-Type") or "").lower()
    is_html = "text/html" in ctype
    if is_html:
        payload = upstream.read()
        try: text = payload.decode("utf-8")
        except UnicodeDecodeError: text = payload.decode("latin-1")
        # Rewrite absolute-path references so they stay inside the iframe.
        # icloudpd uses href="/static/...", src="/static/...", hx-get="/status",
        # hx-post="/post-password" etc.
        for attr in ("href", "src", "hx-get", "hx-post", "hx-put",
                     "hx-delete", "hx-target", "action", "formaction"):
            text = re.sub(
                rf'({attr}=")(/)([^"\\#][^"]*)',
                lambda m: f'{m.group(1)}./{m.group(3)}',
                text, flags=re.IGNORECASE,
            )
        payload = text.encode("utf-8")
        new_len = len(payload)
        handler.send_response(upstream.status)
        for k, v in upstream.getheaders():
            kl = k.lower()
            if kl in {"transfer-encoding", "connection", "content-length"}: continue
            if kl == "location" and v and v.startswith("/"):
                v = "./" + v.lstrip("/")
            handler.send_header(k, v)
        handler.send_header("Content-Length", str(new_len))
        handler.end_headers()
        try: handler.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError): pass
    else:
        handler.send_response(upstream.status)
        for k, v in upstream.getheaders():
            kl = k.lower()
            if kl in {"transfer-encoding", "connection"}: continue
            if kl == "location" and v and v.startswith("/"):
                v = "./" + v.lstrip("/")
            handler.send_header(k, v)
        handler.end_headers()
        while True:
            chunk = upstream.read(65536)
            if not chunk: break
            try: handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError): break
    upstream.close(); conn.close()

# ---- HTTP -----------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_static("index.html"); return
        if path in ("/gallery", "/gallery/"):
            self._serve_static("gallery.html"); return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):]); return
        if path.startswith(PROXY_PREFIX):
            proxy_to_icloudpd(self, "GET"); return
        if path == "/last-sync":
            self._json(200, last_sync_status()); return
        if path == "/sync-log":
            try:
                n = int(urlparse(self.path).query.split("n=", 1)[-1].split("&")[0]) if "n=" in self.path else 200
            except (ValueError, IndexError):
                n = 200
            n = max(1, min(n, 2000))
            try:
                with open(SYNC_LOG, "r") as f:
                    lines = f.readlines()
                self._json(200, {
                    "lines": [l.rstrip("\n") for l in lines[-n:]],
                    "total_lines": len(lines),
                    "running_job": _job_running,
                })
            except FileNotFoundError:
                self._json(200, {"lines": [], "total_lines": 0, "running_job": _job_running})
            return
        if path == "/health":
            self._json(200, {
                "ok": True, "service": "cloud-photos", "version": __version__,
                "running_job": _job_running,
                "icloud_user_set": bool(env("ICLOUD_USERNAME")),
                "session_cookie_present": icloud_provider.session_cookie_present(COOKIE_DIR),
                "auth_state": icloud_provider.AUTH.state,
                "icloudpd_webui_alive": icloud_provider.icloudpd_webui_alive(),
            }); return
        if path == "/auth/status":
            self._json(200, icloud_provider.auth_status(COOKIE_DIR)); return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith(PROXY_PREFIX):
            proxy_to_icloudpd(self, "POST"); return
        if path in JOBS:
            name, fn = JOBS[path]
            if _job_running is not None:
                self._json(429, {"status":"busy","running_job":_job_running}); return
            threading.Thread(target=fn, daemon=True, name=name).start()
            self._json(202, {"status": "accepted", "command": name}); return
        if path == "/auth/start":
            ok, msg = icloud_provider.start_auth(SLIDESHOW_DIR, COOKIE_DIR, log)
            self._json(202 if ok else 409, {"ok": ok, "msg": msg, **icloud_provider.auth_status(COOKIE_DIR)}); return
        if path == "/auth/cancel":
            ok, msg = icloud_provider.cancel_auth(log)
            self._json(200, {"ok": ok, "msg": msg, **icloud_provider.auth_status(COOKIE_DIR)}); return
        self._json(404, {"error": "not found"})

    def _serve_static(self, rel):
        if ".." in rel or rel.startswith("/"):
            self._json(400, {"error": "bad path"}); return
        full = os.path.join(STATIC_DIR, rel) if rel else STATIC_DIR
        if not os.path.isfile(full):
            self._json(404, {"error": "not found"}); return
        ctypes = {
            ".html": "text/html; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
            ".png":  "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon",
        }
        ext = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", ctypes.get(ext, "application/octet-stream"))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(full, "rb") as f:
            self.wfile.write(f.read())

    def _json(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, fmt, *args): return

class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

def _serve(addr, label):
    try:
        with ReusableTCPServer(addr, Handler) as httpd:
            log(f"listening on {addr[0]}:{addr[1]} ({label})")
            httpd.serve_forever()
    except OSError as e:
        log(f"!! bind failed on {addr}: {e}")

def main():
    os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
    log(f"booting v{__version__}")
    log(f"icloud user={env('ICLOUD_USERNAME') or 'MISSING'} album={env('ICLOUD_ALBUM','Slideshow')}")
    log(f"session cookie present: {icloud_provider.session_cookie_present(COOKIE_DIR)}")

    # Deploy bundled web assets to HA's /local/cloud_photos/ so the frontend
    # can serve them via a stable URL. See cloud_photos.deploy.
    deploy_assets(STATIC_DIR, WWW_DEPLOY_DIR, log)

    t = threading.Thread(target=_serve, args=((BIND, PORT), "legacy/host"),
                         daemon=True, name="srv-legacy")
    t.start()
    log("ready")
    _serve(("0.0.0.0", INGRESS_PORT), "ingress")

if __name__ == "__main__":
    main()
