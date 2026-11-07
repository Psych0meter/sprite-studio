#!/usr/bin/env bash
# run.sh: start Sprite Studio (the web app).
#
#   ./run.sh                 http://<container>:8189
#
# First start: creates webapp/.venv (FastAPI, uvicorn; numpy, Pillow and
# scipy come from the system packages, python3-numpy python3-pil
# python3-scipy).
#
# Settings: SG_WEB_PORT (8189), SG_WEB_HOST (127.0.0.1: this machine only;
# the service installed by install.sh listens on 0.0.0.0 only with a password), SG_WEB_PASSWORD
# (empty: no password; set one if the app is reachable by anyone else), and
# SG_DATA, PX_INPUT, PX_GAME, PX_OUTPUT, PX_COMFY_URL, COMFY,
# PX_LORA_HOME (the Settings page overrides them).
#
# Training stops ComfyUI and starts it again. For that the app's user needs
# this sudo rule, added once as root (install.sh does it):
#
#   cat > /etc/sudoers.d/spritegen-comfyui <<'EOF'
#   spritegen ALL=(root) NOPASSWD: /usr/bin/systemctl start comfyui, /usr/bin/systemctl stop comfyui, /usr/bin/systemctl restart comfyui
#   EOF
#   chmod 440 /etc/sudoers.d/spritegen-comfyui
#
# As a service: scripts/install.sh writes /etc/systemd/system/spritegen-web.service.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/uvicorn" ]; then
	echo "first start: creating $VENV"
	python3 -m venv --system-site-packages "$VENV"
	"$VENV/bin/pip" install -q --upgrade pip
	"$VENV/bin/pip" install -q "fastapi>=0.110" "uvicorn>=0.29"
fi
cd "$HERE"
exec "$VENV/bin/python" app.py
