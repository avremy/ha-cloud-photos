"""iCloud provider — wraps icloudpd for sync + interactive re-auth.

icloudpd handles the actual photo download and the MFA webui (port 8080).
This module owns:

- `session_cookie_present()` — check whether a cached cookie exists
- `sync()` — run an incremental sync (no MFA prompt; fails fast if expired)
- `AuthSession` + `start_auth()` / `cancel_auth()` / `auth_status()` — drive
  the icloudpd `--auth-only --mfa-provider webui` flow that the server's
  reverse-proxy at `/icloud/*` exposes to the user.
"""
import os
import subprocess
import threading
import time

ICLOUDPD_PORT = 8080  # icloudpd's --mfa-provider webui port


def env(k, d=""):
    return os.environ.get(k, d)


def session_cookie_present(cookie_dir):
    """Return True if icloudpd's cookie + session file exist."""
    u = env("ICLOUD_USERNAME", "")
    if not u:
        return False
    safe = "".join(c for c in u if c.isalnum())  # icloudpd's transform
    return (os.path.exists(os.path.join(cookie_dir, safe))
            and os.path.exists(os.path.join(cookie_dir, safe + ".session")))


def icloudpd_webui_alive():
    """Quick TCP probe to see if icloudpd's :8080 is up."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", ICLOUDPD_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def sync(slideshow_dir, cookie_dir, synclog):
    """Run a normal incremental sync. Relies on cached cookie.

    `synclog(line)` is a callback the caller provides to append a line to the
    sync log file.
    """
    u = env("ICLOUD_USERNAME")
    if not u:
        synclog("ERROR: icloud_username missing in add-on config"); return 2
    if not session_cookie_present(cookie_dir):
        synclog("ERROR: no session cookie — click Re-authenticate first"); return 3
    os.makedirs(cookie_dir, exist_ok=True)
    cmd = [
        "icloudpd",
        "-d", slideshow_dir,
        "--cookie-directory", cookie_dir,
        "--username", u,
        "--album", env("ICLOUD_ALBUM", "Slideshow"),
        "--folder-structure", "none",
        "--size", "original",
        "--no-progress-bar",
        "--log-level", "info",
        # If the session expired, icloudpd would open its webui asking for MFA.
        # In the unattended /sync path that would hang — so set both providers
        # to console so it just fails fast instead, and the user re-auths via
        # the dedicated /auth/start flow.
        "--password-provider", "console",
        "--mfa-provider",      "console",
    ]
    # Force-fail any prompt by giving icloudpd a closed stdin.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, stdin=subprocess.DEVNULL)
    for line in proc.stdout:
        synclog(line)
    return proc.wait()


class AuthSession:
    def __init__(self): self.reset()
    def reset(self):
        self.proc = None
        self.thread = None
        self.state = "idle"     # idle | starting | running | success | error
        self.error = None
        self.log = []
        self.started_at = None
        self.ended_at = None


AUTH = AuthSession()
AUTH_LOCK = threading.Lock()


def _auth_reader(log):
    assert AUTH.proc and AUTH.proc.stdout
    for raw in AUTH.proc.stdout:
        line = raw.rstrip("\n")
        AUTH.log.append(line)
        if len(AUTH.log) > 300: del AUTH.log[:-300]
        log(f"[auth] {line}")
        low = line.lower()
        if "invalid email/password" in low or "401" in low:
            AUTH.error = AUTH.error or "invalid_credentials"
    rc = AUTH.proc.wait()
    AUTH.ended_at = time.time()
    if AUTH.error:
        AUTH.state = "error"
    elif rc == 0:
        AUTH.state = "success"
    else:
        AUTH.state = "error"
        AUTH.error = AUTH.error or f"icloudpd_exit_{rc}"
    log(f"[auth] icloudpd exited rc={rc} -> {AUTH.state}")


def start_auth(slideshow_dir, cookie_dir, log):
    with AUTH_LOCK:
        if AUTH.proc and AUTH.proc.poll() is None:
            return False, "already_running"
        u = env("ICLOUD_USERNAME")
        if not u:
            return False, "icloud_username_missing"
        AUTH.reset()
        AUTH.state = "starting"
        AUTH.started_at = time.time()
        os.makedirs(cookie_dir, exist_ok=True)
        # We do --auth-only so icloudpd just authenticates and exits; we then
        # call /sync separately to actually download. Password + MFA are read
        # from icloudpd's webui at port 8080.
        cmd = [
            "icloudpd",
            "-d", slideshow_dir,
            "--cookie-directory", cookie_dir,
            "--username", u,
            "--album", env("ICLOUD_ALBUM", "Slideshow"),
            "--auth-only",
            "--password-provider", "webui",
            "--mfa-provider",      "webui",
            "--no-progress-bar",
            "--log-level", "info",
        ]
        # Note: no --password on the cmdline. icloudpd opens its webui and
        # waits for the user to enter it there.
        AUTH.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True,
                                     bufsize=1, stdin=subprocess.DEVNULL)
        AUTH.thread = threading.Thread(target=_auth_reader, args=(log,), daemon=True,
                                       name="auth-reader")
        AUTH.thread.start()
        AUTH.state = "running"
        log("[auth] started icloudpd --auth-only with webui providers")
        return True, "started"


def cancel_auth(log):
    with AUTH_LOCK:
        if AUTH.proc and AUTH.proc.poll() is None:
            try: AUTH.proc.kill()
            except Exception as e: log(f"[auth] kill failed: {e}")
        AUTH.state = "idle"
        AUTH.error = "cancelled"
        return True, "cancelled"


def auth_status(cookie_dir):
    return {
        "state": AUTH.state,
        "error": AUTH.error,
        "log_tail": list(AUTH.log[-30:]),
        "started_at": AUTH.started_at,
        "ended_at": AUTH.ended_at,
        "session_cookie_present": session_cookie_present(cookie_dir),
        "icloudpd_webui_alive": icloudpd_webui_alive(),
    }
