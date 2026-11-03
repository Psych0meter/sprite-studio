#!/usr/bin/env bash
# pxpreviews.sh: render a training run's preview images afterwards, through ComfyUI. Training renders them
# itself on big cards (every 2 epochs); on small ones (SDXL under 12 GB) it cannot, so the checkpoints are
# rendered here, each with the run's preview prompts and the same seeds, into the same folder
# (<output>/sample/), where the Train page shows them as a grid. A preview that exists is not rendered
# again, so this fills the gaps and can be stopped and started again.
#
#   CATEGORY=units MODEL=sdxl EPOCHS=4,8 PROMPTS=2 scripts/pxpreviews.sh
#
# Environment:
#   CATEGORY      the model id (pxmodel.py list)                                   (required)
#   MODEL         sd15 (default) or sdxl: which training's checkpoints
#   EPOCHS        "2,4,6": only these epochs (default: every checkpoint, the final file included)
#   PROMPTS       render the first N preview prompts (default 2; 0 is none)
#   FINAL_EPOCH   the epoch the final (unnumbered) file stands for, when it has no numbered twin
#   COMFY, PX_LORA_HOME, PX_COMFY_URL as for train_lora.sh and pxcomfy.py.
# Needs ComfyUI running. The checkpoints are copied into ComfyUI's loras folder as px_preview_*.safetensors
# for the run and removed at the end.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATEGORY="${CATEGORY:?set CATEGORY to a model id (pxmodel.py list)}"
MODEL="${MODEL:-sd15}"
HOME_DIR="${PX_LORA_HOME:-$HOME/lora}"
COMFY="${COMFY:-$HOME/ComfyUI}"
PX_COMFY_URL="${PX_COMFY_URL:-http://127.0.0.1:8188}"
case "$MODEL" in
sd15) RES=512 SUFFIX="" CKPT="" ;;
sdxl) RES=1024 SUFFIX="_xl" CKPT="sd_xl_base_1.0.safetensors" ;;
*) echo "unknown MODEL: $MODEL (sd15, sdxl)" >&2; exit 2 ;;
esac
py="$HOME_DIR/venv/bin/python"
[ -x "$py" ] || py=python3
model_py() { "$py" -W ignore "$HERE/pxmodel.py" "$@"; }
TRIGGER="$(model_py get "$CATEGORY" trigger)" || { echo "unknown model: $CATEGORY" >&2; exit 2; }
NAME="$TRIGGER$SUFFIX"
OUT="$HOME_DIR/$CATEGORY/output$SUFFIX"
[ -d "$OUT" ] || { echo "error: no training output in $OUT: train this model first" >&2; exit 2; }

mapfile -t PLAN < <(model_py preview-plan "$CATEGORY" --out "$OUT" --name "$NAME" --res "$RES" \
	--epochs "${EPOCHS:-}" --prompts "${PROMPTS:-2}" --final-epoch "${FINAL_EPOCH:-0}")
total=${#PLAN[@]}
if [ "$total" -eq 0 ]; then
	echo "nothing to render: every checkpoint already has its previews"
	exit 0
fi
if ! curl -s -o /dev/null "$PX_COMFY_URL/system_stats"; then
	echo "error: ComfyUI isn't answering at $PX_COMFY_URL: start it first (systemctl start comfyui)" >&2
	exit 2
fi

export PX_RES="$RES"
[ -z "$CKPT" ] || export PX_CKPT="$CKPT"
mkdir -p "$OUT/sample" "$COMFY/models/loras"
tmp="$(mktemp -d)"
declare -A copied=()
# shellcheck disable=SC2329  # runs from the EXIT trap
cleanup() {
	for f in "${copied[@]}"; do rm -f "$COMFY/models/loras/$f"; done
	rm -rf "$tmp"
}
trap cleanup EXIT

n=0 nfail=0 t0=$(date +%s)
for row in "${PLAN[@]}"; do
	IFS=$'\t' read -r epoch ckpt idx seed steps cfg prompt neg <<<"$row"
	n=$((n + 1))
	lora="px_preview_${NAME}_e$(printf %06d "$epoch").safetensors"
	if [ -z "${copied[$epoch]:-}" ]; then
		cp "$ckpt" "$COMFY/models/loras/$lora" || { echo "error: cannot copy $ckpt into ComfyUI's loras folder" >&2; exit 2; }
		copied[$epoch]="$lora"
	fi
	echo "=== [$n/$total] $(date '+%H:%M') epoch $epoch, prompt $((idx + 1)) (elapsed $((($(date +%s) - t0) / 60)) min)"
	rm -f "$tmp"/*.png
	if PX_LORA="$lora" python3 "$HERE/pxcomfy.py" "$prompt" --out "$tmp" \
		--negative "$neg" -n 1 --seed "$seed" --steps "$steps" --cfg "$cfg"; then
		img="$(find "$tmp" -maxdepth 1 -name '*.png' | head -1)"
		if [ -n "$img" ]; then
			mv "$img" "$OUT/sample/${NAME}_e$(printf %06d "$epoch")_$(printf %02d "$idx")_$(date +%Y%m%d%H%M%S).png"
			continue
		fi
	fi
	nfail=$((nfail + 1))
	echo "    FAILED: epoch $epoch, prompt $((idx + 1))"
	if [ "$nfail" -ge 3 ] && [ "$nfail" -eq "$n" ]; then
		echo "error: the first $nfail renders all failed, previews stopped: see above"
		exit 1
	fi
done
echo "all done in $((($(date +%s) - t0) / 60)) min: $OUT/sample/"
if [ "$nfail" -gt 0 ]; then
	echo "failed: $nfail of $total renders"
	[ "$nfail" -eq "$total" ] && exit 1
	exit 4
fi
exit 0
