"""PiNVR alert destinations with Apprise provider URLs.

The URLs may contain provider credentials and are never returned from the API.
Delivery is best-effort, bounded to prevent a slow notification provider from
blocking video motion detection. UI testing is via admin-only test endpoint.
"""
import ipaddress
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from urllib.parse import urlsplit

LOG = logging.getLogger("pinvr.notifications")
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pinvr-alert")
_LAST = {}
_LOCK = threading.Lock()

# Safe, curated external protocols. Avoid arbitrary file/command URLs.
ALLOWED_SCHEMES = {
    "mailto", "mailtos", "ntfy", "ntfys", "pover", "tgram",
    "discord", "discords", "slack", "msteams", "jsons", "gotify",
    "gotifys", "hassios",
}


def validate_url(url):
    if not isinstance(url, str) or not 8 <= len(url) <= 2048:
        raise ValueError("Enter a valid notification service URL.")
    if any(c.isspace() for c in url):
        raise ValueError("The notification URL may not contain spaces.")
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError("Unsupported notification provider. Use one of the listed secure notification types.")
    # Never accept local resources or file paths as destinations.
    if scheme in {"jsons", "ntfys", "gotifys", "hassios"}:
        host = urlsplit(url).hostname
        if not host:
            raise ValueError("Notification destination must have a hostname.")
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                raise ValueError("Private network destinations are not allowed for this provider.")
        except ValueError as e:
            if "Private network" in str(e):
                raise
    return url


def redact(url):
    # The UI displays the label and provider type, not the credential-bearing URL.
    try:
        return urlsplit(url).scheme.lower()
    except ValueError:
        return "unknown"


def _send(url, title, body):
    import apprise
    app = apprise.Apprise()
    if not app.add(url):
        raise RuntimeError("The notification provider rejected its configuration.")
    if not app.notify(title=title[:90], body=body[:900]):
        raise RuntimeError("The notification service did not confirm delivery.")
    return True


def send_test(url):
    future = _EXECUTOR.submit(_send, url, "PiNVR Test Alert",
                              "PiNVR is connected. This is a test notification from your recorder.")
    try:
        future.result(timeout=12)
        return {"ok": True, "message": "The notification provider accepted the test alert. Check your device."}
    except FutureTimeout:
        return {"ok": False, "message": "Notification provider timed out. Check your connection and service."}
    except Exception:
        LOG.warning("Notification test failed (credentials and response hidden)")
        return {"ok": False, "message": "Notification delivery failed. Check the provider URL and internet access."}


def dispatch(destinations, event_type, camera_name=None):
    """Queue delivery without waiting for a provider on the motion capture thread."""
    now = time.monotonic()
    for dest in destinations:
        if not dest["enabled"] or event_type not in dest["events"].split(","):
            continue
        key = (dest["id"], event_type, camera_name or "")
        with _LOCK:
            if now - _LAST.get(key, -9999) < dest["cooldown_seconds"]:
                continue
            _LAST[key] = now
        if event_type == "motion":
            title = "PiNVR: Motion detected"
            message = f"Motion detected on {camera_name or 'camera'}."
        elif event_type == "storage":
            title = "PiNVR: Recording storage offline"
            message = "USB recording drive is not mounted. Check recording storage."
        else:
            continue
        try:
            _EXECUTOR.submit(_deliver_safe, dest["url"], title, message)
        except RuntimeError:
            LOG.warning("Alert queue unavailable")


def _deliver_safe(url, title, message):
    try:
        _send(url, title, message)
    except Exception:
        LOG.warning("PiNVR alert delivery failed (provider details hidden)")
