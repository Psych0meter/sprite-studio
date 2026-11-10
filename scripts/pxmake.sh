#!/usr/bin/env bash
# pxmake.sh: generate sprites for any model (pxmodel.py) with ComfyUI and
# the model's LoRA (pxcomfy.py), then clean them into sprites
# (pxtool.py post). The prompt comes from the model: its trigger word, the
# group's words and keywords, the kind's words, your extra words, colours
# read from the group's sprites, the model's tail words.
#
#   pxmake.sh --model units --group red --kind large --extra "wide crescent body"
#   pxmake.sh --model units --group blue --kind small --init small_blue_4.png
#   pxmake.sh --model items --group tools --extra "curved bronze blade"
#
# Options:
#   --model M        a model id (pxmodel.py list)                    (required)
#   --group G        one of its groups (a team, an item family...)
#   --kind K         one of its kinds (size class), if it has some
#   --extra "TEXT"   what this sprite is or looks like ("plasma shotgun",
#                    "wide body, six side pods")
#   --colours "TEXT" colour words instead of the ones read from the group
#   --refs-kind K    read the colours from that kind of the group instead
#   --init FILE      start from this sprite (a path, or a name in the
#                    model's sprites, the input or the second source folder)
#   --sketch FILE    start from a rough silhouette you drew (denoise 0.85)
#   --mask FILE      with --init: a touch-up, only the white pixels of this
#                    mask (same size as the final sprite) are redrawn
#   --denoise D      how far from --init (default: the kind's, else 0.7)
#   --size PX        final size (default: the kind's, else the model's)
#   --mirror M       auto (mirrored when the original is symmetric), none,
#                    left or right                                    [auto]
#   --keep-green     keep the sprite's own greens (automatic when it has some)
#   --no-tidy        no lone-pixel clean-up and no dark outline
#   --outline-style S  dark (the darkest colour, default) or selective (a darker
#                    shade of the sprite's own colour)
#   --fill PCT       50 to 100: the share of the canvas the sprite's longest
#                    side takes (100: edge to edge). Default: the kind's (or
#                    the model's) fill for new sprites and sketches; a
#                    remaster keeps the original's size unless this is given
#   --keep-edge PX   a remaster of a tile kind keeps the original's outer PX
#                    pixels (so tiles that join still join); default: the
#                    kind's Keep edge. Tile kinds are redone in full colour
#   --palette P      a palette (id in $PX_LORA_HOME/.palettes, or a .json/.hex/
#                    .gpl file) put on the results; none: no palette. Default:
#                    the group's palette, if it has one
#   -n N             images (default 4)      --seed N   reproduce one
#   --name ID        name of the results (default <kind or model>_<group>)
#   --out DIR        folder for this job (default $PX_OUTPUT/<name>_<date>)
#
# Environment: PX_LORA_HOME (models), PX_INPUT, PX_GAME, PX_OUTPUT, and
# pxcomfy.py's PX_COMFY_URL, PX_LORA (default <trigger>.safetensors),
# PX_LORA_ID (the LoRA's stable id, written in job.txt), PX_LORA_STRENGTH,
# PX_CKPT, PX_RES. A LoRA named *_xl.safetensors renders
# at 1024 with the SDXL model.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SG_DATA="${SG_DATA:-$HOME/sprites}"
PX_INPUT="${PX_INPUT:-$SG_DATA/input}"
PX_GAME="${PX_GAME:-$SG_DATA/game}"
PX_OUTPUT="${PX_OUTPUT:-$SG_DATA/output}"
PX_LORA_HOME="${PX_LORA_HOME:-$HOME/lora}"
export PX_INPUT PX_GAME PX_OUTPUT PX_LORA_HOME
PX_TOOL="$HERE/pxtool.py"

model="" group="" kind="" extra="" colours="" refs_kind="" init="" sketch="" mask="" denoise=""
size="" mirror="auto" count=4 seed="" name="" out="" post_opts=() palette="" fill="" keep_edge=""
while [ $# -gt 0 ]; do
	case "$1" in
	--model) model="$2"; shift 2 ;;
	--group) group="$2"; shift 2 ;;
	--kind) kind="$2"; shift 2 ;;
	--extra) extra="$2"; shift 2 ;;
	--colours) colours="$2"; shift 2 ;;
	--refs-kind) refs_kind="$2"; shift 2 ;;
	--init) init="$2"; shift 2 ;;
	--sketch) sketch="$2"; shift 2 ;;
	--mask) mask="$2"; shift 2 ;;
	--denoise) denoise="$2"; shift 2 ;;
	--size) size="$2"; shift 2 ;;
	--mirror) mirror="$2"; shift 2 ;;
	--keep-green) post_opts+=(--keep-green); shift ;;
	--no-tidy) post_opts+=(--no-tidy); shift ;;
	--outline-style) post_opts+=(--outline-style "$2"); shift 2 ;;
	--palette) palette="$2"; shift 2 ;;
	--fill) fill="$2"; shift 2 ;;
	--keep-edge) keep_edge="$2"; shift 2 ;;
	-n) count="$2"; shift 2 ;;
	--seed) seed="$2"; shift 2 ;;
	--name) name="$2"; shift 2 ;;
	--out) out="$2"; shift 2 ;;
	-h | --help) sed -n '2,49p' "$0"; exit 0 ;;
	*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
done
[ -n "$model" ] || { echo "--model is required (pxmodel.py list shows the models)" >&2; exit 2; }
[ -n "$group" ] || { echo "--group is required (pxmodel.py sprites $model shows the groups)" >&2; exit 2; }

resolve() { # a path, or a name in the model's sprites, the input or the second source folder
	local f
	for f in "$1" "$PX_LORA_HOME/$model/sprites/$1" "$PX_INPUT/$1" "$PX_GAME/$1"; do
		[ -f "$f" ] && { echo "$f"; return; }
	done
	echo "not found: $1" >&2
	exit 2
}

start=""
if [ -n "$init" ]; then start="$(resolve "$init")"; fi
if [ -n "$sketch" ]; then start="$(resolve "$sketch")"; denoise="${denoise:-0.85}"; fi

# The prompt, the colour references and the defaults, from the model.
info="$(python3 -W ignore "$HERE/pxmodel.py" prompt "$model" --group "$group" ${kind:+--kind "$kind"} \
	--extra "$extra" --colours "$colours" ${refs_kind:+--refs-kind "$refs_kind"} ${start:+--exclude "$start"} ${palette:+--palette "$palette"})"
field() { python3 -c 'import json,sys; v=json.loads(sys.argv[1])[sys.argv[2]]; print(v if not isinstance(v, list) else "\n".join(v))' "$info" "$1"; }
prompt="$(field prompt)"
trigger="$(field trigger)"
size="${size:-$(field size)}"
[ -n "$start" ] && denoise="${denoise:-$(field denoise)}"
nrefs="$(field refs | grep -c . || true)"
neg="$(field negative)"
pal_file="$(field palette)"
# Fill: given, or the kind's for what has no original to keep the size of.
if [ -z "$fill" ] && { [ -z "$start" ] || [ -n "$sketch" ]; }; then fill="$(field fill)"; fi
[ -n "$fill" ] && [ "$fill" != 0 ] && post_opts+=(--fill "$fill")
[ -n "$pal_file" ] && post_opts+=(--palette "$pal_file")
# Tiles (floors, walls...): full colour, and a remaster keeps the original's outer px so they still join.
tile="$(field tile)"
[ -n "$keep_edge" ] || keep_edge="$(field edge)"
[ "$tile" = 1 ] && post_opts+=(--tile)
[ "$tile" = 1 ] && [ -n "$start" ] && [ "$keep_edge" != 0 ] && post_opts+=(--keep-edge "$keep_edge")

export PX_LORA="${PX_LORA:-$trigger.safetensors}"
case "$PX_LORA" in
*_xl.safetensors) PX_RES="${PX_RES:-1024}" PX_CKPT="${PX_CKPT:-sd_xl_base_1.0.safetensors}" ;;
esac
export PX_RES="${PX_RES:-512}"
[ -z "${PX_CKPT:-}" ] || export PX_CKPT

name="${name:-${kind:-$model}_$group}"
job="${out:-$PX_OUTPUT/${name}_$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$job/raw"
[ -n "$start" ] && cp "$start" "$job/orig.png"

# Green sprites: the background is green too, so only its pure colour goes.
if [[ " ${post_opts[*]-} " != *" --keep-green "* ]]; then
	has_green=0
	[[ "$prompt" == *"glowing green"* ]] && has_green=1
	if [ -n "$start" ] && python3 -W ignore - "$start" <<'EOF'; then has_green=1; fi
import colorsys, sys
from PIL import Image
px = [p for p in Image.open(sys.argv[1]).convert("RGBA").getdata() if p[3] > 127]
g = sum(1 for r, gg, b, _ in px
        if (lambda h, s, v: s > 0.25 and v > 0.25 and abs(h * 360 - 120) < 40)(*colorsys.rgb_to_hsv(r / 255, gg / 255, b / 255)))
sys.exit(0 if px and g / len(px) > 0.02 else 1)
EOF
	[ "$has_green" = 1 ] && post_opts+=(--keep-green)
fi

args=(--out "$job/raw" -n "$count" --negative "$neg")
if [ -n "$start" ]; then
	python3 -W ignore "$PX_TOOL" init "$start" "$job/init.png" --size "$size" --canvas "$PX_RES" ${mask:+--mask "$(resolve "$mask")"}
	args+=(--init "$job/init.png" --denoise "$denoise")
	[ -n "$mask" ] && args+=(--mask "$job/init_mask.png")
fi
[ -n "$seed" ] && args+=(--seed "$seed")

{
	echo "name=$name model=$model group=$group kind=${kind:-none} lora=$PX_LORA lora_id=${PX_LORA_ID:-none} res=$PX_RES mask=${mask:-none} init=${start:-none} denoise=${denoise:-n/a} size=$size refs=$nrefs palette=${pal_file:-none} fill=${fill:-0} tile=$tile edge=${keep_edge:-0}"
	echo "prompt: $prompt"
	echo "negative: $neg"
	echo "extra: $extra"
	printf 'pxcomfy.py %q' "$prompt"; printf ' %q' "${args[@]}"; echo
} | tee "$job/job.txt"
python3 "$HERE/pxcomfy.py" "$prompt" "${args[@]}"

i=0
for r in "$job/raw/"*.png; do
	i=$((i + 1))
	seed_tag="$(basename "$r" .png | grep -oE '_s[0-9]+_' | tail -1 | tr -d _)"
	[ -n "$seed_tag" ] || seed_tag="v$i"
	python3 -W ignore "$PX_TOOL" post "$r" "$job/${name}_${seed_tag}.png" --size "$size" --margin 0 --mirror "$mirror" \
		${start:+--orig "$job/orig.png" --grid "$job/init.png.json"} \
		${mask:+--keep-mask "$job/init_mask_small.png" --base "$job/init_base.png"} ${post_opts[@]+"${post_opts[@]}"}
done
touch "$job/.done"
echo "done: $job"
