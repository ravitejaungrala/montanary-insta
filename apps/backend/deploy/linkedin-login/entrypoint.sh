#!/usr/bin/env bash
# Fargate login-container entrypoint: start a virtual display + VNC + noVNC so the
# user can SEE and drive the browser, then run the login task which captures the
# profile. See fargate_login_task.py.
set -euo pipefail

export DISPLAY=":99"
NOVNC_PORT="${LI_NOVNC_PORT:-6080}"

# 1) Virtual display.
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
sleep 2

# 2) VNC server on the display (localhost:5900).
#    SECURITY: this exposes control of the browser. HARDENING (do before prod):
#      - lock the task security group to the connecting user's IP,
#      - set LI_VNC_PASSWORD (below) and/or terminate the task right after capture,
#      - front it with TLS. Do NOT leave 6080 open to 0.0.0.0 unauthenticated.
if [ -n "${LI_VNC_PASSWORD:-}" ]; then
  mkdir -p /root/.vnc
  x11vnc -storepasswd "${LI_VNC_PASSWORD}" /root/.vnc/passwd
  x11vnc -display :99 -rfbauth /root/.vnc/passwd -forever -shared -bg -rfbport 5900
else
  x11vnc -display :99 -nopw -forever -shared -bg -rfbport 5900
fi

# 3) noVNC (websockify) serves vnc.html on NOVNC_PORT, bridging to VNC 5900.
websockify --web=/usr/share/novnc "${NOVNC_PORT}" localhost:5900 &
sleep 1

# 4) Discover the task's public IP for the viewer URL (Fargate assignPublicIp=ENABLED).
LI_PUBLIC_IP="$(curl -s --max-time 5 https://checkip.amazonaws.com || true)"
export LI_PUBLIC_IP="$(echo "${LI_PUBLIC_IP}" | tr -d '[:space:]')"
echo "Public IP: ${LI_PUBLIC_IP:-unknown}  noVNC port: ${NOVNC_PORT}"

# 5) Run the login task (publishes viewer_url, drives login, saves profile, exits).
cd /app
exec python -m gtm.linkedin.fargate_login_task
