# PiNVR Secure View

![PiNVR dashboard](screenshots/desktop-live.jpg)

**Native Raspberry Pi NVR + iPhone/Android mobile web app**, styled as a dark professional security console. Designed initially for Raspberry Pi 4 (4 GB RAM), 9+ RTSP cameras and external USB recording storage. **No Docker.**

> **Development status: early working alpha.** Login, first-login password change, RTSP camera configuration, FFmpeg stream-copy recording to mounted USB storage, recorded MP4 listing/playback, and responsive PWA UI are implemented. WebRTC live streaming is now wired through go2rtc but requires live camera and Raspberry Pi testing. ONVIF discovery, PTZ, motion notifications and measured latency/FPS are **not yet implemented**. Do not replace a production NVR until tested on your camera hardware.

## Screenshots & how to use

| Screen | Preview | What it does |
| --- | --- | --- |
| Desktop Live View | ![Live view](screenshots/desktop-live.jpg) | Camera tree, layout choices (1/4/9/12/16), focus selected camera, recording status. WebRTC streams activate for up to four visible cameras at a time; clicking Focus loads the selected main stream. The documentation screenshots use example cameras and placeholders. |
| Camera Management | ![Camera management](screenshots/desktop-cameras.jpg) | Add, edit and remove RTSP IP cameras; specify main/substream RTSP paths and enable recording. |
| Playback | ![Playback](screenshots/desktop-playback.jpg) | Search recorded MP4 segments by camera and play/download clips. Requires recorded footage on mounted storage. |
| Mobile | ![Mobile view](screenshots/mobile-live.jpg) | Open the same server on Android Chrome or iPhone Safari and add to home screen; mobile interface shares server settings. |
| Login | ![Login](screenshots/login.jpg) | Sign in and change the default password before viewing camera settings. |

Screenshots are from an application test run using example camera definitions. No footage or performance measurements are claimed.

## Raspberry Pi installation

Install **Raspberry Pi OS Lite 64-bit** on Pi 4, and connect your cameras via wired Ethernet and your USB recording drive. Download/clone this repository and run:

```bash
sudo bash install.sh
```

The installer sets up system Python virtualenv, FFmpeg, SHA256-verified native go2rtc v1.9.15 ARM64 and `pinvr.service` on port **8080**, with no Docker. go2rtc management listens only on `127.0.0.1:1984`, and WebRTC media uses TCP/UDP `8555` on the private network. Do not forward either port to the public internet. Start at `http://<PI-IP>:8080` on your local network.

**Initial credentials:** username `Admin`, password `raspberry`. You **must** change the password immediately after first login (minimum 12 characters). Never expose port 8080 directly to the public internet. Use an HTTPS reverse proxy and a VPN (WireGuard/Tailscale) for remote access.

### Mount the USB drive (required)

The recorder **intentionally refuses to write** if `/mnt/pinvr-recordings` is not a mounted volume, to protect the OS microSD card. Find the correct drive UUID and filesystem type:

```bash
lsblk -f
sudo blkid
```

Add the appropriate persistent entry to `/etc/fstab` using the correct UUID/filesystem. For an ext4 drive, one example is:

```text
UUID=YOUR_ACTUAL_USB_UUID /mnt/pinvr-recordings ext4 defaults,nofail,x-systemd.device-timeout=10 0 2
```

Then:

```bash
sudo mount -a
sudo chown -R pinvr:pinvr /mnt/pinvr-recordings
sudo systemctl restart pinvr
sudo journalctl -u pinvr -f
```

**Important:** Check `findmnt /mnt/pinvr-recordings` and backups before formatting or changing an existing USB drive. Never format a drive with existing footage without a backup. For USB drives formatted exFAT/NTFS, use the correct mount options to grant the `pinvr` service write access.

### Add a camera

1. Open **Devices → Add Camera**.
2. Enter a name, IP/hostname and camera username/password.
3. Provide main and substream paths (e.g., `/Streaming/Channels/101` and `/Streaming/Channels/102` for compatible camera models; paths vary by manufacturer).
4. Select **Record continuously to USB**. Save.
5. Verify **Settings → Recording workers** is active and inspect `journalctl -u pinvr` if not.
6. Select **Playback** to see completed 5-minute MP4 recording segments.

Recording uses `ffmpeg -c:v copy` without decoding to reduce Pi load. Some camera codecs/containers might be incompatible with MP4 or browser playback; H.264 is recommended. Segment boundaries depend on keyframes, so durations may vary. The supervisor retries stopped recorder processes every 15 seconds. Disk retention management is **not implemented yet**: monitor free space to avoid exhaustion.

### Android and iPhone

Open the same PiNVR address in Chrome or Safari on your local network, then choose **Add to Home Screen**. The responsive website includes an installable web app manifest, but push notifications and offline video are not yet available. Keep access behind a VPN when remote.

## Architecture

- **FastAPI + SQLite** — authenticated configuration, clip listing and status API.
- **FFmpeg** — one `-c:v copy` worker per enabled camera with continuous recording.
- **go2rtc v1.9.15** — native ARM64 video relay with authenticated WHEP signaling from PiNVR to loopback go2rtc, and direct WebRTC media on port 8555.
- **USB mount safety** — block writes if the recording mount is absent.
- **Responsive HTML/CSS/JavaScript** — dark Hikvision-inspired PiNVR UI (independent branding) for desktop/mobile.
- **systemd** — native service, runs unprivileged as `pinvr`.

**Security caveats for this alpha:** HTTP on LAN is not encrypted; configure TLS before transmitting camera credentials on untrusted networks. Camera passwords are stored in a restricted SQLite file, **not encrypted at rest**. A logged-in local admin can inspect process arguments containing camera RTSP credentials; isolate the Pi and restrict system accounts. Auth currently supports one built-in admin account and has no multi-user roles, rate limiting or CSRF token mechanism. Do not publicly expose the service. Treat this as an early integration/test build.

## Local development & tests

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
PINVR_DATA=/tmp/pinvr-dev PINVR_RECORDINGS=/tmp/pinvr-clips uvicorn pinvr.main:app --port 8080
pytest -q
```

The app can run locally without the go2rtc binary for camera management and recordings; live feeds require the binary. The default app refuses recording to an unmounted directory. `PINVR_ALLOW_LOCAL_RECORDINGS=1` is **test-only** and bypasses mount detection; never set it for a real deployment.

## Roadmap

1. Test and tune the new go2rtc WebRTC bridge, low-latency substreams and authentication on a physical Raspberry Pi 4 with 9–16 cameras. Add stream health/reconnect diagnostics and optimized camera-wall subscription.
2. Implement ONVIF discovery and credential provisioning, PTZ and camera-native motion events.
3. Disk usage limits, retention cleanup and failed-disk alerts.
4. Multi-user accounts, encrypted camera secrets, login rate limiting, CSRF protections and HTTPS deployment.
5. Benchmark 9/12/16 active streams and publish real FPS, latency, temperature and storage throughput results.

## License

See [LICENSE](LICENSE) in this repository.
