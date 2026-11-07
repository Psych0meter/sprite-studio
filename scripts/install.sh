#!/usr/bin/env bash
# install.sh: set up Sprite Studio on a Debian / Ubuntu machine or container
# with an NVIDIA GPU: ComfyUI (generation, systemd service on port 8188),
# kohya sd-scripts (LoRA training), Sprite Studio itself (web app, service on
# port 8189), the SD 1.5 base model. Run as root, from a checkout of the
# repository:
#
#   sudo scripts/install.sh
#
# Settings (environment variables, defaults in brackets):
#   SG_USER       the user that owns and runs everything        [spritegen]
#   SG_APP        where the app is installed           [/home/$SG_USER/spritegen]
#   SG_DATA       the data folder: input/, game/ (sprites to learn from)
#                 and output/ (results); a NAS share works   [/home/$SG_USER/sprites]
#   TORCH_INDEX   PyTorch wheel index  [chosen from the GPU: cu128 for RTX 50
#                 (Blackwell) cards, cu126 for the others, GTX 10 included]
#   TORCH_VERSION pin a version (e.g. 2.7.1)
#   NVIDIA_RUN    in a container: the NVIDIA .run installer of the HOST's
#                 driver version, to install its user-space part (nvidia-smi...)
#                 [skipped if empty]
#
# Before: the GPU visible (/dev/nvidia0, nvidiactl, nvidia-uvm; in an LXC
# container, passed through: docs/INSTALL.md), and SG_DATA mounted if it is
# a share. Safe to run again (it also updates the app): finished steps are
# skipped.
set -euo pipefail

SG_USER="${SG_USER:-spritegen}"
HOME_DIR="/home/$SG_USER"
SG_APP="${SG_APP:-$HOME_DIR/spritegen}"
SG_DATA="${SG_DATA:-$HOME_DIR/sprites}"
TORCH_INDEX="${TORCH_INDEX:-}"
TORCH_VERSION="${TORCH_VERSION:-}"
NVIDIA_RUN="${NVIDIA_RUN:-}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMFY="$HOME_DIR/ComfyUI"
SD15_URL="https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors"

step() { echo; echo "=== $*"; }
as_user() { runuser -u "$SG_USER" -- "$@"; }

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }

step "GPU devices"
for d in /dev/nvidia0 /dev/nvidiactl /dev/nvidia-uvm; do
	[ -e "$d" ] || { echo "$d is missing: install the NVIDIA driver, or pass the GPU to the container (docs/INSTALL.md)" >&2; exit 2; }
done
ls -l /dev/nvidia*

step "packages"
apt-get update -q
apt-get install -y -q git curl ca-certificates python3 python3-venv python3-pip python3-dev \
	build-essential tmux unzip sudo python3-numpy python3-pil python3-scipy

if [ -n "$NVIDIA_RUN" ]; then
	step "NVIDIA user-space libraries (no kernel module)"
	sh "$NVIDIA_RUN" --no-kernel-module --silent
fi
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv ||
	echo "(nvidia-smi not installed: optional, PyTorch is checked below)"

# The PyTorch build must include the card's architecture: newest cards need
# a recent CUDA build, the oldest ones a build that still has them.
if [ -z "$TORCH_INDEX" ]; then
	cap="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' .')"
	if [ -n "$cap" ] && [ "$cap" -ge 100 ] 2>/dev/null; then
		TORCH_INDEX=https://download.pytorch.org/whl/cu128
	else
		TORCH_INDEX=https://download.pytorch.org/whl/cu126
	fi
	echo "PyTorch builds from $TORCH_INDEX (GPU compute capability: ${cap:-unknown})"
fi

step "user $SG_USER"
id "$SG_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "Sprite Studio" "$SG_USER"
for g in video render; do getent group "$g" >/dev/null && usermod -aG "$g" "$SG_USER"; done

step "ComfyUI"
[ -d "$COMFY" ] || as_user git clone https://github.com/comfyanonymous/ComfyUI.git "$COMFY"
[ -d "$COMFY/.venv" ] || as_user python3 -m venv "$COMFY/.venv"
as_user "$COMFY/.venv/bin/pip" install -q --upgrade pip
if ! as_user "$COMFY/.venv/bin/python" -c 'import torch' 2>/dev/null; then
	as_user "$COMFY/.venv/bin/pip" install "torch${TORCH_VERSION:+==$TORCH_VERSION}" torchvision torchaudio \
		--index-url "$TORCH_INDEX"
fi
# The PyTorch build must include the card's own architecture (sm_61 for a
# GTX 10 card, sm_89 for an RTX 40...), or an older-compatible one.
as_user "$COMFY/.venv/bin/python" - <<'EOF'
import sys, torch
arch = torch.cuda.get_arch_list()
print("torch", torch.__version__, "cuda available:", torch.cuda.is_available(), arch)
if not torch.cuda.is_available():
    sys.exit("PyTorch does not see the GPU: check the passthrough and the driver libraries")
major, minor = torch.cuda.get_device_capability(0)
mine = major * 10 + minor
have = [int(a[3:]) for a in arch if a.startswith("sm_") and a[3:].isdigit()]
# a build runs on its own architectures and on newer minors of the same major
if not any(h // 10 == major and h <= mine for h in have):
    sys.exit(f"this PyTorch build has no kernels for this GPU (sm_{mine}): set TORCH_VERSION "
             "(older cards) or TORCH_INDEX (newer cards, e.g. .../whl/cu128) and rerun")
print("GPU:", torch.cuda.get_device_name(0))
EOF
as_user "$COMFY/.venv/bin/pip" install -q -r "$COMFY/requirements.txt"

step "Sprite Studio files ($SG_APP)"
install -d -o "$SG_USER" -g "$SG_USER" "$SG_APP" "$SG_DATA"
for d in input game output; do install -d -o "$SG_USER" -g "$SG_USER" "$SG_DATA/$d" 2>/dev/null || true; done
# (nothing to copy when install.sh runs from the installed copy itself)
if [ "$(realpath "$REPO")" != "$(realpath "$SG_APP")" ]; then
	rm -rf "$SG_APP/webapp/static"
	cp -r "$REPO/scripts" "$REPO/webapp" "$SG_APP/"
	cp "$REPO"/README.md "$REPO"/CHANGELOG.md "$REPO"/VERSION "$SG_APP/" 2>/dev/null || true
	if [ -d "$REPO/docs" ]; then cp -r "$REPO/docs" "$SG_APP/"; fi
fi
# Before 0.1.0 the scripts sat at the top of the folder: they live in scripts/ now.
for f in "$SG_APP"/px*.sh "$SG_APP"/px*.py "$SG_APP"/train_lora.sh "$SG_APP"/install.sh; do
	if [ -f "$f" ] && [ -f "$SG_APP/scripts/$(basename "$f")" ]; then rm -f "$f"; fi
done
chown -R "$SG_USER:$SG_USER" "$SG_APP"
chmod +x "$SG_APP"/scripts/*.sh "$SG_APP"/scripts/*.py "$SG_APP/webapp/run.sh"
if ! grep -q "$SG_APP/scripts" "$HOME_DIR/.bashrc" 2>/dev/null; then
	cat >>"$HOME_DIR/.bashrc" <<EOF

# Sprite Studio
export SG_DATA=$SG_DATA PX_INPUT=$SG_DATA/input PX_GAME=$SG_DATA/game PX_OUTPUT=$SG_DATA/output
export PATH="$SG_APP/scripts:\$PATH"
EOF
fi

step "base model"
ckpt="$COMFY/models/checkpoints"
if [ ! -f "$ckpt/v1-5-pruned-emaonly.safetensors" ]; then
	echo "downloading SD 1.5 (about 4 GB)"
	as_user curl -L --fail -o "$ckpt/v1-5-pruned-emaonly.safetensors" "$SD15_URL"
fi
find "$ckpt" -maxdepth 1 -name "*.safetensors" -printf "  %f  %s bytes\n"

step "ComfyUI service"
cat >/etc/systemd/system/comfyui.service <<EOF
[Unit]
Description=ComfyUI (Sprite Studio)
After=network-online.target

[Service]
User=$SG_USER
WorkingDirectory=$COMFY
ExecStart=$COMFY/.venv/bin/python main.py --listen 0.0.0.0 --port 8188
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now comfyui
for _ in $(seq 60); do curl -s -o /dev/null http://127.0.0.1:8188/system_stats && break; sleep 2; done
curl -s http://127.0.0.1:8188/system_stats | head -c 300; echo

step "LoRA training (kohya sd-scripts)"
# Training needs the GPU memory ComfyUI holds: train_lora.sh stops nothing
# by itself, so stop ComfyUI before a training run (see the guide).
systemctl stop comfyui
as_user env COMFY="$COMFY" "$SG_APP/scripts/train_lora.sh" setup
systemctl start comfyui

step "Sprite Studio (web app, port 8189)"
# The app stops ComfyUI for a training run and starts it again: allow just that.
SYSTEMCTL="$(command -v systemctl)"
cat >/etc/sudoers.d/spritegen-comfyui <<EOF
$SG_USER ALL=(root) NOPASSWD: $SYSTEMCTL start comfyui, $SYSTEMCTL stop comfyui, $SYSTEMCTL restart comfyui
EOF
chmod 440 /etc/sudoers.d/spritegen-comfyui
command -v sudo >/dev/null || apt-get install -y -q sudo
visudo -cf /etc/sudoers.d/spritegen-comfyui
# Settings added by hand to the unit (a port, a host, allowed origins) survive a reinstall.
UNIT=/etc/systemd/system/spritegen-web.service
KEPT=""
[ -f "$UNIT" ] && KEPT="$(grep -E '^Environment="?SG_WEB_(PORT|HOST|ALLOWED_ORIGINS)=' "$UNIT" || true)"
# Everyone signs in with an account (the first start asks for a one-time setup code, printed in the service's log),
# so the service listens on the network. SG_WEB_HOST=127.0.0.1 keeps it on this machine (behind a reverse proxy).
# The old single password (SG_WEB_PASSWORD) and the open mode (SG_WEB_ALLOW_OPEN) are gone: they are dropped here.
if [ -f "$UNIT" ] && grep -qE 'SG_WEB_(PASSWORD|ALLOW_OPEN)=' "$UNIT"; then
	echo "  note: SG_WEB_PASSWORD / SG_WEB_ALLOW_OPEN are no longer used: sign in with an account (see docs/INSTALL.md)."
fi
HOST_LINE="Environment=SG_WEB_HOST=0.0.0.0"
EXTRA=""
KEPT="$(printf '%s\n' "$KEPT" | grep -vE 'SG_WEB_HOST=' || true)"
[ -z "${SG_WEB_HOST:-}" ] || HOST_LINE="Environment=SG_WEB_HOST=$SG_WEB_HOST"  # an address given to this script wins
cat >/etc/systemd/system/spritegen-web.service <<EOF
[Unit]
Description=Sprite Studio (web app)
After=network-online.target comfyui.service

[Service]
User=$SG_USER
WorkingDirectory=$SG_APP/webapp
Environment=SG_DATA=$SG_DATA PX_INPUT=$SG_DATA/input PX_GAME=$SG_DATA/game PX_OUTPUT=$SG_DATA/output
$HOST_LINE
$EXTRA
$KEPT
ExecStart=$SG_APP/webapp/run.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable spritegen-web
systemctl restart spritegen-web

step "folders"
for d in "$SG_DATA/input" "$SG_DATA/game" "$SG_DATA/output"; do
	if [ -d "$d" ]; then echo "  $d ok"; else echo "  $d missing (mount the share, or create it)"; fi
done
if as_user test -w "$SG_DATA/output" 2>/dev/null; then
	echo "  $SG_USER can write to $SG_DATA/output"
else
	echo "  $SG_USER cannot write to $SG_DATA/output yet (permissions / uid mapping)"
fi

step "done"
cat <<EOF
Sprite Studio $(cat "$SG_APP/VERSION" 2>/dev/null || true)
  Studio:   http://$(hostname -I | awk '{print $1}'):8189   (systemctl status spritegen-web)
  ComfyUI:  http://$(hostname -I | awk '{print $1}'):8188   (systemctl status comfyui)
  Scripts:  $SG_APP/scripts   (su - $SG_USER)
Next: open the studio, create a model in Models, add its sprites, train it.
EOF
