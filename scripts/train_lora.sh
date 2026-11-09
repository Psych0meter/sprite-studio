#!/usr/bin/env bash
# train_lora.sh: train the style LoRA of one sprite model (pxmodel.py) with
# kohya sd-scripts. It fits 8 GB NVIDIA cards and adapts to the card: the
# precision is chosen from the GPU's generation (PRECISION=auto).
#
#   train_lora.sh setup      once: sd-scripts + its own Python env + the SD 1.5 model
#   train_lora.sh dataset    the training set of $CATEGORY's model (pxmodel.py dataset)
#   train_lora.sh train      the training itself (about 1 to 2 hours with SD 1.5)
#   train_lora.sh install [CHECKPOINT]
#                            copy the chosen checkpoint into ComfyUI's
#                            models/loras as <trigger>.safetensors
#
#   CATEGORY=items ./train_lora.sh dataset     (and the same for train, install)
#
# A model (made in the web app, or from a template set with
# "pxmodel.py init-defaults") lives in $PX_LORA_HOME/<model>/: model.json, sprites/, dataset/,
# output/ (dataset_xl/, output_xl/ for SDXL). Its sprites come from the web
# app (upload, import) or "pxmodel.py import <model>"; the training set is
# made from them on the model's groups and kinds, and the preview prompts
# from its groups.
#
# Environment (defaults in brackets):
#   CATEGORY  the model id                            (required)
#   MODEL     sd15 (512 px, about 1 h 30) or sdxl (1024 px: twice the
#             detail per sprite pixel, several hours, LoRA named <trigger>_xl;
#             run "MODEL=sdxl train_lora.sh setup" once to fetch SDXL)  [sd15]
#   PX_INPUT  [$SG_DATA/input]    PX_GAME  [$SG_DATA/game]   (imports; SG_DATA [~/sprites])
#   PX_LORA_HOME  where everything lives     [~/lora]
#   COMFY     ComfyUI folder                 [~/ComfyUI]
#   EPOCHS (12; SDXL 8), REPEATS (the model's; 0: about 300 steps per epoch), DIM (32), LR (1e-4), PRECISION (auto: bf16
#   on RTX 30 and newer, fp16 on RTX 20 / Volta, full precision on older cards,
#   which are slow in half precision; or no, fp16, bf16. SDXL always trains in
#   half precision to fit in 8 GB: bf16 where the card has it, else fp16)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATEGORY="${CATEGORY:-}"
PX_INPUT="${PX_INPUT:-${SG_DATA:-$HOME/sprites}/input}"
PX_GAME="${PX_GAME:-${SG_DATA:-$HOME/sprites}/game}"
HOME_DIR="${PX_LORA_HOME:-$HOME/lora}"
COMFY="${COMFY:-$HOME/ComfyUI}"
SD="$HOME_DIR/sd-scripts"
VENV="$HOME_DIR/venv"
MODEL="${MODEL:-sd15}"
case "$MODEL" in
sd15) BASE="$COMFY/models/checkpoints/v1-5-pruned-emaonly.safetensors" RES=512 SUFFIX="" ;;
sdxl) BASE="$COMFY/models/checkpoints/sd_xl_base_1.0.safetensors" RES=1024 SUFFIX="_xl" ;;
*) echo "unknown MODEL: $MODEL (sd15, sdxl)" >&2; exit 2 ;;
esac
export PX_LORA_HOME="$HOME_DIR" PX_INPUT PX_GAME
model_py() { # pxmodel.py with the training environment's Python (Pillow, numpy)
	local py="$VENV/bin/python"
	[ -x "$py" ] || py=python3
	"$py" -W ignore "$HERE/pxmodel.py" "$@"
}
TRIGGER=""
if [ "${1:-}" != setup ]; then
	[ -n "$CATEGORY" ] || { echo "set CATEGORY to a model id (pxmodel.py list)" >&2; exit 2; }
	TRIGGER="$(model_py get "$CATEGORY" trigger)" || { echo "unknown model: $CATEGORY (pxmodel.py list)" >&2; exit 2; }
fi
NAME="$TRIGGER$SUFFIX"          # pxunit, or pxunit_xl for SDXL
DATA="$HOME_DIR/$CATEGORY/dataset$SUFFIX"
OUT="$HOME_DIR/$CATEGORY/output$SUFFIX"
if [ "$MODEL" = sdxl ]; then DEF_EPOCHS=8 DEF_DIM=16; else DEF_EPOCHS=12 DEF_DIM=32; fi
EPOCHS="${EPOCHS:-$DEF_EPOCHS}" REPEATS="${REPEATS:-0}" DIM="${DIM:-$DEF_DIM}" LR="${LR:-1e-4}"
PRECISION="${PRECISION:-auto}"

# The GPU's compute capability (6.1 GTX 10, 7.5 RTX 20, 8.6 RTX 30, 8.9 RTX 40,
# 12.0 RTX 50...), read by the training environment's PyTorch.
gpu_capability() {
	"$VENV/bin/python" -c 'import torch; c = torch.cuda.get_device_capability(0); print(c[0] * 10 + c[1])' 2>/dev/null || echo 0
}
pick_precision() { # pick_precision sd15|sdxl -> no / fp16 / bf16
	local cap
	cap="$(gpu_capability)"
	if [ "$PRECISION" != auto ]; then
		echo "$PRECISION"
	elif [ "$cap" -ge 80 ]; then
		echo bf16
	elif [ "$cap" -ge 70 ] || [ "$1" = sdxl ]; then
		echo fp16
	else
		echo no
	fi
}

setup() {
	mkdir -p "$HOME_DIR"
	[ -d "$SD" ] || git clone https://github.com/kohya-ss/sd-scripts.git "$SD"
	# The same torch build as ComfyUI, which already runs on this GPU.
	local torch_ver index
	torch_ver="$("$COMFY/.venv/bin/python" -c 'import torch; print(torch.__version__)')"
	case "$torch_ver" in
	*+cu*) index="https://download.pytorch.org/whl/${torch_ver##*+}" ;;
	*) index="" ;;
	esac
	echo "ComfyUI uses torch $torch_ver"
	[ -d "$VENV" ] || python3 -m venv "$VENV"
	"$VENV/bin/pip" install --upgrade pip
	if [ -n "$index" ]; then
		"$VENV/bin/pip" install "torch==${torch_ver%%+*}" torchvision --index-url "$index"
	else
		"$VENV/bin/pip" install "torch==${torch_ver}" torchvision
	fi
	(cd "$SD" && "$VENV/bin/pip" install -r requirements.txt)
	"$VENV/bin/pip" install numpy pillow scipy
	if [ "$MODEL" = sdxl ] && [ ! -f "$BASE" ]; then
		echo "downloading the SDXL base model (about 7 GB) into $BASE"
		curl -L --fail -o "$BASE" \
			https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors
	fi
	local sd15="$COMFY/models/checkpoints/v1-5-pruned-emaonly.safetensors"
	if [ ! -f "$sd15" ]; then
		echo "downloading the SD 1.5 base model (about 4 GB) into $sd15"
		curl -L --fail -o "$sd15" \
			https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors
	fi
	"$VENV/bin/python" -c 'import torch; print("CUDA:", torch.cuda.is_available(), torch.cuda.get_device_name(0))'
}

dataset() {
	rm -rf "$DATA"
	model_py dataset "$CATEGORY" "$DATA" --repeats "$REPEATS" --res "$RES"
	echo "check $DATA/sheet.png and $DATA/captions.csv before training"
}

# Preview prompts: the model's own, or one per group (pxmodel.py previews),
# rendered every 2 epochs to pick the best checkpoint.
sample_prompts() {
	model_py previews "$CATEGORY" --res "$RES"
}

# A new training never overwrites the checkpoints of the last one: they (and their previews) move to
# $OUT/previous/<unix time>/ first.
archive_previous_run() {
	local old=()
	shopt -s nullglob
	old=("$OUT"/*.safetensors)
	shopt -u nullglob
	if [ ${#old[@]} -eq 0 ] && [ ! -d "$OUT/sample" ]; then return 0; fi
	local dest
	dest="$OUT/previous/$(date +%s)"
	mkdir -p "$dest"
	if [ ${#old[@]} -gt 0 ]; then mv "${old[@]}" "$dest/"; fi
	if [ -d "$OUT/sample" ]; then mv "$OUT/sample" "$dest/sample"; fi
	echo "the previous run's checkpoints were kept in $dest"
}

train() {
	[ -d "$DATA/img" ] || { echo "no training set in $DATA: run CATEGORY=$CATEGORY $0 dataset first" >&2; exit 2; }
	[ -f "$BASE" ] || { echo "error: the base model $BASE is missing: run MODEL=$MODEL $0 setup first (it downloads it)" >&2; exit 2; }
	mkdir -p "$OUT"
	archive_previous_run
	sample_prompts >"$OUT/sample_prompts.txt"
	cd "$SD"
	local prec
	prec="$(pick_precision "$MODEL")"
	[ "$MODEL" = sdxl ] && [ "$prec" = no ] && prec=fp16
	echo "GPU compute capability $(gpu_capability), precision: $prec"
	local common=(
		--pretrained_model_name_or_path "$BASE"
		--train_data_dir "$DATA/img"
		--output_dir "$OUT" --output_name "$NAME"
		--resolution "$RES" --caption_extension .txt --max_token_length 225
		--network_module networks.lora --network_dim "$DIM" --network_alpha $((DIM / 2))
		--lr_scheduler cosine --lr_warmup_steps 100
		--max_train_epochs "$EPOCHS" --save_every_n_epochs 2
		--train_batch_size 1 --save_precision fp16
		--gradient_checkpointing --cache_latents --sdpa
		--noise_offset 0.05 --flip_aug --seed 42
		--max_data_loader_n_workers 2 --persistent_data_loader_workers
		--logging_dir "$OUT/logs"
	)
	local samples=(--sample_prompts "$OUT/sample_prompts.txt" --sample_every_n_epochs 2 --sample_sampler euler_a)
	if [ "$MODEL" = sdxl ]; then
		# SDXL: only the UNet is trained, the text encoders' outputs are
		# computed once, Adafactor (small optimiser state). Mixed precision
		# alone keeps the UNet's weights in fp32 (about 10 GB): below 16 GB the
		# weights themselves are kept in half precision (--full_fp16 /
		# --full_bf16, about 5 GB), which fits an 8 GB card at 1024 px.
		local vram full=()
		vram="$("$VENV/bin/python" -c 'import torch; print(torch.cuda.get_device_properties(0).total_memory // 2**20)' 2>/dev/null || echo 0)"
		if [ "$vram" -lt 16000 ]; then
			[ "$prec" = bf16 ] && full=(--full_bf16) || full=(--full_fp16)
		fi
		echo "GPU memory ${vram} MB${full:+, weights kept in half precision (${full[*]})}"
		# The preview renders decode a 1024 px image with the VAE in full
		# precision (--no_half_vae), which needs more than 1 GB on top of the
		# training state: it ran an 8 GB card out of memory after hours of
		# training. Below 12 GB the checkpoints are saved without previews
		# (try them with Compare LoRAs, which renders them in ComfyUI).
		if [ "$vram" -ge 12000 ]; then
			common+=("${samples[@]}")
		else
			echo "No preview renders during training on this card (not enough memory): compare the checkpoints with Compare LoRAs"
		fi
		export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
		"$VENV/bin/accelerate" launch --num_cpu_threads_per_process 2 sdxl_train_network.py "${common[@]}" \
			--network_train_unet_only --cache_text_encoder_outputs --no_half_vae \
			--mixed_precision "$prec" --learning_rate "$LR" --unet_lr "$LR" \
			--optimizer_type Adafactor --optimizer_args scale_parameter=False relative_step=False warmup_init=False \
			"${full[@]}" || {
			rc=$?
			echo "error: SDXL training stopped (exit $rc). If the log above says CUDA out of memory, this card is too small for SDXL at $RES px: keep SD 1.5 on it, or close whatever else uses the GPU (nvidia-smi) and try again." >&2
			exit "$rc"
		}
	else
		common+=("${samples[@]}")
		"$VENV/bin/accelerate" launch --num_cpu_threads_per_process 2 train_network.py "${common[@]}" \
			--mixed_precision "$prec" --learning_rate "$LR" --unet_lr "$LR" --text_encoder_lr 5e-5 \
			--optimizer_type AdamW
	fi
	echo "checkpoints: $OUT/$NAME-0000NN.safetensors (every 2 epochs) and $OUT/$NAME.safetensors (last)"
	if [ -d "$OUT/sample" ]; then echo "previews:    $OUT/sample/"; fi
}

install() {
	local f="${1:-$OUT/$NAME.safetensors}"
	local dst="$COMFY/models/loras/$NAME.safetensors"
	mkdir -p "$COMFY/models/loras"
	if [ -e "$dst" ]; then
		# never overwritten: the file that was there is kept as <name>_<unix time>.safetensors
		local kept stamp
		stamp="$(date -r "$dst" +%s)"
		kept="$COMFY/models/loras/${NAME}_${stamp}.safetensors"
		mv "$dst" "$kept"
		echo "the LoRA that was installed was kept as $kept"
	fi
	cp "$f" "$dst"
	echo "installed $f as $dst"
}

case "${1:-}" in
setup) setup ;;
dataset) dataset ;;
train) train ;;
install) install "${2:-}" ;;
*) sed -n '2,30p' "$0"; exit 2 ;;
esac
