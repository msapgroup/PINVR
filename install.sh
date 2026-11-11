#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run: sudo bash install.sh'; exit 1; fi
if [[ $(uname -m) != aarch64 ]]; then echo 'This installer targets 64-bit Raspberry Pi OS (aarch64).'; exit 1; fi
apt-get update
apt-get install -y python3-venv ffmpeg
id pinvr &>/dev/null || useradd --system --home /var/lib/pinvr --shell /usr/sbin/nologin pinvr
install -d -m 700 -o pinvr -g pinvr /var/lib/pinvr
install -d -m 755 /opt/pinvr
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
ExecStart=/opt/pinvr/.venv/bin/uvicorn pinvr.main:app --host 0.0.0.0 --port 8080 --workers 1
Restart=on-failure
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
systemctl enable --now pinvr
cat <<MSG
PiNVR installed: http://$(hostname -I | awk '{print $1}'):8080
Initial login: Admin / raspberry — CHANGE PASSWORD IMMEDIATELY.
IMPORTANT: Recordings will NOT start until /mnt/pinvr-recordings is mounted as an external drive.
For example, set a persistent UUID mount in /etc/fstab and make the directory writable by pinvr.
DO NOT expose port 8080 directly to the public internet.
MSG
