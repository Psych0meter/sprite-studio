# Sprite Studio guide

How to get good sprites out of Sprite Studio: the ideas behind it, the
work flow from your sprites to a trained LoRA to new sprites, and every
page of the app. Installation is in [INSTALL.md](INSTALL.md).

## 1. The ideas in five minutes

**The base model.** Stable Diffusion turns a text *prompt* into a picture.
It knows "a spaceship", "pixel art", "seen from above", but it has never
seen *your* game. Two versions are supported:

| Base model | Renders at | Training on an 8 GB card | Suits |
|---|---|---|---|
| SD 1.5 | 512 x 512 | comfortable | sprites up to 64 px, icons |
| SDXL | 1024 x 1024 | tight, slow on older cards | sprites of 128 px and more |

**The LoRA.** A small add-on file (about 40 MB) trained on your images.
Loaded on the base model, it teaches it your style without retraining the
whole model. In Sprite Studio, one *model* = one LoRA.

**Captions and the trigger word.** Every training image comes with a
caption, built from the model:

```
pxunit, red faction, large unit, armoured hull, top-down view, facing up,
dark red metal hull, near-black shadows, bright glowing red lights,
pixel art, flat green background
```

The first word is the *trigger word*: a made-up word the LoRA ties to what
all your images share (the style). The other words describe what differs
(group, kind, colours), so you can ask for them later.

**Training.** The trainer shows the base model your images again and again
and nudges the LoRA towards them. One pass over the images is an *epoch*;
the LoRA is saved every two epochs, and you pick the epoch that looks best.

**Generating.** A prompt, and optionally a starting sprite. *Denoise* says
how far a remaster may go: 0.4 stays close, 0.7 is a loose variation, 1.0
ignores the starting image. The render is then cleaned into a sprite.

## 2. Work flow

1. **Models**: create a model, add its sprites, give it groups and kinds.
2. **Train**: build the training set, check it, train, pick an epoch, *Use* it.
3. **Generate** or **Batch**: remaster sprites, invent new ones.
4. **Palettes**: fix colours and light that come out wrong.
5. **Results**: compare, star the keepers, export them.
6. **LoRA files**: install, rename or delete the LoRAs you no longer need.

## 3. Models

A model lives in `~/lora/<id>/`: `model.json` (what it is), `sprites/`
(what it learns from), `dataset/` and `output/` (SD 1.5 training set and
runs; `dataset_xl/` and `output_xl/` for SDXL).

### 3.1 Projects and starting points

Models belong to a *project*, one per game. The list at the top of the menu
shows one project at a time; projects are created, renamed and deleted at
the bottom of the Models page (deleting one moves its models, it never
deletes them).

To start a model (Models page, *Add models*):

- **New model**: empty, a copy of another model's settings (optionally with
  only some of its groups), or one model of a template set.
- **Template sets**: ready-made models. *Generic starting points* holds
  top-down units in three sizes, side-view characters, item icons, props.
  More sets are JSON files in `~/lora/.templates/`.
- **Import a model file**: a model exported from another installation
  (a model's Settings tab, *Export the model*), optionally kept as a template.

Every model needs its own trigger word (its LoRA files are named after it);
the app picks a free one when it creates a model.

### 3.2 Sprites

On a model's **Sprites** tab:

- **Drop PNG sprites or a zip of them** (sub-folder names can become name prefixes:
  `icons/map/x.png` becomes `map_x.png`), choosing the group and kind they
  go into, or letting their names decide.
- **Import from a source folder**: the named folders of Settings (`input`,
  `game`, and any `name=path` you add), with a file pattern; the model's
  *import rules* (Settings tab) repeat it.
- **Select** sprites to set their group, kind or own words by hand, leave
  them out of training, or remove them (moved to `sprites/.trash/`).

### 3.3 Groups and kinds

**Groups** are the main axis: a faction, a family of items. Each has:

- *words*, put in every caption and prompt of the group ("red faction");
- *keywords*, an extra description ("organic insect hull, bone plating");
- a *file name pattern* (a regular expression, `_red_` for instance) that
  puts a sprite in the group unless you set it by hand;
- *own words from*: a pattern whose first bracketed part becomes the
  sprite's own words (the item's name from `^weapon_(.+)$`);
- an *on* switch (off: not learned, not offered) and an optional *palette*.

**Kinds** are the second, optional axis: a size class (small 32 px, large
128 px). Their *size* sets the pixel grid of training and of what is
generated; *fill* how much of the canvas a new sprite takes; *remaster
distance* the default denoise; *keywords* and *words to avoid* refine
the prompt per kind. A model without kinds uses each sprite's own size.

A model for one faction can put each kind's look of that faction in the
kinds' keywords, and the faction's shared look in the group's keywords.

**Caption order**: trigger, group words, kind words, the sprite's own words,
group keywords, kind keywords, the model's end words, the sprite's colours,
"pixel art, flat green background". The Sprites tab shows the caption of the
selected sprite.

### 3.3.1 Preview prompts

The **Preview prompts** tab of a model lists the test prompts that every
training run draws at each epoch. One row per prompt:

- the prompt itself, and *Words to avoid* (optional);
- a check that the prompt starts with the model's trigger word: without it
  the LoRA is not called, and the preview looks like plain Stable Diffusion.
  **Fix** puts the trigger first;
- the first 2 rows are marked: they are the ones rendered afterwards when a
  card cannot render previews during training (see Train);
- move up, move down and remove.

**Add** starts a row from a group and a kind (the prompt the model would
build for them) or from an empty one. With no list, the automatic prompts
are used; they are folded away, and **Copy them into the list** lets you
edit them. Choose prompts that differ (one per group, one per kind) rather
than variants of the same sprite.

### 3.4 One model or several?

One model for every group learns what they share (kinds, pixel style)
from all the sprites, but groups can blur into each other. A model per
group (New model, a copy keeping one group) learns that group more
faithfully, takes a training run of its own, and can copy its sprites more.
Start with one model and distinct group words; split the groups it still
mixes up.

## 4. Training

### 4.1 A good training set

- **One consistent style**: the LoRA learns what the images share.
- **Enough images**: about 20 per group or more; a group of 5 is too few.
- **Honest captions**: describe what changes from image to image, not the
  style itself (the trigger word absorbs it).
- **One framing**: every image on flat green (`#00ff00`), centred, on the
  same pixel grid. Pick another key colour for a model whose art is green.

### 4.2 The pixel grid

Stable Diffusion works on a picture 8 times smaller than the image, so its
smallest clean detail is about 8 render pixels. Each kind therefore gets a
whole number of render pixels per sprite pixel, `k = render size // size`:
at 512, a 64 px sprite gets 8, 128 px gets 4. The same `k`, on the same
centred grid, is used by the training images, the starting images of
remasters and the clean-up (one sprite pixel per `k x k` block, its most
common colour, the grid's phase matched to the render). SDXL renders at
1024, which doubles `k`: finer shading and cleaner edges on big sprites.

| Sprite size | Base model |
|---|---|
| 16 to 64 px | SD 1.5 |
| 96 to 128 px | SD 1.5 (accept doubled pixels) or SDXL |
| 192 px and more | SDXL, or hand finishing |

Sprites under about 24 px are too small for the LoRA to learn well.

### 4.3 Light

The LoRA learns the shading it sees. *Find sprites lit from below*
(Sprites tab) selects sprites brighter at the bottom. Settings → *Relight
for training* (50 to 70) relights those sprites' edges from the top in the
training set only; build the set again and retrain. For results already
made, use a palette's *Light from the top* slider (section 7).

### 4.4 The Train page

1. **Base model**: SD 1.5 or SDXL (downloaded on first use, about 7 GB).
2. **Training set**: built from the model; check the sheet and the captions
   (each caption can be edited). *Repeats* 0 means about 300 steps per
   epoch, whatever the number of sprites.
3. **Train**: epochs (12 for SD 1.5, 8 for SDXL), rank (32; 16 for SDXL),
   learning rate (1e-4), precision (*Automatic*: bf16 on RTX 30 and newer,
   fp16 on RTX 20, full precision on older cards). The app stops ComfyUI
   during the run on cards under 15 GB and starts it again at the end.
4. **Choose a checkpoint**: the preview grid shows the same prompts at every
   saved epoch. Take the first epoch where the style is clearly there and
   before the images turn noisy or copy the originals; click **Use** (it
   becomes the model's LoRA) or **Add** (installed under its own name, to
   compare or batch with). A checkpoint with no previews (SDXL on a card
   under 12 GB, a stopped run, a checkpoint added later) can be rendered
   afterwards: tick the epochs in the box above the grid and click **Render
   the missing previews**. The app renders the run's first 2 test prompts
   for each, with the run's own seeds, through ComfyUI (it must be running;
   on a GTX 1080 an SDXL image takes about 2 minutes). Each preview appears
   in the grid as it is done, a cancelled run keeps what is finished, and a
   preview already there is never rendered again. The render time of each
   image is shown under it (and next to the name of a variant in Results). On a card under 12 GB the
   Train step has a checkbox, **Render previews when training ends**, which
   queues the same job as soon as the run finishes. *All checkpoint files*
   lists every file with the same **Use** and **Add** buttons.

Underfit (previews look like generic Stable Diffusion): more epochs, or a
learning rate of 2e-4. Overfit (previews copy your sprites, colours burn):
an earlier epoch. 1500 to 4000 steps in total suit a style LoRA of about
100 images.

**SDXL on 8 GB**: only the UNet is trained, the text encoders' outputs are
cached, the weights are kept in half precision under 16 GB, Adafactor,
rank 16. It fits with almost nothing to spare; on cards without fast half
precision (GTX 10 and older) it is very slow (about 30 seconds a step on a
GTX 1080, so 2400 steps take most of a day), and SD 1.5 is the better
choice. Under 12 GB no preview images are rendered during training (the
render ran an 8 GB card out of memory); the checkpoints are still saved
every 2 epochs, and their previews are rendered afterwards through ComfyUI
(see *Choose a checkpoint*), or compared with Compare LoRAs.

## 5. Generating

### 5.1 The Generate page

Model, group, kind, then where to start:

- **The description**: a new design from text (and the group's and kind's
  words). Render more variants than for a remaster and keep the best.
- **A sprite**: a remaster; *denoise* (remaster distance) sets how far.
- **My sketch**: a rough silhouette in flat colours on green; the LoRA
  details it. The best way to control the shape of something new.

*More settings*: the LoRA and its strength, colours from another kind,
symmetry (mirror one half), green parts (keep a sprite's own greens), the
fill, the palette, the results' name.

The page shows which LoRA the model uses (and which epoch it is) and refuses
to start when none is installed.

### 5.2 Touch-ups and remasters of results

On any variant in Results:

- **Touch up** (brush): paint the pixels to redraw, say what to draw there,
  choose how much it changes. Only the painted pixels are redrawn.
- **Generate again from this one**: a remaster of the variant.
- **Clean up again** (sliders icon): the model's raw render is read again
  with other settings, next to the render so you can compare. Nothing is
  drawn again: it takes under a second and needs no GPU. The preview follows
  the settings live; **Keep it as a new variant** saves it next to the old
  one as `<name>_c1.png`, `_c2`... (the old one stays). Settings:
  - *Palette*: as generated, none (the render's own colours), or any saved
    palette. The palette is the biggest change from the raw render.
  - *Symmetry*: like the original, none, or one half mirrored onto the other.
  - *Colours at most* (2 to 64): fewer colours give a flatter, more graphic
    sprite.
  - *Edge* (20 to 80 %): how much of a pixel's block of the render must be
    sprite to draw that pixel. Lower is a fuller silhouette, higher a thinner
    one.
  - *Loose specks*: separate bits smaller than this share of the sprite are
    dropped.
  - *Background tolerance*: how close to the green a pixel must be to count
    as background. Higher removes more fringe but may eat green-tinted parts.
  - *Tidy lone pixels*, *Dark outline*, *Greens of its own*.

  Not available for touch-ups (redo them from Generate) or for variants
  whose raw render was deleted.
- **Recolour**: a palette on this variant, kept as a new variant.

### 5.3 Fill

*Fill (%)*: how much of the canvas a sprite's longest side takes. 100 is
edge to edge, 90 leaves about 3 px on each side of a 64 px sprite, 50 is the
least allowed. The drawing is cut out of the render and pixelated at that
size, so it is never cut by the canvas nor lost in empty space. It applies
to new sprites and sketches; a remaster keeps its original's size unless a
fill is given for that run. Keep one fill per kind so sprites of a kind read
at the same size; 90 to 95 % leaves room for outlines and effects drawn
around the sprite, 100 % suits icons.

### 5.4 Batches

**Batch** runs a whole model, or some groups and kinds, a few variants each,
with comparison sheets per kind:

- **Remaster every sprite**: each sprite from itself.
- **Invent new sprites**: N new designs for each group and kind, with an
  optional list of ideas (one per line) used in turn.

A batch with the same name continues where it stopped. *Preview the list*
shows what will run and how long it should take on your card.

### 5.5 Comparing LoRAs

**Compare LoRAs** renders the same test subjects (a new design and a
remaster per group) with each LoRA file and strength you tick, on fixed
seeds, one sheet per subject: to choose between epochs, or between SD 1.5
and SDXL.

### 5.6 Managing LoRA files

**LoRA files** lists every LoRA in ComfyUI's `models/loras` folder as a
table: name, the model it belongs to, base (SD 1.5 or SDXL), epoch, date,
size, and *Results*, the number of result folders rendered with it. A tag
shows the file each model generates with (*in use, SD 1.5* or *SDXL*).

- **Use** installs a file as the one its model generates with (same as
  *Use* on the Train page, for files you added earlier). The SDXL file is
  kept apart from the SD 1.5 one.
- **Rename** a file. The one in use keeps its name, because the name is how
  the app finds it; a name that belongs to a model is refused.
- **Delete** one file, or tick several and delete them together. The
  trained checkpoints of the Train page are never touched, so a deleted
  LoRA can be installed again. Deleting the one in use leaves the model
  without a LoRA until you install another.
- **Compare** (with files ticked) opens Compare LoRAs with them selected.
- Filter by model, find a name, sort by date, name, model or size. The top
  right shows the total size and the free disk space.

A file copied into the folder by hand shows its model from its name and its
epoch only when the name says it (`_e6`); otherwise the epoch reads `?`.

## 6. Results

Every run, batch and comparison of the output folder. Zoom (Fit, 1x to 8x),
backdrop (checker, dark, floor, light), raw renders; star the variant to
keep (*Export picks* copies them to `output/picked/`, named after the sprite
they replace), compare side by side. **Delete** a result, one sprite of a
batch, its failed sprites, or every empty result: they go to the output
folder's `.bin`, where *Undo last delete* puts them back until you *Empty
bin*.

## 7. Palettes

A LoRA reproduces the colours of its training sprites: a dark group comes
out dark. A palette fixes colours after rendering, without retraining.

**Making one** (Palettes page, *New palette*):

- **Automatic tuning**: no colours; a tuning computed so a group's mean
  light, contrast and saturation match other sprites (the rest of the
  model, or another model for a one-group model). It can become the group's
  palette at once.
- **A model's colours** / **a batch's colours**: the colours of a group, a
  kind or a batch's results, merged down to 2 to 64 colours, each weighted
  by how much of the art it covers.
- **Import a file**: `.hex` (Lospec, Aseprite), `.gpl` (GIMP, Krita), a
  palette `.json`, or an image.

**How it is applied**:

- *Tuning only*: the render keeps its colours, adjusted by the tuning.
- *Snap to the colours*: every pixel takes the nearest palette colour.
- *Match light, then snap*: the render's light and shade are first spread
  like the palette's, then snapped. Use it to get the same colours on every
  sprite of a batch, even from renders much darker or lighter than the
  palette.

**Tuning**: brightness (a gamma: lifts the darks, keeps the lights),
contrast, saturation, hue, and *light from the top* (top edges of the
silhouette brightened, bottom edges darkened). *Auto-tune* sets the sliders
from measured sprites; *Take colours from* fills the palette from sprites or
a batch. The preview is computed exactly as generation will do it.

**Where it applies**: a group's own palette (Models → Groups and kinds, or
*Use it for the group*), one run (Generate, Batch), or one existing sprite
(Recolour in Results). Exports: `.hex`, `.gpl`, `.png`, `.json`.

## 8. Graphics cards

| What | How it adapts |
|---|---|
| PyTorch build | CUDA 12.8 for RTX 50 cards, 12.6 for the others (GTX 10 included); `install.sh` stops if the build has no kernels for the card |
| Training precision | Automatic: bf16 on RTX 30 and newer, fp16 on RTX 20, full precision on older cards; SDXL always in half precision |
| ComfyUI during training | stopped on cards under 15 GB (Settings: auto, always, never) |
| Time estimates | measured from your own finished jobs and training runs |

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "No LoRA installed" on Generate or Batch | the model has no chosen epoch | Train page: *Use* an epoch |
| every sprite of a batch fails at once | ComfyUI stopped, or LoRA missing | the job page names the cause |
| `CUDA out of memory` while training | something else holds the GPU, or the card is too small (SDXL) | `nvidia-smi`; SD 1.5 on small cards |
| `torch.cuda.is_available()` is False | device files not passed, or PyTorch without the card's architecture | INSTALL.md section 3, `TORCH_INDEX` / `TORCH_VERSION` |
| previews look like generic Stable Diffusion | underfit | more epochs, or learning rate 2e-4 |
| previews copy the originals | overfit | an earlier epoch, a lower LoRA strength |
| textured background instead of flat green | LoRA missing or too weak | strength 1.0, check the LoRA name |
| results darker than the art | the LoRA's average colours | a palette (section 7) |
| highlights at the bottom | sprites painted lit from below | *Light from the top*, *Relight for training* |
| chunky pixels on big sprites | SD 1.5's 8 px limit | SDXL, or hand finishing |
| page shows an old layout after an update | browser cache | Ctrl+Shift+R |

## 10. Glossary

| Term | Meaning |
|---|---|
| base model | the Stable Diffusion file a LoRA works on (SD 1.5, SDXL) |
| LoRA | a small add-on trained on your images |
| checkpoint (LoRA) | the LoRA saved at one epoch |
| trigger word | a made-up word the LoRA ties to your style |
| caption | the text that goes with a training image |
| epoch / repeats / steps | one pass over the set / times each image is shown per pass / images seen in total |
| rank (dim) | the LoRA's capacity |
| denoise | how much a starting image may change (0 to 1) |
| seed | the random number that makes a result reproducible |
| latent | the 8x smaller picture Stable Diffusion works on |
| bf16 / fp16 / fp32 | half and full precision numbers |
