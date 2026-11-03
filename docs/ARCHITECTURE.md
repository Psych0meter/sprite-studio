# Architecture

How Sprite Studio is built: the pieces, where data lives, the file formats,
and the main algorithms. For contributors; users want [GUIDE.md](GUIDE.md).

## 1. Principles

- **The scripts are the single source of truth.** Everything the web app
  does, it does by running the command-line tools in `scripts/` (or by
  calling `pxmodel.py` / `pxtool.py` functions directly). The app builds
  command lines, runs them as jobs and reads what they write. A result made
  from the app and one made from a shell are the same.
- **Plain files, no database.** Models, palettes, results and the app's
  state are JSON and PNG files on disk, readable and editable by hand.
- **No build step.** Python 3.11+, Bash, and one hand-written JavaScript
  file served as is. No front-end framework, no bundler, no CDN.
- **Built-in ComfyUI nodes only.** Generation uses ComfyUI's API with core
  nodes (checkpoint and LoRA loaders, text encoders, KSampler, VAE, image
  and mask loaders), so any ComfyUI install works without custom nodes.
- **Hardware is detected, not assumed.** PyTorch build, precision, memory
  savers and time estimates come from the card in use.

## 2. Components

```
webapp/static/   index.html, app.css, app.js       single-page app (hash routing)
                 palette.js                        palette arithmetic (mirrors pxtool.py)
webapp/app.py    FastAPI: REST API, job queue, file serving, settings
scripts/
  pxmodel.py     models: normalise/load/save, sprite classification, captions,
                 training sets, preview prompts, generation prompts, batch and
                 invent lists, template sets
  pxtool.py      images: pixel grid, init images, render clean-up (post),
                 colour words, palettes (tune, relight, snap, match, extract,
                 auto-tune, export), sheets
  pxcomfy.py     one ComfyUI job: workflow JSON, uploads, polling, download
  pxmake.sh      one generation: prompt from pxmodel, init image, pxcomfy,
                 post per render, job.txt
  pxbatch.sh     remaster or invent over a model, resumable, sheets
  pxmatrix.sh    LoRA comparison: subjects x LoRA files x strengths
  train_lora.sh  setup (sd-scripts, base models), dataset, train, install
  pxpreviews.sh  a training run's missing preview images, rendered through ComfyUI
  install.sh     system install: user, ComfyUI, sd-scripts, services
```

## 3. Data on disk

| Path | What |
|---|---|
| `$PX_LORA_HOME/<model>/model.json` | a model (format below) |
| `$PX_LORA_HOME/<model>/sprites/` | its sprites; `.trash/` holds removed ones |
| `$PX_LORA_HOME/<model>/dataset[_xl]/` | training set: `img/<repeats>_<trigger>/` images and captions, `sheet.png`, `captions.csv` |
| `$PX_LORA_HOME/<model>/output[_xl]/` | training runs: `<trigger>-0000NN.safetensors`, `sample/`, `logs/`, `sample_prompts.txt` |
| `$PX_LORA_HOME/.palettes/<id>.json` | palettes |
| `$PX_LORA_HOME/.templates/*.json` | extra template sets |
| `$PX_LORA_HOME/.deleted/` | deleted models |
| `$COMFY/models/loras/<trigger>[_xl].safetensors` | the LoRA generation uses; extra ones `<trigger>_<name>.safetensors` |
| `$PX_OUTPUT/<name>_<date>/` | one generation: `job.txt`, `raw/`, `orig.png`, `init*.png`, variants `<name>_s<seed>.png` and `_x4` previews |
| `$PX_OUTPUT/batch-<tag>/<kind>/<sprite>/` | one sprite of a batch (same layout); `sheet_<kind>.png`, `batch.log` |
| `$PX_OUTPUT/matrix-<tag>/` | a LoRA comparison |
| `$PX_OUTPUT/.bin/` | deleted results (each with `.origin`) |
| `$SG_WEB_STATE/` | `settings.json`, `jobs.json`, `logs/`, `picks.json`, `installed.json` (which checkpoint each installed LoRA is), `projects.json` (projects with no model yet) |

## 4. Formats

**model.json**: documented at the top of `scripts/pxmodel.py`. `normalise()`
fills every default, so older files load unchanged; a renamed or retyped
field is migrated there (for example `colour_words: "ship"` became
`"hard"`).

**Palette** (`.palettes/<id>.json`): `id`, `label`, `colours` (`#rrggbb`),
`weights` (pixels each colour covered, optional), `tune` (`hue`,
`saturation`, `brightness`, `contrast`, `light`), `mode` (`tune`, `snap`,
`match`), `words` (name the colours in the prompt), `source`.

**job.txt**: first line `key=value` pairs (`name model group kind lora res
mask init denoise size refs palette fill`), then `prompt:`, `negative:`,
`extra:` lines and the exact `pxcomfy.py` command. Results pages read it.

## 5. Generation pipeline

1. `pxmodel.py prompt` builds the prompt (trigger, group and kind words and
   keywords, extra words, colour words from reference sprites or the
   palette), the negative prompt, size, denoise, palette and fill.
2. With a starting sprite, `pxtool.py init` scales it by the whole factor
   `k = canvas // size` onto flat green at the canvas size and writes the
   grid (`init.png.json`); a touch-up also writes the mask.
3. `pxcomfy.py` checks the LoRA and base model exist in ComfyUI, uploads the
   images and runs the workflow (txt2img, img2img, or masked img2img).
4. `pxtool.py post` per render: background removed by flood fill from the
   border (key colour and its shades), loose specks dropped, holes filled,
   palette of at most 64 colours from the render, one sprite pixel per
   `k x k` block (majority vote, grid phase searched), or a fit to the
   requested *fill*; lone pixels tidied, dark outline, mirror; then the
   palette (relight, tune, match, snap) and the touch-up merge. The
   clean-up's settings (`--max-colours`, `--edge`, `--min-part`, `--key-tol`,
   `--no-outline`, `--no-tidy`, `--mirror`, `--palette`, `--fill`) are
   arguments of `pxtool.post`, whose defaults are what `pxmake.sh` uses.
5. *Clean up again* (`POST /api/results/cleanup`) re-runs step 4 on a
   variant's saved render (`raw/*_s<seed>_*.png`) with the job's own
   size, grid (`init.png.json`), original and palette, and the settings sent
   by the page. Without `save` it returns the PNG as a data URL and writes
   nothing (the page asks for it live, so `_snap` looks each distinct colour
   up once); with `save` the result is `<name>_c<n>.png` next to the
   variant. Touch-ups and variants without a raw render are refused.

The JavaScript preview of palettes (`webapp/static/palette.js`) repeats
`pxtool`'s arithmetic, rounding included, so the preview matches the output
pixel for pixel; change both together (`tests/test_palette_js.py` checks
it).

## 6. Training pipeline

`train_lora.sh dataset` calls `pxmodel.py dataset`: every trained sprite
framed on green at the kind's pixel grid (relit when the model asks and the
sprite reads as lit from below), its caption next to it, `repeats` from the
model or about 300 steps per epoch. `train` runs sd-scripts'
`train_network.py` (SD 1.5) or `sdxl_train_network.py` (SDXL) with the
precision picked from the card and the memory savers; preview prompts come
from `pxmodel.py previews`.

**Previews afterwards.** Training renders them itself (every 2 epochs)
except on SDXL under 12 GB. `pxmodel.py preview-plan` lists what a run is
missing: for each checkpoint and each of the first N prompts of
`<output>/sample_prompts.txt` (else the model's), the cell with no
`sample/<name>_e<epoch>_<prompt>_*.png` yet; the unnumbered final file counts
as the run's last epoch (`FINAL_EPOCH`: the epochs of the last training
job, else the base model's default) unless a numbered checkpoint already is
that epoch. `pxpreviews.sh` copies each checkpoint into ComfyUI's loras
folder as `px_preview_*.safetensors` for the run, renders each cell with
`pxcomfy.py` (the prompt's own seed, steps and cfg, one image), and writes
it under the name the Train page's grid reads, with its background made
the pure key green (`pxtool.py flatten-background`) and its render time next
to it (`<name>.json`, written by `pxcomfy.py` beside every image as
`{"seconds", "seed"}` and read by `render_seconds`). Exit codes follow the batch
convention (0 ok, 4 some failed, 1 none made). In the app this is the
`previews` job kind (`POST /api/train/<model>/previews`, GPU lane, waits for
ComfyUI like generation); a training request with `previews_after` queues
it when the run finishes (`follow_up`). `GET /api/train/<model>` reports
`previews_todo`, `previews_by_epoch` and `final_epoch`.

## 7. Web app

- **Jobs**: a queue with two lanes. The GPU lane runs one job at a time
  (generation, batches, comparisons, training); the CPU lane runs at once
  (training sets, setup). Jobs are processes in their own group (cancel
  kills the group), logged to `logs/<id>.log`, persisted in `jobs.json`.
  Training stops ComfyUI first (sudo rule for `systemctl`) when the card
  is small, and starts it again.
- **Progress** is parsed from the logs (training steps and epochs, batch
  sprites, matrix cells, generated images); the first error line becomes
  the job's summary. Exit code 4 from a batch or matrix means some items
  failed (shown as finished with a warning), 1 that nothing was made.
- **Paths**: every path the browser sends is checked to be under a known
  folder (`safe_path`); writes go only to the output and model folders.
- **Front end**: `app.js` and `palette.js`, a small `h()` element helper, one route
  function per page, in-app dialogs (`askText`, `askConfirm`); no global
  build or framework.

## 8. Tests

`tests/` runs without a GPU or ComfyUI: `pxtool` image functions on
synthetic sprites, `pxmodel` on temporary models, the API through FastAPI's
test client on temporary folders, and `palette.js` against `pxtool` in
Node. Add a test with every bug fix
and every new rule.
