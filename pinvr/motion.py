"""Lightweight native video motion detection for PiNVR RTSP substreams.

Each enabled camera is sampled at 2 FPS and scaled to 320x180 grayscale by
FFmpeg. Only sparse pixels are compared in Python; no main-stream decoding,
OpenCV, cloud analysis or Docker is required. This is a basic motion detector,
not person/vehicle classification.
"""
import subprocess
import threading
import time

WIDTH, HEIGHT, FPS = 320, 180, 2
FRAME_SIZE = WIDTH * HEIGHT
STOP = threading.Event()
WORKERS = {}
LOCK = threading.RLock()


def changed_fraction(previous, frame, sensitivity=5):
    """Return fraction of sampled pixels changing enough to count as motion."""
    if len(previous) != FRAME_SIZE or len(frame) != FRAME_SIZE:
        raise ValueError('Unexpected motion analysis frame size')
    # Lower sensitivity requires bigger intensity changes and more changed pixels.
    pixel_delta = max(10, 36 - 3 * int(sensitivity))
    a, b = previous[::8], frame[::8]
    return sum(abs(x - y) >= pixel_delta for x, y in zip(a, b)) / len(a)


def has_motion(previous, frame, sensitivity=5):
    minimum = max(0.012, 0.16 - 0.015 * int(sensitivity))
    return changed_fraction(previous, frame, sensitivity) >= minimum


def command(url):
    return ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-rtsp_transport', 'tcp', '-rw_timeout', '5000000', '-i', url,
            '-an', '-sn', '-dn', '-vf', f'fps={FPS},scale={WIDTH}:{HEIGHT}:flags=fast_bilinear,format=gray',
            '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']


def read_frame(pipe):
    chunks = []
    remaining = FRAME_SIZE
    while remaining:
        data = pipe.read(remaining)
        if not data:
            return None
        chunks.append(data)
        remaining -= len(data)
    return b''.join(chunks)


def _watch(camera, url, callback, stop):
    while not STOP.is_set() and not stop.is_set():
        proc = None
        try:
            proc = subprocess.Popen(command(url), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            old = None
            consecutive = 0
            while not STOP.is_set() and not stop.is_set():
                frame = read_frame(proc.stdout)
                if frame is None:
                    break
                if old is not None and has_motion(old, frame, camera['motion_sensitivity']):
                    consecutive += 1
                    if consecutive == 2:
                        try: callback(camera['id'])
                        except Exception: pass  # Stream monitoring must keep running.
                else:
                    consecutive = 0
                old = frame
        except OSError:
            pass
        finally:
            if proc:
                proc.terminate() if proc.poll() is None else None
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                if proc.stdout: proc.stdout.close()
        stop.wait(5)


def configure(cameras, url_for_camera, callback):
    """Stop modified/removed workers; start one only for opted-in cameras."""
    desired = {}
    for camera in cameras:
        if camera['enabled'] and camera['motion_detection']:
            url = url_for_camera(camera, sub=True)
            desired[camera['id']] = (camera, url)
    with LOCK:
        for cid, info in list(WORKERS.items()):
            if cid not in desired or info['config'] != (desired[cid][1], desired[cid][0]['motion_sensitivity']):
                info['stop'].set()
                if info.get('proc'): info['proc'].terminate()
                del WORKERS[cid]
        if STOP.is_set():
            return
        for cid, (camera, url) in desired.items():
            if cid in WORKERS: continue
            stop = threading.Event()
            worker = threading.Thread(target=_watch, args=(camera, url, callback, stop), daemon=True,
                                      name=f'pinvr-motion-{cid}')
            WORKERS[cid] = {'stop': stop, 'thread': worker,
                            'config': (url, camera['motion_sensitivity'])}
            worker.start()


def shutdown():
    STOP.set()
    with LOCK:
        for info in WORKERS.values():
            info['stop'].set()
        WORKERS.clear()
