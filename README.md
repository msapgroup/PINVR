# PiNVR Secure View

![PiNVR dashboard](screenshots/desktop-live.jpg)

**Native Raspberry Pi NVR + iPhone/Android mobile web app**, styled as a dark professional security console. Designed initially for Raspberry Pi 4 (4 GB RAM), 9+ RTSP cameras and external USB recording storage. **No Docker.**

> **Development status: early working alpha.** Login, recurring password-change recommendation, RTSP camera configuration, FFmpeg stream-copy recording to mounted USB storage, recorded MP4/MKV listing, JPG snapshots and browser playback, and responsive PWA UI are implemented. WebRTC live streaming is now wired through go2rtc but requires live camera and Raspberry Pi testing. ONVIF discovery and authenticated motion-event webhooks are implemented. ONVIF motion-event subscriptions, PTZ, mobile push notifications, and measured latency/FPS **still require implementation or on-device testing**. Do not replace a production NVR until tested on your camera hardware.

## Screenshots & how to use

| Screen | Preview | What it does |
| --- | --- | --- |
| Desktop Live View | ![Live view](screenshots/desktop-live.jpg) | Camera tree, layout choices (1/4/9/12/16), focus selected camera, recording status. WebRTC streams activate for up to four visible cameras at a time; clicking Focus loads the selected main stream. The documentation screenshots use example cameras and placeholders. |
| Camera Management | ![Camera management](screenshots/desktop-cameras.jpg) | Add, edit and remove RTSP IP cameras; specify main/substream RTSP paths and enable recording. |
| Playback | ![Playback](screenshots/desktop-playback.jpg) | Search MP4/MKV segments and JPG snapshots by camera. MP4 playback works with supported browser codecs; MKV is offered as a download. Requires mounted storage. |
| Recording formats and JPG settings | ![Recording options](screenshots/desktop-recording-options.jpg) | Set continuous or motion-triggered recording, choose MP4/MKV, set 1–7 JPG snapshots per event, and test motion. |
| Native video motion detection | ![Video motion detection](screenshots/desktop-video-motion-detection.jpg) | Enable built-in motion detection from RTSP substreams, set per-camera sensitivity, and use motion-triggered clips and 1–7 JPG snapshots. Detection requires real compatible camera substreams and sufficient Pi CPU capacity. |
| Admin Users & Permissions | ![Users](screenshots/desktop-users.jpg) | Create or remove Viewer accounts, reset Viewer passwords and see access restrictions. |
| Viewer Mobile | ![Viewer mobile](screenshots/mobile-viewer.jpg) | Viewer can open Live View and Playback without accessing Admin settings. |
| Mobile | ![Mobile view](screenshots/mobile-live.jpg) | Open the same server on Android Chrome or iPhone Safari and add to home screen; mobile interface shares server settings. |
| Network Access | ![Network Access settings](screenshots/desktop-network.jpg) | Change the web listening port, view VPN guidance and understand router external-to-internal mappings. |
| Login | ![Login](screenshots/login.jpg) | Sign in as Admin or Viewer. A dismissible security reminder appears on each login until the password is changed. |

Screenshots are from an application test run using example camera definitions. No footage or performance measurements are claimed.

## Raspberry Pi installation

Install **Raspberry Pi OS Lite 64-bit** on Pi 4, and connect your cameras via wired Ethernet and your USB recording drive. Download/clone this repository and run:

```bash
sudo bash install.sh
```

The installer sets up system Python virtualenv, FFmpeg, SHA256-verified native go2rtc v1.9.15 ARM64 and `pinvr.service` on port **8080 by default**, configurable in **Settings → Network Access**, with no Docker. go2rtc management listens only on `127.0.0.1:1984`, and WebRTC media uses TCP/UDP `8555` on the private network. Do not forward either port to the public internet. Start at `http://<PI-IP>:8080` on your local network and use a private VPN for remote access.

**Initial credentials:** username `Admin`, password `raspberry`. A password change is **strongly recommended**, but not required to use the app. The reminder appears on every new login until the password has been changed (new passwords require at least 12 characters). Never expose port 8080 directly to the public internet. Use an HTTPS reverse proxy and a VPN (WireGuard/Tailscale) for remote access.

### Mount the USB drive permanently (required)

PiNVR writes recordings to `/mnt/pinvr-recordings`. **This must be the existing external USB drive, not a directory on the Raspberry Pi microSD card.** The recorder refuses to write when the drive is not mounted. **Do not format or repartition a drive that already contains recordings.**

**Step 1 — Identify the external drive and its filesystem.** Connect your USB drive, then run:

```bash
lsblk -o NAME,SIZE,FSTYPE,UUID,LABEL,MOUNTPOINTS,MODEL
sudo blkid
```

Find the partition that matches the **USB drive's size and model** (often `/dev/sda1`, but it may have a different name). Write down its **actual UUID** and **FSTYPE** (such as `ext4`, `exfat`, or `ntfs`). Double-check that it is **not the microSD system partition**. If it is already mounted elsewhere, identify that mount and stop before changing anything: moving a live mount may interrupt other software.

**Step 2 — Create the recording mount point and back up `fstab`.**

```bash
sudo mkdir -p /mnt/pinvr-recordings
sudo cp -a /etc/fstab "/etc/fstab.backup.$(date +%Y%m%d-%H%M%S)"
```

**Step 3 — Open `fstab` and add exactly one matching line.**

```bash
sudo nano /etc/fstab
```

For a USB drive whose `FSTYPE` is **ext4**, add the following line at the bottom, replacing `YOUR_ACTUAL_USB_UUID` with the UUID reported by `lsblk` (leave `UUID=` intact):

```fstab
UUID=YOUR_ACTUAL_USB_UUID /mnt/pinvr-recordings ext4 defaults,nofail,x-systemd.device-timeout=10 0 2
```

For a drive whose `FSTYPE` is **exfat**, use this pattern **instead**, replacing `1000` with the actual numeric UID and GID of the `pinvr` service user from `id pinvr`:

```bash
id pinvr
```

```fstab
UUID=YOUR_ACTUAL_USB_UUID /mnt/pinvr-recordings exfat defaults,nofail,uid=PINVR_UID,gid=PINVR_GID,umask=007,x-systemd.device-timeout=10 0 0
```

For **NTFS** drives, check available filesystem support first with `grep ntfs /proc/filesystems` and `command -v mount.ntfs-3g`. Do not use the ext4 line for NTFS: its mount type and permission options differ. Prefer ext4 on a Linux-only recording drive, but **do not reformat an existing drive just to follow this guide**. Ask for help with the correct NTFS entry if needed.

**Save in Nano:** press **Ctrl+O**, press **Enter**, then press **Ctrl+X**. Avoid duplicate `fstab` entries for the same drive or mount point.

**Step 4 — Test the mount before starting recording.**

```bash
sudo systemctl daemon-reload
sudo findmnt --verify --verbose
sudo mount /mnt/pinvr-recordings
findmnt /mnt/pinvr-recordings -o TARGET,SOURCE,FSTYPE,OPTIONS
lsblk -f
```

If `mount` reports `already mounted`, that is acceptable **only if** `findmnt` shows the **correct external USB partition** at `/mnt/pinvr-recordings`. If `findmnt` reports nothing or the wrong device, **stop** and correct `fstab` before proceeding. Do not save recordings to an unmounted directory.

**Step 5 — Give PiNVR write access.** For an **ext4** drive, set ownership of the recording directory to the service account:

```bash
sudo chown pinvr:pinvr /mnt/pinvr-recordings
sudo chmod 750 /mnt/pinvr-recordings
```

For **exFAT**, access comes from the `uid`/`gid` mount options, not `chown`. On either supported filesystem, test writing as the application user:

```bash
sudo -u pinvr sh -c 'touch /mnt/pinvr-recordings/.pinvr-write-test && rm /mnt/pinvr-recordings/.pinvr-write-test'
```

If that fails, correct the USB mount permissions before proceeding. Avoid a blanket recursive `chown -R` on a drive with existing unrelated files.

**Step 6 — Restart PiNVR and check recording status.**

```bash
sudo systemctl restart pinvr
sudo systemctl status pinvr --no-pager
sudo journalctl -u pinvr -n 50 --no-pager
findmnt /mnt/pinvr-recordings
```

In PiNVR, check **Settings → System Health / Storage** for the mounted USB drive, then enable recording for one camera and verify a clip is saved. Finally reboot (`sudo reboot`) when convenient and confirm `findmnt /mnt/pinvr-recordings` still lists the correct USB device.

**Troubleshooting:** If `findmnt --verify` reports errors, restore the most recent backup you made in Step 2 (inspect the backup name first), correct the entry, and rerun the checks. A USB disk mounting slowly at startup may need extra troubleshooting with `journalctl -b -u mnt-pinvr\\x2drecordings.mount`. Filesystems and device ownership vary, so only use an example that matches your drive.

### Add a camera

1. Open **Devices → Add Camera**.
2. Enter a name, IP/hostname and camera username/password.
3. Provide main and substream paths (e.g., `/Streaming/Channels/101` and `/Streaming/Channels/102` for compatible camera models; paths vary by manufacturer).
4. Select **Enable video recording to USB**, choose **Continuous** or **Motion event clips**, select **MP4** (recommended) or **MKV**, and choose **1–7 JPGs per motion event**. Save. For motion mode, configure an external camera-event integration as described below.
5. Verify **Settings → Recording workers** is active and inspect `journalctl -u pinvr` if not.
6. Select **Playback** to see completed 5-minute continuous recording segments or 30-second motion-event clips (when an event is received). The JPG gallery displays available snapshots.

Recording uses `ffmpeg -c:v copy` without decoding to reduce Pi load. **MP4 with fragmented metadata is the default:** it is easier to view on iPhone, Android, and most browsers when the camera uses H.264, and has better interruption recovery than ordinary non-fragmented MP4. **MKV** is an alternative for resilient storage and VLC playback but generally downloads rather than playing natively in browsers. Neither container converts unsupported codecs; use camera-native H.264 where practical. <https://ffmpeg.org/ffmpeg-formats.html> Segment boundaries depend on keyframes, so durations may vary. The supervisor retries stopped recorder processes every 15 seconds. Disk retention management is **not implemented yet**: monitor free space to avoid exhaustion.



### Motion recording, JPG bursts and ONVIF discovery

![Video motion detection options](screenshots/desktop-video-motion-detection.jpg)

**Native video motion detection:** For each camera, enable Detect Motion from Video. PiNVR compares 320×180 grayscale substream frames sampled at approximately 2 FPS and triggers a motion event when changes exceed the selected 1–10 sensitivity. Start with sensitivity 5; adjust to reduce false positives from trees, headlights, or sudden lighting changes. This requires active camera RTSP access, uses CPU for decoding each enabled substream, and has not yet been benchmarked with 9+ simultaneous cameras on a Raspberry Pi 4. Do not enable motion processing on all cameras until measured resource usage is acceptable.


![Recording formats and snapshots](screenshots/desktop-recording-options.jpg)

- **Continuous (default):** direct-copy five-minute MP4 or MKV segments for every recording-enabled camera. For a Pi 4 with 9+ cameras this is the simplest, most reliable starting choice.
- **Motion events:** short **30-second** direct-copy video clips and a **1–7 image JPG burst** captured from the camera's substream at approximately 1 image/second, when an authenticated event arrives. This does not provide pre-event buffering, and hardware/stream timing may affect the precise recorded duration. A motion clip requires **Enable video recording**; JPG bursts can be created without a motion video clip.
- **Test Motion & JPG Burst:** press in the camera editor for a saved camera. JPG generation requires the USB drive, camera RTSP connectivity, and FFmpeg; with no physical cameras connected it will not create images.
- **ONVIF Discovery:** Devices → ONVIF discovery performs a local UDP WS-Discovery search. It lists candidate camera IPs and service addresses but does not automatically authenticate or configure every vendor's RTSP path.
- **Motion webhook:** Admins can select **Generate Event Key** in a saved camera's editor. The key is displayed once and must be stored securely. A compatible camera notification bridge sends `POST http://PI-VPN-IP:PORT/api/events/motion/CAMERA_ID` with header `X-PiNVR-Event-Key: GENERATED_KEY`. The camera is recorded as a motion event and can trigger the configured JPG burst and motion clip. **This is an integration interface—not a universal ONVIF motion detector.** Camera-native ONVIF event subscription/pull-point support is still planned. Do not publish this endpoint directly to the public internet; use your VPN and protect the secret.
- **Storage:** snapshots live under `/mnt/pinvr-recordings/CAMERA_ID/snapshots` as JPG files. Playback lists both stored clips and snapshots, with Viewer read-only access to enabled camera recordings. Viewer accounts cannot manually trigger captures or generate webhook credentials.

A recording format change affects **new files**; previously recorded MP4/MKV files are preserved. Set the right time and timezone on the Raspberry Pi for reliable date-based filenames.

### Change the web access port (VPN / advanced router setups)

![Network Access settings](screenshots/desktop-network.jpg)

1. Sign in as `Admin` and optionally dismiss the recurring password-change recommendation.
2. Open **Settings → Network Access**. The default TCP web port is **8080**.
3. Enter a new unused port from **1024 to 65535** (excluding **1984** and **8555**, which PiNVR reserves for internal streaming) and your **current Admin password**.
4. Select **Apply port & restart**, confirm, and open the new URL shown by PiNVR, for example `http://192.168.1.50:8090`. The native systemd service automatically restarts and preserves this setting across reboots and upgrades. If the new port cannot be bound during restart, the launcher attempts to recover on the previous port.
5. Update any VPN firewall policy, saved mobile app shortcut, reverse proxy, or router mapping to match the new listening port. Keep private VPN access as the recommended option.

**Port mapping distinction:** Your **PiNVR listening port** and your **router external port** are separate. A router could map external `18443` to an internal PiNVR port `8080` without changing PiNVR at all. Port forwarding provides **no encryption**, and the alpha web application speaks HTTP; **do not expose it directly to the internet**. Use your existing VPN or configure a secure HTTPS reverse proxy before sharing remotely. An external HTTPS proxy typically terminates TLS at its own port and forwards to PiNVR privately.

**Troubleshooting:** If you cannot reconnect after a port change, connect over SSH/VPN and run `sudo systemctl status pinvr` and `sudo journalctl -u pinvr -n 100`. You can inspect the configured port with `sudo sqlite3 /var/lib/pinvr/pinvr.db "SELECT value FROM settings WHERE key='web_port';"` if `sqlite3` is installed. The Pi firewall must permit the chosen port on the LAN/VPN interface. Avoid changing the port while remotely accessing PiNVR without a fallback SSH path. A port change does not alter the go2rtc WebRTC media port `8555`, which needs private/VPN connectivity when live video is used.

### Accounts, password reminders and Viewer playback

![Admin users and permissions](screenshots/desktop-users.jpg)

- **Admin:** Full camera, recording, network, user, and system control.
- **Viewer:** Live camera feeds **and recorded clip search/playback** on both desktop and mobile. Cannot add/edit/delete cameras, change recording policies, use admin system APIs, or administer accounts.
- The default `Admin` / `raspberry` password remains usable until changed, but because it is public, the system shows a **security recommendation at every login** until a stronger password is set. Select **Remind me next login** to dismiss it for the current session, or **Change password**. Viewer accounts with newly assigned passwords receive the same reminder. The reminder does not block playback or configuration.
- For remote VPN access, use unique passwords and restrict network access. Allowing the public default password to remain active increases risk, even over a VPN.

![Viewer mobile view](screenshots/mobile-viewer.jpg)

### Android and iPhone

Open the same PiNVR address in Chrome or Safari on your local network, then choose **Add to Home Screen**. The responsive website includes an installable web app manifest, but push notifications and offline video are not yet available. Keep access behind a VPN when remote.

## Architecture

- **FastAPI + SQLite** — authenticated configuration, clip listing and status API.
- **FFmpeg** — one `-c:v copy` worker per enabled continuous-recording camera; event-triggered clip and JPG workers only when a webhook or manual test triggers them.
- **go2rtc v1.9.15** — native ARM64 video relay with authenticated WHEP signaling from PiNVR to loopback go2rtc, and direct WebRTC media on port 8555.
- **USB mount safety** — block writes if the recording mount is absent.
- **Responsive HTML/CSS/JavaScript** — dark Hikvision-inspired PiNVR UI (independent branding) for desktop/mobile.
- **systemd** — native service, runs unprivileged as `pinvr`.

**Security caveats for this alpha:** HTTP on LAN is not encrypted; configure TLS before transmitting camera credentials on untrusted networks. Camera passwords are stored in a restricted SQLite file, **not encrypted at rest**. A logged-in local admin can inspect process arguments containing camera RTSP credentials; isolate the Pi and restrict system accounts. Auth currently supports one built-in admin and multiple Viewer accounts, but has no login rate limiting or CSRF token mechanism. Viewer accounts can access live streams and recordings, not administrative endpoints. Do not publicly expose the service. Treat this as an early integration/test build.

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
2. Extend basic ONVIF discovery to camera provisioning, PTZ and automatic ONVIF camera-native motion event subscriptions (webhook support is already available).
3. Disk usage limits, retention cleanup and failed-disk alerts.
4. Multi-user accounts, encrypted camera secrets, login rate limiting, CSRF protections and HTTPS deployment.
5. Benchmark 9/12/16 active streams and publish real FPS, latency, temperature and storage throughput results.

## License

See [LICENSE](LICENSE) in this repository.
