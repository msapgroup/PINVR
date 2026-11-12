"""Safe cleanup of old PiNVR media on the mounted external recording drive.

Only camera-ID directories and known PiNVR filenames are considered.
Skip in-progress or very recent files, symlinks, and unmounted storage.
"""
from pathlib import Path
import os
import re
import shutil
import time

CLIP = re.compile(r"[0-9]{8}-[0-9]{6}\.(?:mp4|mkv)\Z")
JPG = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}-[0-9]{2}\.jpg\Z")
MIN_AGE_SECONDS = 900


def candidates(root: Path, now=None):
    """List safe, completed candidates oldest first, never traversing symlinks."""
    now = time.time() if now is None else now
    found = []
    for camera_dir in root.iterdir():
        if not camera_dir.is_dir() or camera_dir.is_symlink() or not camera_dir.name.isdecimal():
            continue
        for directory, is_snapshot in ((camera_dir, False), (camera_dir / "snapshots", True)):
            if not directory.is_dir() or directory.is_symlink():
                continue
            for item in directory.iterdir():
                if item.is_symlink() or not item.is_file():
                    continue
                if not (JPG if is_snapshot else CLIP).fullmatch(item.name):
                    continue
                try:
                    st = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                if 0 < st.st_size and now - st.st_mtime >= MIN_AGE_SECONDS:
                    found.append((st.st_mtime, item, st.st_size))
    return sorted(found, key=lambda item: item[0])


def prune(root: Path, mounted, retention_days=14, max_used_percent=90,
          min_free_gb=5, now=None, usage_func=shutil.disk_usage):
    """Return cleanup statistics; never delete anything if mount validation fails."""
    if not mounted() or not root.exists() or not os.path.ismount(root):
        return {"ok": False, "reason": "Recording drive not mounted", "removed": 0, "bytes_freed": 0}
    now = time.time() if now is None else now
    retention_days = min(3650, max(1, int(retention_days)))
    max_used_percent = min(98, max(50, int(max_used_percent)))
    min_free_gb = min(1000, max(1, int(min_free_gb)))
    files = candidates(root, now)
    removed = freed = 0
    expiry = now - retention_days * 86400
    threshold = min_free_gb * (1024 ** 3)

    def must_free_space():
        used = usage_func(root)
        return used.free < threshold or (used.used * 100 / max(1, used.total)) >= max_used_percent

    # First remove expired clips. Then remove oldest additional clips only
    # if the disk is over its capacity / reserve threshold.
    for timestamp, item, size in files:
        if timestamp >= expiry and not must_free_space():
            break
        if not mounted() or not os.path.ismount(root):
            break
        try:
            # Ensure the file is still the same size and old enough to be closed.
            st = item.stat(follow_symlinks=False)
            if item.is_symlink() or st.st_size != size or now - st.st_mtime < MIN_AGE_SECONDS:
                continue
            item.unlink()
            removed += 1
            freed += size
        except OSError:
            continue
    return {"ok": True, "removed": removed, "bytes_freed": freed, "available_files": len(files)}
