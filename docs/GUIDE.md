# Sprite Studio guide

How to get good sprites out of Sprite Studio: the ideas behind it, the
work flow from your sprites to a trained LoRA to new sprites, and every
page of the app. Installation is in [INSTALL.md](INSTALL.md).

## 1. The ideas in five minutes

**The base model.** Stable Diffusion turns a text *prompt* into a picture.
It knows "a robot", "pixel art", "seen from above", but it has never
seen *your* sprites. Two versions are supported:

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
pxunit, red team, large unit, armoured body, top-down view, facing up,
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

**Renaming a model** (*Settings → Rename this model*) changes its id and moves
the folder. Everything points at the model by a hidden id, so its owner, shares,
LoRA records, results and gallery images follow. It is refused while a job for the
model is queued or running. A model made from a template stops matching it, so
*Use the template's settings* is no longer offered.

### 3.1 Projects and starting points

Models belong to a *project*, one per project. The list at the top of the menu
shows one project at a time; projects are created, renamed and deleted at
the bottom of the Models page (deleting one moves its models, it never
deletes them).

To start a model (Models page, *Add models*):

- **New model**: empty, a copy of another model's settings (optionally with
  only some of its groups), or one model of a template set.
- **Template sets**: ready-made models. *Generic starting points* holds
  top-down units in three sizes, side-view characters, item icons, props, tiles (a Tile kind with the edge kept).
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
  **Remove duplicates** finds sprites that are the same image under different names
  (a result added twice, say), lists them, and keeps one of each: the one you set by
  hand, else the shortest name.

### 3.3 Groups and kinds

**Groups** are the main axis: a team, a family of items. Each has:

- *words*, put in every caption and prompt of the group ("red team");
- *keywords*, an extra description ("organic insect body, bone plating");
- a *file name pattern* (a regular expression, `_red_` for instance) that
  puts a sprite in the group unless you set it by hand. It is searched in
  the file name without `.png`, anywhere in it unless you start with `^`;
  the first group (or kind) in the list that matches wins; `.` matches
  everything; empty matches nothing (only sprites you sort by hand);
- *own words from*: a pattern whose first bracketed part becomes the
  sprite's own words (the item's name from `^weapon_(.+)$`). It needs a
  bracketed part: `.*` alone gives no words, so it stays empty until you
  want own words;
- an *on* switch (off: not learned, not offered) and an optional *palette*.

**Kinds** are the second, optional axis: a size class (small 32 px, large
128 px). Their *size* sets the pixel grid of training and of what is
generated; *fill* how much of the canvas a new sprite takes; *remaster
distance* the default denoise; *keywords* and *words to avoid* refine
the prompt per kind; *Tile* and *Keep edge* are for tiles, see 5.7. A model without kinds uses each sprite's own size.

A model for one team can put each kind's look of that team in the
kinds' keywords, and the team's shared look in the group's keywords.

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
  **Fix** puts the trigger first, and **Auto-fix** at the top does it for every
  prompt that needs it, after showing each old and new start;
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

### 4.1.1 What good pixel art looks like

The app follows the rules of thumb of Derek Yu's pixel-art tutorial where a
tool can:

- **A one-pixel outline, no stray pixels.** The clean-up draws a one-pixel
  outline and *tidies* lone pixels; edges are hard (no anti-aliasing on the
  outside, so the sprite sits on any background without a halo).
- **Selective outlining.** *Outline style: Selective* paints each outline
  pixel in a darker shade of its own colour instead of the palette's darkest
  colour: softer, and the sprite reads as one form. Dark is better when the
  sprite must stand out from a busy background.
- **Few colours, reused.** Fewer, well-chosen colours read better than many
  close ones. The palette editor warns about near-twin colours (they look
  blurry and waste the palette) and merges them in one click.
- **No gradients, no pillow shading.** The negative prompt rejects gradients,
  pillow shading (shading inward from the outline), anti-aliased edges and
  jagged lines; shading comes from the training sprites.
- **One light, from the top.** See 4.3 (*relight*) and the palette's
  *Light from the top*.
- **Keep sprites small.** A sprite rarely needs more than about 200 px; the
  Models page says so for bigger kinds.

By hand (the tool cannot do these for you): keep curves even (steps of 6, 3,
2, 1 rather than 3, 1, 3), shade the parts farthest from the light first and
add highlights last, and keep to about two darker and two lighter shades of
each base colour.

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
  - *Colours at most*: 2 to 256 (64 by default).
  - *Tidy lone pixels*, *Outline* and its *style* (dark, or selective: see
    4.1.1), *Greens of its own*.

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

A batch with the same name continues where it stopped. Left empty, the name
is the model, the LoRA and the date (`units-pxunit_e6-20261106`), so batches
of other models or LoRAs never share a folder. *Preview the list*
shows what will run and how long it should take on your card.

### 5.5 Comparing LoRAs

**Compare LoRAs** renders the same test subjects (a new design and a
remaster per group) with each LoRA file and strength you tick, on fixed
seeds, one sheet per subject: to choose between epochs, or between SD 1.5
and SDXL.

### 5.6 Managing LoRA files

**LoRA files** lists every LoRA in ComfyUI's `models/loras` folder as a
table: name, the model it belongs to, base (SD 1.5 or SDXL), epoch, creation date (kept when a file is renamed or copied),
size, and *Results*, the number of result folders rendered with it. A tag
shows the file each model generates with (*in use, SD 1.5* or *SDXL*).

- **Use** installs a file as the one its model generates with (same as
  *Use* on the Train page, for files you added earlier). The SDXL file is
  kept apart from the SD 1.5 one.
- **Rename** a file. The one a model generates with keeps its name (the model
  looks for it by that name); a name that belongs to a model is refused.
- **Nothing is overwritten.** Installing a LoRA over a name that exists
  (*Use*, or *Install* on the Train page) first renames the old file to
  `<name>_<time>.safetensors` (the time is a unix time), so a result or a
  gallery image made with it can still be explained, and you can *Use* the old
  file again. The same goes for training: a new run moves the checkpoints and
  previews of the last one to `previous/<time>/` in the model's output folder.
- A LoRA that **results or gallery images still use cannot be deleted** (the
  button is off, with the reason in its tooltip). Delete those results (and the
  gallery images made with it) first. **Renaming is always fine**: results and
  gallery images follow the file by an id the app keeps for it, not by its name,
  and show the new name.
- **Delete** one file, or tick several and delete them together. The
  trained checkpoints of the Train page are never touched, so a deleted
  LoRA can be installed again. Deleting the one in use leaves the model
  without a LoRA until you install another.
- **Compare** (with files ticked) opens Compare LoRAs with them selected.
- Filter by model, find a name, sort by clicking a column header (again to
  reverse) or with the Sort list. The top
  right shows the total size and the free disk space.

A file copied into the folder by hand shows its model from its name and its
epoch only when the name says it (`_e6`); otherwise the epoch reads `?`.

### 5.7 Tiles: floors, walls, anything that must join

Sprites are pixel art with a background to remove and 64 colours by default
(up to 256).
A **tile** is the opposite: opaque art in full colour (a floor, a wall) that
must meet its neighbours. Tick **Tile** on its kind (Models › Groups and
kinds). Results of a tile kind are the render's tile cut out and scaled to
the kind's size, with no background removal, no colour limit and no outline.

**Keep edge (px)** makes a remaster keep the outer N pixels of the original,
blended into the new surface over 4 more pixels. Tiles drawn to join keep
joining (seams, pipes that cross a side at the same place), while the inside
is redone. Use about 8 to 16 px for a floor with a seam and a connector. It
needs the original, so it applies to remasters (Generate from a sprite, and
Batch), not to new designs. `pxmake.sh --keep-edge PX` overrides it for one
run. Clean up again is not offered for tiles (nothing to tune).

Make the kind's *final size* the tile's real size (256 for a 256 px floor),
so the pixel grid is a whole number (2 on SD 1.5, 4 on SDXL).

## 6. Results

Every run, batch and comparison of the output folder. Zoom (Fit, 1x to 8x),
backdrop (checker, dark, floor, light), raw renders; star the variant to
keep (*Export picks* copies them to `output/picked/`, named after the sprite
they replace), compare side by side. **Rename** gives a result folder another name; its
stars, owner, shares and the gallery images saved from it follow (not while a job is
writing to it). **Delete** a result, one sprite of a
batch, its failed sprites, or every empty result: they go to the output
folder's `.bin`, where *Undo last delete* puts them back until you *Empty
bin*. **Train on picks…** copies the starred sprites into a new or existing
model as training sprites (the originals stay), to train a model on what
another one made well. **View bin** lists what was deleted, with *Put back* and *Erase* for
each.

### 6.1 The gallery

Results are working files: you delete them. The **Gallery** keeps what you
want to keep. The bookmark button on a sprite in Results copies the image to
your gallery together with its recipe: the LoRA (file, epoch, checkpoint),
the seed, model, group, kind, settings, prompt and extras. Then:

- It **stays even when its result is deleted**, and the recipe cannot be
  edited; only the name, folder, tags and note can. While an image is in the
  gallery, **its LoRA cannot be deleted** (it can be renamed; the image follows);
  remove the image from the gallery and the LoRA is free again.
- **Share** an image (button in its dialog) with chosen people or everyone;
  they can look at it and export it, not change or remove it. Each person has
  their own gallery.
- **Folders and tags**: give an image one folder (an album) and any tags (up to 12), then filter
  by folder, tag, owner (*Mine* / *Shared with me*) or text (name, LoRA, seed,
  model...). Click folders and tags to add them (several folders: any
  of them; several tags: all of them), click one again to **hide** it (crossed
  out), a third time to clear it. *Clear all filters* (shown while any filter is on) resets the search and all filters. In the search box, several words must
  all match and a word starting with `-` excludes (`swarm -veteran`). The counts
  follow the filters. *Move to folder*, *Add tags* and *Remove tags* work on the
  selection, which only ever holds sprites that are on screen (changing a filter
  deselects the hidden ones). A folder is just a label: type a new name to create one.
- **Export zip** of the selection (or *with folders*, which keeps the gallery
  folders as folders in the zip): the PNG files named after the sprites, and a
  `manifest.json` with each recipe.
- **Train on these…** copies the selected sprites into a new model or an existing
  one (pick the group and kind if you like) as training sprites, named after the
  gallery names. The gallery images stay; sprites the model already has are never
  overwritten.

Same recipe, same picture? Generating again with the **same LoRA file, seed,
prompt, settings, starting sprite and base model** gives the same image on the
same graphics card, driver and ComfyUI/PyTorch versions. Another card or newer
software can change a few pixels, and a LoRA installed again under the same
name is a different file (which is why old files are kept).

## 7. Palettes

A LoRA reproduces the colours of its training sprites: a dark group comes
out dark. A palette fixes colours after rendering, without retraining.
**Rename id** changes a palette's id; the groups that use it follow.

**Making one** (Palettes page, *New palette*):

- **Automatic tuning**: no colours; a tuning computed so a group's mean
  light, contrast and saturation match other sprites (the rest of the
  model, or another model for a one-group model). It can become the group's
  palette at once.
- **A model's colours** / **a batch's colours**: the colours of a group, a
  kind or a batch's results, merged down to 2 to 256 colours, each weighted
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
| Time estimates | measured from your own jobs that finished without error (failed, cancelled and partly failed ones are ignored) |
| Updates | The **Updates** page (administrators) compares Sprite Studio, ComfyUI and kohya sd-scripts with their latest *released* version (a `vX.Y.Z` tag; unfinished work on the main branch is never installed). *Update now* installs the release and checks that it works; if the check fails the previous version is put back by itself. ComfyUI and sd-scripts wait for an idle GPU and refuse local changes. Sprite Studio waits until no job runs, then restarts (the page reconnects in a few seconds); if it does not start, it goes back to the previous version after a few attempts, and *Go back to the previous version* does it by hand. An installation that is a git checkout is updated with `git pull` instead |

## 9. Accounts and sharing

Everyone signs in (the sign-in page; the administrator creates the first
account with the one-time setup code, see INSTALL.md section 5).

- **Your own space.** Your models, their LoRAs, your results (with the bin and
  your picks and gallery), palettes, projects, jobs and logs are yours: other users do not
  see them. Shared things (below) and the source folders are the exceptions.
  The GPU queue shows that it is busy, and whose job waits, but not what
  somebody else is making.
- **Your password.** *Password* in the menu on the left changes it (at least
  10 characters; it signs you out everywhere else). After several wrong
  attempts the account waits a few minutes before it accepts another.
- **Sharing.** *Share* (a model's page, a result's row in Results, a palette)
  lets you choose users, or everyone with an account. Whoever it is shared with
  can **use** it: generate or run a batch with a shared model (its LoRA comes
  with it), apply a shared palette, look at a shared result, copy a shared
  model into one of their own (*New model → A copy of a model*), or make their
  own copy of a shared palette. They cannot change, train, rename or delete it,
  and a tag says who owns it. Anything a shared model makes belongs to the one
  who ran it.
- **Administrators** see everything, with its owner, can give a resource to
  another user (the *owner* in the sharing request), and alone use Settings,
  the ComfyUI controls and the update check on Overview, and the **Users**
  page: add a user (with a first password and *must change it at the first
  sign-in*), reset a password (the user is signed out and chooses a new one),
  require a change, make or unmake an administrator, disable, and remove (their
  resources go to the user you choose, nothing is deleted). The last
  administrator cannot be removed, demoted or disabled.

## 10. Troubleshooting

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
| "sign in" appears again | the session ended (a password change, a disabled account, a week without use) | sign in again |
| a model, result or palette a colleague talked about is not listed | it is not shared with you | ask them to *Share* it |

## 11. Glossary

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
