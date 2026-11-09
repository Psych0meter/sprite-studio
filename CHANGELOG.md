# Changelog

All notable changes to Sprite Studio are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Install**: Python 3.11 or newer is required (the project already needed it). `install.sh` stops with a clear message on an older system before changing anything; the supported systems are Debian 12/13 and Ubuntu 24.04+ (Ubuntu 22.04 has Python 3.10). The tests run on Python 3.11, 3.12 and 3.13.
- **Jobs**: one user can have at most 20 jobs waiting for the GPU and 4 CPU jobs running (`SG_MAX_QUEUED_PER_USER`, `SG_MAX_CPU_JOBS_PER_USER`), so one person cannot fill the queue for everyone.
- **Security**: file responses (`/api/file`) are marked `private`, so no shared cache keeps them.

### Fixed

- **Updates**: a Sprite Studio update that is cut short (power loss, a kill) while it replaces files is now detected and rolled back at the next start. The marker is written before the first file changes, the web packages are saved and put back with the files, and the release's `pyproject.toml` is read before anything changes.
- **Install**: ComfyUI is started again when the training setup fails after it was stopped.
- **Sharing**: a malformed list of users in a sharing request answers 400 instead of an error.

## [0.3.2] - 2026-11-08

### Added

- **Updates**: the Updates page also updates Sprite Studio itself. It replaces its files, restarts, and goes back to the previous version by itself if the new one does not start or answer (a *Go back* button does it by hand). It needs an installation made by `install.sh`, not a git checkout.

### Changed

- **Train**: the learning rate is shown as a plain decimal in your browser's format (0,0001), never as 1e-4, and the hint says the same; 1e-4, 0.0001 and 0,0001 are all accepted when typed.
- **Status bar**: small progress bars for the GPU (memory in use, how busy it is) and for the disk space used and free on the volumes the app works on (results, models, ComfyUI, its own state), amber from 80 % and red from 92 % full.
- **Updates**: each panel links to the project's GitHub page and shows the notes of the newer release when there is one.
- **Updates**: ComfyUI, kohya sd-scripts and Sprite Studio now move only to the latest *released* version (the highest `vX.Y.Z` tag), no longer to the tip of the upstream branch. The packages are saved first and the previous version is put back when the new one fails its check. **Breaking**: the update check no longer lists commits, and `scripts/pxupdate.sh` is replaced by `scripts/pxupdate.py`.
- **ComfyUI**: `install.sh` now makes ComfyUI listen on `127.0.0.1` only (it has no login, so it was open to the whole network). Running it again on an existing installation changes that. To reach ComfyUI from another machine use an SSH tunnel, or install with `COMFY_LISTEN=0.0.0.0` behind a firewall.
- **Security**: the app's own state (accounts, prompts, paths, picks, job logs, `auth.log`) is kept in a folder only its user can enter (0700, files 0600); API answers are never cached (`Cache-Control: private, no-store`); the API schema (`/openapi.json`) is no longer published; image analysis runs two at a time.
- **Accounts**: every account has a permanent internal id, and sharing, ownership, jobs, picks, projects and the per-user folders use it instead of the user name (a name could be given again after a deletion). Existing installations are migrated when the app starts (names in your sharing lists, jobs, picks, projects and the `uploads`, `picked` and `recoloured` folders become ids; a name with no account is dropped). The pages still show names. Downgrading to 0.3.1 afterwards is not supported.

### Fixed

- **Users**: deleting a user also hands their jobs, picks, projects and upload, pick and recolour folders to the user who takes over (the folders become `from_<name>` in theirs). A new user created later with the same name starts with nothing and inherits no sharing.
- **Speed**: the model list (loaded by every page, Batch most visibly) took 6 s with many results and LoRA files because it read every result's `job.txt` once per LoRA file; it no longer reads them at all, the LoRA files page keeps its count for 20 s, and the first visit asks for the list once instead of twice.
- **Pages**: a page that was still loading when you moved to another one could draw under it (the Train page under Results). Every page now draws into its own container, so a late page is dropped.
- **Train**: training stops with a clear message when ComfyUI is still running 30 seconds after it was asked to stop, instead of starting on a busy GPU.
- **Speed**: pages no longer wait on the logs of every past job; the measured speeds are kept until a job starts or ends (the Overview, Generate and Train pages were slow with many jobs).
- **Spinners**: the corner spinner hides while a control or the page already shows its own.

## [0.3.1] - 2026-11-08

### Fixed

- **Spinners**: the corner spinner shows above dialogs (it was hidden behind them, so *Train on picks* looked frozen), and any button or select that waits for the server (more than a quarter of a second) is dimmed with its own spinner until it answers, in dialogs and pages alike.
- **Models**: the *Remaster distance* of a kind moves in steps of 0.01 with the arrows (it jumped by 0.15 or more), and stays between 0.30 and 0.95.
- **Models**: a new group or kind starts with the file name pattern `.` (every sprite) instead of an empty one, which matched nothing. An empty pattern still means "only sprites you sort by hand".
- **Train**: the time estimate is now seconds per step, measured on your last finished runs of the same base model (SD 1.5 and SDXL apart), times the steps of the next run (images × repeats × epochs). It follows the *Epochs* and *Repeats* fields as you type. Before, it was the total time of the last run, whatever its size.

## [0.3.0] - 2026-11-08

### Added

- **Accounts**: the app asks everyone to sign in. The first start prints a one-time setup code that creates the administrator on the sign-in page; what existed before (models, results, palettes, projects, jobs, picks) becomes theirs. Passwords are hashed (scrypt, 10 characters or more), sessions are a `HttpOnly` cookie, wrong passwords lock the account for a few minutes, and `auth.log` records sign-ins and changes.
- **Users**: administrators add, disable, remove and make administrators of users on a new Users page, reset a password or require a change at the next sign-in, and give the resources of a removed user to someone else. Users change their own password from the menu on the left. `webapp/users_cli.py` does the same from the machine (and recovers a lost administrator password).
- **Own space**: each user's models (with their LoRAs), results (with bin and picks), palettes, projects, jobs and logs are private; administrators see everything, with who owns it.
- **Sharing**: a *Share* button on models, results and palettes gives chosen users, or everyone, read-only use: generate with a shared model, apply a shared palette, look at a shared result, copy a shared model or palette. Only the owner (or an administrator) changes or deletes it.

- **Results**: *Train on picks…* copies the starred sprites into a new model, or an existing one, as training sprites (all in one group and kind if you choose, named after their pick name, never overwriting a sprite the model has), and opens the model.
- **Updates**: *Check for updates* on the Overview's ComfyUI card looks at the upstream repositories of ComfyUI and kohya sd-scripts (a git fetch, nothing is changed) and lists the newer commits and how to update.
- **Preview prompts**: *Auto-fix* proposes to put the trigger word first in every prompt that does not start with it (a stale or mistyped first word is replaced), showing each old and new start before changing anything.
- **Results**: *View bin* lists the deleted results (cover, name, date, size) with *Put back* and *Erase* for each.
- **Colours**: palettes and the clean-up can hold up to 256 colours (was 64): taking colours from sprites, *Colours at most* in Clean up again, `--max-colours`. The default stays 64.
- **Outline style**: *Selective* outlining paints the outline in a darker shade of the sprite's own colours instead of the darkest one (Generate › More settings, Clean up again, `pxmake.sh --outline-style`).
- **Palettes**: a warning and a *Merge them* button for near-twin colours.
- **Models**: a hint when a kind is larger than about 200 px.

- **Templates**: a built-in "Tiles (floors, walls)" model, a Tile kind that keeps the original's edge.
- **Tiles**: a kind can be a **Tile** (floors, walls: opaque, full colour, no
  background removal, colour limit or outline) and have **Keep edge (px)**:
  a remaster keeps the original's outer pixels, so tiles that were made to
  join still join. Their captions say "game tile, full colour" instead of
  "pixel art, flat green background". `pxmake.sh --keep-edge` overrides it for a run.
- **Results**: each image shows its seed next to the render time (click to copy it).
- **LoRA files**: a new page lists every LoRA in ComfyUI's loras folder (name,
  model, base, epoch, date, size, number of results made with it, and which
  one each model generates with). **Use** installs a file as the model's own,
  **Rename** and **Delete** manage them (several at once), **Compare** opens
  Compare LoRAs with the ticked files.
- **Menu**: the version of the app is shown at the bottom of the left menu.
- **Updates**: an administrator page (*Updates* in the menu, also linked from the Overview) shows what is new upstream for ComfyUI and kohya sd-scripts and has an *Update now* button: a fast-forward `git pull`, the Python requirements, and a ComfyUI restart, as a job with a log. It waits for an idle GPU and refuses a checkout with local changes (`scripts/pxupdate.sh`).
- **Spinners**: a small spinner appears while the app waits for the server (after 0.4 s), pages show a loading note, and the thing you changed (a model chip in Generate, a select in Train or Compare) is dimmed until its data arrives.

### Changed

- **Settings, ComfyUI controls, updates**: reserved for administrators; the source folders stay shared and read-only. The Results folder's `picked/`, `uploads/` and recoloured sprites are kept in a folder per user.
- **Install** (**breaking**: nobody can use the app until the administrator is created with the setup code; the old password is not carried over): the service listens on the network (an account is needed for everything); `SG_WEB_PASSWORD` and `SG_WEB_ALLOW_OPEN` are no longer used (the app says so at start, `install.sh` drops them). Passwords travel in clear text over plain HTTP: put an HTTPS reverse proxy in front outside a trusted network.
- **Live updates**: the status bar, job panels, Jobs, a job's log, Results and Train now share one refresh mechanism. Each one asks the server only while something can change, rebuilds only what changed, pauses while the tab is hidden, never sends overlapping requests and retries after an error. The Jobs list also notices jobs started from elsewhere (every 15 s when idle).
- **Train page**: it no longer rebuilds itself every few seconds while a job runs. Progress bars update in place, from the status the app already polls; the server is asked again every 20 s while training (4 s while previews render) and the page is rebuilt only when something it shows changed: a new checkpoint or preview image, a job starting or ending. Nothing is polled while the tab is hidden.
- **LoRA files**: click a column header to sort by it (click again to reverse).
- **Jobs**: *Clear successful* removes only the jobs that succeeded, so failed ones stay readable; *Clear all finished* (with a confirmation) removes every finished job.
- **Time estimates**: the measured speed (seconds per image, minutes per training) uses only jobs that finished without error, no longer failed, cancelled or partly failed ones.
- **Long lists**: Jobs and LoRA files show 25, 50 or 100 rows per page with the same page controls (the choice is remembered), and the Overview's LoRA list scrolls instead of stretching the page.
- **Results**: the list on the left now updates by itself (new results, running batches, sprite counts) every 30 seconds, and a result a job is still writing is patched in place: only the sprites that changed are redrawn, so the page no longer reloads completely and keeps its scroll position and open images. When the job finishes, the page settles once.
- **Security**: found by an outside audit. A plain run now listens on this machine only (`SG_WEB_HOST`); the service made by `install.sh` still listens on every address and keeps a password, port or host added to its unit, and the installer says when no password is set. Changes sent from another website are refused. Requests, uploaded images (checked as real images, at most 64 million pixels) and zips (5000 files, 1 GB unpacked) have size limits. `/api/file` serves only images and small text files, no longer model weights or code. A password with accents no longer crashes the login.
- **Jobs**: at most 100 jobs may wait for the GPU and 8 CPU jobs run at once (the app says so instead of piling up); the logs of old jobs are deleted.
- **Prompts**: the negative prompt also rejects pillow shading, anti-aliased edges and jagged lines (the training captions are unchanged).
- **Wording**: the app, guide and examples no longer speak of ships, factions or hulls: groups are "teams or families", examples use neutral units, projects are not "games". Stored names (`game` source folder, `PX_GAME`) and the hard-surface colour words are unchanged, so saved models and trained LoRAs keep working.

- **Results**: the render time of each image is on its own line under the name; a
  long name hid it.
- **Batch**: a batch with no name is now called after its model, LoRA and date
  (it used to be the date alone, so every unnamed batch of the day landed in
  the same folder). Running the same batch again still continues it.
- **Train**: the list of checkpoint files shows an epoch for each file; the
  last one (which the trainer saves without a number) reads *epoch 8 (last)*.
  *Add* names it with that epoch (`<trigger>_e8`) instead of `_final`.
- **Status line**: reads *memory 6.5 of 8 GB, GPU busy 100 %*. The percentage
  was always the share of time the card computes, not its memory.

### Fixed

- **Install**: running `install.sh` again keeps the data folder (`SG_DATA`) the service already uses, instead of going back to `~/sprites` when it is not given again (results, sources and picks seemed to vanish).
- **Projects**: the delete menu on a project always offers *No project*, so it is no longer empty when there is only one project.
- **Results**: a result folder that cannot be read (an empty `job.txt`, for one) no longer empties the whole Results page; it is skipped and named in the service log.
- **Users**: you can no longer remove or disable your own account (the buttons are hidden on your row and the server refuses).
- **Results**: the list of results is faster with many shared items (the access list is read once per change, not once per request).
- **Train on picks**: a refused request (an unknown group or kind, an invalid model) no longer leaves a new model behind, and only the picks that really became sprites lose their star.
- **Updates**: the check is kept for 15 minutes, only one runs at a time, and a checkout with no upstream branch says so. *Check again* looks again at once.

### Security

- **Models**: resetting a model to its template checks that you may use it before anything else, so someone else's model answers "not found".
- **Sign-in**: the single shared password (HTTP Basic) is replaced by accounts with per-user sessions, hashed passwords, lockout after wrong attempts, an administrator role, and ownership checks on every model, result, palette, LoRA file, pick and job (404 for what is not yours, 403 for what is only shared with you).
- **Request size**: the limit on a request body is enforced on the bytes really read, so a missing or false `Content-Length` no longer gets round it; an uploaded file's size is also checked before its base64 is decoded.
- **Zip import**: at most 250 MB unpacked by default (was 1 GB), `SG_MAX_ZIP_MB` changes it.
- **Image analysis**: `/api/info` reads an image's size first and refuses more than 16 million pixels, and counts colours with far less memory; `/api/file` refuses text files over 10 MB.

## [0.2.0] - 2026-11-03

### Added

- **Train**: previews can be rendered after training. A box above the preview
  grid lists the checkpoints with missing previews; **Render the missing
  previews** renders the run's first 2 test prompts for the ticked ones
  through ComfyUI, with the run's own seeds, and shows each one as it is
  done. Existing previews are kept. On a card under 12 GB (SDXL) a checkbox,
  *Render previews when training ends*, does it automatically after the run.
- **Results, Train**: the render time of each generated image is shown
  (small figure next to the name of a variant, under a preview). Images
  generated before this version have none.
- **Results**: **Clean up again** (sliders icon on a variant). The model's
  raw render is shown next to the result, with live settings for palette,
  symmetry, number of colours, edge, loose specks, background tolerance,
  tidying and outline. No GPU is needed. A good one is kept as a new
  variant (`<name>_c1.png`); the old one stays.
- **Releases**: pushing a `vX.Y.Z` tag publishes a GitHub release with the
  changelog notes and a source archive.

### Changed

- **Models**: the *Preview prompts* tab is easier to use. Each prompt is its own
  row with a words-to-avoid field, a check that it starts with the model's
  trigger word (with a **Fix** button), a tag on the first 2 prompts (the ones
  rendered afterwards), move up/down and remove. **Add** builds a prompt from
  a group and a kind, or an empty one; the automatic prompts are folded away
  and can be copied into the list to edit. Saved lists load unchanged.
- **Train**: previews are centred under their epoch, and a preview rendered
  afterwards has a uniform green background (the model's green drifts from
  one image to the next, which made the grid look uneven). Previews already
  rendered keep their own green.

- **Clean-up**: converting a render to a sprite is several times faster
  (colours are matched once per distinct colour), with identical output.

### Fixed

- **Training**: a finished SDXL run on a card under 12 GB is no longer
  reported as failed. All checkpoints were saved, but the script ended with
  exit code 1 because there was no preview folder to mention.
- **Train**: the list of checkpoint files no longer closes itself every time
  the page refreshes during a training run (the same goes for Generate's
  *More settings*).
- **Train**: each checkpoint file has an **Add** button next to **Use**, so
  checkpoints saved without previews (SDXL on a small card) can be installed
  under their own name and compared on Compare LoRAs.
- **Training**: SDXL training no longer runs an 8 GB card out of memory when
  it renders the preview images (hours in, at the end of epoch 2, after the
  checkpoint was saved). Under 12 GB SDXL saves its checkpoints without
  previews; try them with Compare LoRAs.

## [0.1.0] - 2026-11-01

First release as a standalone project. Installations made from the earlier
unversioned bundle are updated by running `scripts/install.sh` from this
repository: models, LoRAs, palettes, results and settings are kept.

### Added

- **Models**: one LoRA per model, with groups and kinds (words, keywords,
  words to avoid, file name patterns, sizes), per-sprite overrides, import
  rules and named source folders, PNG and zip uploads, model export and
  import, template sets (built-in *Generic starting points*, JSON sets in
  `~/lora/.templates/`).
- **Projects**: models grouped by game, with a project picker in the menu and
  a Projects panel to create, rename and delete projects.
- **Training**: training sets on a whole-number pixel grid per kind, editable
  captions, SD 1.5 and SDXL, automatic precision for the card, live progress,
  preview grids per epoch, *Use* and *Add* to install an epoch, and the epoch
  in use shown on Train, Generate and Batch.
- **Relight for training**: sprites whose shading is lit from below get their
  edges relit from the top in the training set.
- **Generation**: remasters, new designs from a description or a sketch,
  masked touch-ups, symmetry, LoRA choice and strength; the render clean-up
  into sprites (background removal, grid sampling, 64 colours, outline).
- **Fill**: how much of the canvas a new sprite takes, per kind, per model and
  per run.
- **Batches**: remaster every sprite of a model, or invent new sprites per
  group and kind from a list of ideas; resumable, with comparison sheets.
- **Palettes**: tuning (brightness, contrast, saturation, hue, light from the
  top), snap and match modes, colours from sprites or a batch's results,
  automatic tuning against other groups, `.hex` / `.gpl` / `.png` / `.json`
  import and export, per-group palettes, recolouring of existing results,
  and a preview computed like the output.
- **Results**: viewer with zoom and backdrops, comparison tray, picks and
  export, delete with a bin and undo, live refresh while a batch runs.
- **Compare LoRAs**: fixed-seed comparisons of LoRA files and strengths.
- **Jobs**: GPU and CPU queues, logs, cancel and retry, failures summarised
  with their cause, batches that stop early when every sprite fails the same
  way.
- **Installation**: `scripts/install.sh` for Debian and Ubuntu, with the
  PyTorch build chosen from the card, ComfyUI and Sprite Studio services,
  and the sudo rule to stop ComfyUI during training.
- **Project tooling**: README, user guide, installation and architecture
  documents, pytest suite, Ruff, ShellCheck and GitHub Actions checks.
- **Licence**: Sprite Studio is released under the MIT licence.

### Changed

- The command-line tools moved to `scripts/`; the web app finds them there
  (`SG_SCRIPTS` overrides it). `install.sh` removes the old copies at the top
  of the installation folder.
- Data folders default to `$SG_DATA/input`, `game` and `output`, with
  `SG_DATA` defaulting to `~/sprites` (the installer's service sets them).
- The colour-word setting `ship` is now called `hard`; saved models are
  migrated when loaded.
- Commands need a model to be named: `pxmake.sh --model`, `pxbatch.sh
  --model`, `MODEL=` for `pxmatrix.sh`, `CATEGORY=` for `train_lora.sh`.

### Removed

- The game-specific built-in models and their template set; saved models
  stay as they are (export one from its Settings tab to keep it as a
  template).
- `pxgen.sh` and `pxart.sh` (use `pxmake.sh`), and the one-off migration
  script `backup_old.sh`.

[Unreleased]: https://github.com/Psych0meter/sprite-studio/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/Psych0meter/sprite-studio/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Psych0meter/sprite-studio/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Psych0meter/sprite-studio/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Psych0meter/sprite-studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Psych0meter/sprite-studio/releases/tag/v0.1.0
