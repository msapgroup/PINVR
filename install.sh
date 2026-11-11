#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run: sudo bash install.sh'; exit 1; fi
if [[ $(uname -m) != aarch64 ]]; then echo 'This installer targets 64-bit Raspberry Pi OS (aarch64).'; exit 1; fi
apt-get update
apt-get install -y python3-venv ffmpeg curl ca-certificates
id pinvr &>/dev/null || useradd --system --home /var/lib/pinvr --shell /usr/sbin/nologin pinvr
install -d -m 700 -o pinvr -g pinvr /var/lib/pinvr
install -d -m 755 /opt/pinvr /opt/pinvr/bin
# Pin and verify the official go2rtc ARM64 release.
GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/download/v1.9.15/go2rtc_linux_arm64"
GO2RTC_SHA256="af416f22b21a24ab83b8d78642190b4932ae695b4e386853f11d1b0319781763"
curl -fsSL --retry 3 "$GO2RTC_URL" -o /tmp/go2rtc-pinvr-download
echo "$GO2RTC_SHA256  /tmp/go2rtc-pinvr-download" | sha256sum -c -
install -m 755 /tmp/go2rtc-pinvr-download /opt/pinvr/bin/go2rtc
rm -f /tmp/go2rtc-pinvr-download
cp -a pinvr static requirements.txt /opt/pinvr/
python3 -m venv /opt/pinvr/.venv
/opt/pinvr/.venv/bin/pip install --no-cache-dir -r /opt/pinvr/requirements.txt
chown -R root:root /opt/pinvr
cat >/etc/systemd/system/pinvr.service <<'UNIT'
[Unit]
Description=PiNVR Secure View
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=pinvr
Group=pinvr
WorkingDirectory=/opt/pinvr
Environment=PINVR_DATA=/var/lib/pinvr
Environment=PINVR_RECORDINGS=/mnt/pinvr-recordings
Environment=PINVR_MANAGED=1
ExecStart=/opt/pinvr/.venv/bin/python -m pinvr.launcher
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/pinvr /mnt/pinvr-recordings
PrivateTmp=true
[Install]
WantedBy=multi-user.target
UNIT
install -d -m 755 /mnt/pinvr-recordings
systemctl daemon-reload
systemctl enable pinvr
systemctl restart pinvr
cat <<MSG
PiNVR installed. Web access port is 8080 by default; change it under Settings > Network Access.
To verify current port: sudo journalctl -u pinvr -n 30
Initial login: Admin / raspberry — CHANGE PASSWORD IMMEDIATELY.
IMPORTANT: Recordings will NOT start until /mnt/pinvr-recordings is mounted as an external drive.
For example, set a persistent UUID mount in /etc/fstab and make the directory writable by pinvr.
Allow TCP/UDP port 8555 on your private network for WebRTC media.
DO NOT expose the admin web port or 8555 directly to the public internet.
For remote access use your VPN. Any external router mapping port may be different from the PiNVR listening port.
MSG
