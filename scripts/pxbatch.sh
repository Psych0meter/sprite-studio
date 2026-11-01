#!/usr/bin/env bash
# pxbatch.sh: remaster every sprite of a model (pxmodel.py) with pxmake.sh,
# a few variants each, then build one comparison sheet per kind (or per
# group when the model has no kinds) to pick from.
#
#   pxbatch.sh --model units                    every sprite of a model (long: hours)
#   pxbatch.sh --model items                    every item icon
#   pxbatch.sh --model units --kinds small,large   some kinds
#   pxbatch.sh --model units --groups red,blue  some groups
#   pxbatch.sh --model units --dry-run          list what would run
#   pxbatch.sh --model units --invent 6 --groups red   6 new red sprites of each kind
#   pxbatch.sh --model units --invent 4 --ideas "crescent hull|long lance|twin pods"
#
# Each sprite starts from itself, takes its colours from its own group, and
# keeps its kind's size and pixel grid. Sprites the model's batch_skip
# matches (two-state art) are left out: a pair must stay the same ship, so
# it is made from the variant you pick.
#
# Options: --model M (required), --groups a,b, --kinds a,b, -n N (variants, default 2), --tag NAME (batch folder,
# default today's date), --denoise D (one value for every sprite), --palette P
# (one palette for every sprite; default: each group's own, none: no palette),
# --fill PCT (the share of the canvas each sprite fills, remasters too;
# default: the kinds' fill, for invented sprites only), --dry-run, --invent N (instead of remastering: N new sprites for each group
# and kind, drawn from the description alone; kinds the group has sprites of,
# or the ones given), --ideas "a|b|c" (with --invent: what each new one is or
# looks like, used in turn).
# A sprite whose folder is finished is skipped, so a stopped batch resumes
# where it was: rerun the same command (same --tag).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PX_OUTPUT="${PX_OUTPUT:-${SG_DATA:-$HOME/sprites}/output}"
export PX_OUTPUT

model="" groups="" kinds="" count=2 tag="$(date +%Y%m%d)" dry=0 extra=() invent=0 ideas=""
idea_list=()
while [ $# -gt 0 ]; do
	case "$1" in
	--model) model="$2"; shift 2 ;;
	--groups) groups="$2"; shift 2 ;;
	--kinds | --only) kinds="$2"; shift 2 ;;
	-n) count="$2"; shift 2 ;;
	--tag) tag="$2"; shift 2 ;;
	--dry-run) dry=1; shift ;;
	--invent) invent="$2"; shift 2 ;;
	--ideas) ideas="$2"; shift 2 ;;
	--denoise) extra+=(--denoise "$2"); shift 2 ;;
	--palette) extra+=(--palette "$2"); shift 2 ;;
	--fill) extra+=(--fill "$2"); shift 2 ;;
	-h | --help) sed -n '2,29p' "$0"; exit 0 ;;
	*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
done
batch="$PX_OUTPUT/batch-$tag"
log="$batch/batch.log"
failed=() done_n=0 todo_n=0
if [ "$invent" -gt 0 ] 2>/dev/null; then
	# New sprites: one row per (group, kind) and number, "-" for the path.
	IFS='|' read -r -a idea_list <<<"$ideas"
	list="$(python3 -W ignore "$HERE/pxmodel.py" invent-list "$model" --groups "$groups" --kinds "$kinds" | while IFS=$'\t' read -r g k sz; do
		for i in $(seq 1 "$invent"); do printf -- '-\t%s\t%s\t%s\t%s\n' "$g" "$k" "$sz" "$i"; done
	done)" || exit 2
	[ -n "$list" ] || { echo "nothing to invent: no enabled group (or no group with sprites of those kinds)" >&2; exit 2; }
else
	list="$(python3 -W ignore "$HERE/pxmodel.py" batch-list "$model" --groups "$groups" --kinds "$kinds")" || exit 2
fi
[ "$dry" = 1 ] || mkdir -p "$batch"

while IFS=$'\t' read -r path group kind size num; do
	[ -n "$path" ] || continue
	[ "$kind" = - ] && kind=""
	class="${kind:-$group}"
	if [ "$path" = - ]; then
		id="new_${kind:+${kind}_}${group}_${num}"
		idea=""
		if [ ${#idea_list[@]} -gt 0 ]; then idea="${idea_list[$(((num - 1) % ${#idea_list[@]}))]}"; fi
		idea="$(echo "$idea" | sed 's/^ *//; s/ *$//')"
		start=(--size "$size" ${idea:+--extra "$idea"})
	else
		id="$(basename "$path" .png)"
		start=(--init "$path" --size "$size")
	fi
	dir="$batch/$class/$id"
	if [ -f "$dir/.done" ]; then
		done_n=$((done_n + 1)); continue
	fi
	todo_n=$((todo_n + 1))
	args=(--model "$model" --group "$group" ${kind:+--kind "$kind"} "${start[@]}"
		-n "$count" --name "$id" --out "$dir" "${extra[@]}")
	if [ "$dry" = 1 ]; then
		echo "would run: pxmake.sh ${args[*]}"
		continue
	fi
	rm -rf "$dir"
	echo "=== $(date '+%F %T') $id" | tee -a "$log"
	out="$(mktemp)"
	"$HERE/pxmake.sh" "${args[@]}" >"$out" 2>&1
	rc=$?
	cat "$out" >>"$log"
	if [ $rc -ne 0 ]; then
		failed+=("$id")
		reason="$(grep -E '^error: |Error: |Exception: |not found|isn.t |refused|unknown ' "$out" | tail -1)"
		[ -n "$reason" ] || reason="$(grep -v '^\s*$' "$out" | tail -1)"
		echo "    FAILED: ${reason:-pxmake.sh exited with code $rc}" | tee -a "$log"
		# The same cause for every sprite (no LoRA, ComfyUI down): stop now.
		if [ ${#failed[@]} -ge 3 ] && [ "$todo_n" -eq ${#failed[@]} ] && [ "$reason" = "${last_reason:-}" ]; then
			rm -f "$out"
			echo "error: the first ${#failed[@]} sprites all failed the same way, batch stopped: ${reason#error: }" | tee -a "$log"
			exit 1
		fi
		last_reason="$reason"
	fi
	rm -f "$out"
done <<<"$list"

if [ "$dry" = 0 ]; then
	for d in "$batch"/*/; do
		[ -d "$d" ] || continue
		python3 -W ignore "$HERE/pxtool.py" sheet "$batch/sheet_$(basename "$d").png" "$d"*/ | tee -a "$log"
	done
fi
echo
echo "batch: $batch   model: $model   already done: $done_n   run now: $todo_n   failed: ${#failed[@]}"
[ ${#failed[@]} -eq 0 ] || printf '  failed: %s\n' "${failed[@]}"
[ "$dry" = 1 ] && echo "about $((todo_n * count * 15 / 60)) min of rendering at ~15 s per image (SD 1.5)"
# 0: all good; 1: nothing made; 4: some sprites failed (the others are usable).
if [ ${#failed[@]} -gt 0 ]; then
	[ ${#failed[@]} -eq "$todo_n" ] && { echo "error: all $todo_n sprites failed"; exit 1; }
	exit 4
fi
exit 0
