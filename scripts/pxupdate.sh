#!/usr/bin/env bash
# pxupdate.sh: bring ComfyUI or kohya sd-scripts up to date with their upstream branch: git pull (fast-forward
# only, so local edits are never overwritten), then their Python requirements in their own environment.
#
#   scripts/pxupdate.sh comfyui        ComfyUI: pull, pip install -r requirements.txt in its .venv, restart it
#   scripts/pxupdate.sh sd-scripts     kohya sd-scripts: pull, pip install -r requirements.txt in <lora home>/venv
#
# Environment: COMFY (ComfyUI folder, default ~/ComfyUI), PX_LORA_HOME (default ~/lora), COMFY_SERVICE (the
# systemd unit to restart, default comfyui; needs the sudo rule install.sh adds). Nothing else is touched:
# not the torch build, not models, not your data.
set -euo pipefail

target="${1:-}"
COMFY="${COMFY:-$HOME/ComfyUI}"
HOME_DIR="${PX_LORA_HOME:-$HOME/lora}"
case "$target" in
comfyui) dir="$COMFY" venv="$COMFY/.venv" ;;
sd-scripts) dir="$HOME_DIR/sd-scripts" venv="$HOME_DIR/venv" ;;
-h | --help | "")
	sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
	exit 0
	;;
*)
	echo "unknown target: $target (comfyui or sd-scripts)" >&2
	exit 2
	;;
esac

[ -d "$dir/.git" ] || { echo "$dir is not a git checkout" >&2; exit 1; }
if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
	echo "$dir has local changes to tracked files: not updating (commit or stash them first)" >&2
	exit 1
fi
echo "before: $(git -C "$dir" describe --tags --always)"
git -C "$dir" pull --ff-only
echo "after:  $(git -C "$dir" describe --tags --always)"
if [ -f "$dir/requirements.txt" ] && [ -x "$venv/bin/pip" ]; then
	(cd "$dir" && "$venv/bin/pip" install -r requirements.txt)
else
	echo "no requirements.txt or no environment at $venv: skipped the Python packages"
fi
if [ "$target" = comfyui ]; then
	if sudo -n systemctl restart "${COMFY_SERVICE:-comfyui}" 2>/dev/null; then
		echo "ComfyUI restarted"
	else
		echo "restart ComfyUI yourself (the sudo rule for systemctl is missing)"
	fi
fi
echo "done"
