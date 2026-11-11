"""Non-destructive RTSP connectivity and media inspection for camera setup.

ffprobe reads stream headers only. It never records, updates a camera,
or includes RTSP passwords and full diagnostic stderr in API responses.
"""
import json
import subprocess
from fractions import Fraction


def _rate(value):
    try:
        number = float(Fraction(str(value)))
        return round(number, 2) if 0 < number <= 240 else None
    except (ValueError, ZeroDivisionError, TypeError, OverflowError):
        return None


def _failure(stderr):
    text = (stderr or "").lower()
    if any(word in text for word in ("401 unauthorized", "403 forbidden", "authorization failed", "authentication failed")):
        return "Camera rejected the username or password."
    if any(word in text for word in ("404 not found", "method describe failed: 404")):
        return "RTSP stream path not found. Check this camera model's stream URL."
    if "connection refused" in text:
        return "Connection refused. Check the camera IP, RTSP port, and whether RTSP is enabled."
    if any(word in text for word in ("timed out", "timeout", "network is unreachable", "no route to host")):
        return "Camera did not respond. Check network/VPN connectivity and camera IP."
    return "Could not read a video stream. Check RTSP credentials, path, camera status, and codec."


def inspect_stream(url, label):
    """Probe a single RTSP URL with a hard deadline and bounded output."""
    cmd = [
        "ffprobe", "-hide_banner", "-v", "error",
        "-rtsp_transport", "tcp", "-rw_timeout", "5000000",
        "-analyzeduration", "2000000", "-probesize", "1000000",
        "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate",
        "-show_streams", "-of", "json", url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=9, check=False)
    except subprocess.TimeoutExpired:
        return {"stream": label, "ok": False, "message": "Camera connection timed out (9 seconds)."}
    except FileNotFoundError:
        return {"stream": label, "ok": False, "message": "FFprobe is not installed. Install FFmpeg on the Pi."}
    except OSError:
        return {"stream": label, "ok": False, "message": "Unable to start the camera test utility."}
    if result.returncode:
        return {"stream": label, "ok": False, "message": _failure(result.stderr[:4096].decode("utf-8", errors="replace"))}
    try:
        parsed = json.loads(result.stdout[:131072])
        videos = [v for v in parsed.get("streams", []) if v.get("codec_type") == "video"]
        audios = [a.get("codec_name", "unknown") for a in parsed.get("streams", []) if a.get("codec_type") == "audio"]
    except (ValueError, TypeError, AttributeError):
        return {"stream": label, "ok": False, "message": "Camera returned unreadable stream information."}
    if not videos:
        return {"stream": label, "ok": False, "message": "Camera connected but did not expose a video stream."}
    video = videos[0]
    codec = str(video.get("codec_name") or "unknown").lower()
    width = video.get("width") if isinstance(video.get("width"), int) else None
    height = video.get("height") if isinstance(video.get("height"), int) else None
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    recommendation = (
        "H.264 detected — recommended for PiNVR browser viewing."
        if codec == "h264"
        else "H.265 detected — recording may work, but browser/WebRTC viewing may fail. Prefer H.264."
        if codec in ("hevc", "h265")
        else "Codec may not play in every browser. Set the camera to H.264 if available."
    )
    return {
        "stream": label, "ok": True, "codec": codec,
        "width": width, "height": height, "fps": fps,
        "audio": audios[:3], "message": recommendation,
    }


def inspect_camera(camera, url_builder):
    # Serial probing avoids sending too many resource-intensive connection
    # attempts to lower-powered cameras at once.
    results = [inspect_stream(url_builder(camera, sub=sub), label)
               for label, sub in (("main", False), ("substream", True))]
    return {"ok": all(x["ok"] for x in results), "streams": results,
            "saved": False, "tested": True}
