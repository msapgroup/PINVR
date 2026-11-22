"""Recorder output freshness checks (not a codec/frame-integrity validator)."""
import re
import time

NAME = re.compile(r"[0-9]{8}-[0-9]{6}\.(mp4|mkv)\Z")


def recent_output(root, camera_id, started_at, now=None, freshness_seconds=480):
    """Return True only if video bytes written since recorder started are recent.

    A process can be alive but stuck without writing files. A grace period
    is handled by the caller, not by this function.
    """
    now = time.time() if now is None else now
    folder = root / str(camera_id)
    try:
        for entry in folder.iterdir():
            if not NAME.fullmatch(entry.name) or entry.is_symlink() or not entry.is_file():
                continue
            info = entry.stat(follow_symlinks=False)
            if info.st_size >= 1024 and started_at - 2 <= info.st_mtime <= now + 5 and now - info.st_mtime <= freshness_seconds:
                return True
    except OSError:
        return False
    return False
