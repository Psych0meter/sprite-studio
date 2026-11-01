#!/usr/bin/env bash
# pxmatrix.sh: an unattended comparison run, to choose between LoRA files
# (checkpoints, older trainings) and strengths for one model.
#
#   MODEL=units LORAS="e6:pxunit.safetensors e8:pxunit_e8.safetensors" nohup ./pxmatrix.sh > ~/pxmatrix.log 2>&1 &
#
# The test subjects come from the model (pxmodel.py matrix-subjects): for
# each enabled group, a new design on its most common kind and a remaster of
# one of its sprites. Every subject is rendered with each LoRA file of LORAS
# and each strength of STRENGTHS, on the same two seeds, so a sheet compares
# like with like. Results go to $PX_OUTPUT/matrix-<TAG>/<subject>/..., one
# sheet per subject (rows = settings, columns = original + 2 seeds) to
# $PX_OUTPUT/matrix-<TAG>/sheet_<subject>.png. Finished cells are skipped,
# so a stopped run resumes when started again with the same TAG.
#
# Environment:
#   MODEL      the model                                  (required)
#   LORAS      "label:file ..." in ComfyUI's models/loras (default: the model's)
#   STRENGTHS  "0.8 1.0" (default)
#   TAG (default 1), SEED (default 1000), and pxmake.sh's PX_* settings.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PX_OUTPUT="${PX_OUTPUT:-${SG_DATA:-$HOME/sprites}/output}"
PX_COMFY_URL="${PX_COMFY_URL:-http://127.0.0.1:8188}"
MODEL="${MODEL:?set MODEL to a model id (pxmodel.py list)}"
trigger="$(python3 -W ignore "$HERE/pxmodel.py" get "$MODEL" trigger)" || exit 2
LORAS="${LORAS:-main:$trigger.safetensors}"
STRENGTHS="${STRENGTHS:-0.8 1.0}"
TAG="${TAG:-1}" SEED="${SEED:-1000}"
OUT="$PX_OUTPUT/matrix-$TAG"
mkdir -p "$OUT"

if ! curl -s -o /dev/null "$PX_COMFY_URL/system_stats"; then
	echo "ComfyUI isn't answering at $PX_COMFY_URL: start it first (systemctl start comfyui)." >&2
	exit 2
fi

mapfile -t SUBJECTS < <(python3 -W ignore "$HERE/pxmodel.py" matrix-subjects "$MODEL")
[ ${#SUBJECTS[@]} -gt 0 ] || { echo "model $MODEL has no sprite in an enabled group to test with" >&2; exit 2; }
read -r -a lora_list <<<"$LORAS"
read -r -a strength_list <<<"$STRENGTHS"
total=$((${#SUBJECTS[@]} * ${#lora_list[@]} * ${#strength_list[@]}))
n=0 t0=$(date +%s) nfail=0
for s in "${SUBJECTS[@]}"; do
	sname="${s%%|*}"
	IFS=$'\t' read -r -a sopts <<<"${s#*|}"
	for l in "${lora_list[@]}"; do
		lname="${l%%:*}" lfile="${l#*:}"
		for st in "${strength_list[@]}"; do
			n=$((n + 1))
			cell="$OUT/$sname/${sname}__${lname}_s${st}"
			[ -f "$cell/.done" ] && continue
			rm -rf "$cell"
			echo "=== [$n/$total] $(date '+%H:%M') $sname $lname strength $st (elapsed $((($(date +%s) - t0) / 60)) min)"
			out="$(mktemp)"
			PX_LORA="$lfile" PX_LORA_STRENGTH="$st" \
				"$HERE/pxmake.sh" --model "$MODEL" "${sopts[@]}" -n 2 --seed "$SEED" \
				--name "${sname}_${lname}_s${st}" --out "$cell" 2>&1 | tee "$out"
			if [ "${PIPESTATUS[0]}" -ne 0 ]; then
				nfail=$((nfail + 1))
				reason="$(grep -E '^error: |Error: |not found|isn.t |refused' "$out" | tail -1)"
				echo "    FAILED: $sname $lname $st: ${reason:-see above}"
				if [ "$nfail" -ge 3 ] && [ "$nfail" -eq "$n" ]; then
					rm -f "$out"; echo "error: the first $nfail renders all failed, test stopped: ${reason#error: }"; exit 1
				fi
			fi
			rm -f "$out"
		done
	done
	python3 -W ignore "$HERE/pxtool.py" sheet "$OUT/sheet_$sname.png" "$OUT/$sname/"*/ --cell 280
done
echo "all done in $((($(date +%s) - t0) / 60)) min: $OUT/sheet_*.png"
if [ "$nfail" -gt 0 ]; then
	echo "failed: $nfail of $total renders"
	[ "$nfail" -eq "$total" ] && exit 1
	exit 4
fi
