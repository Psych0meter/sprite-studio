# Changelog

All notable changes to Sprite Studio are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Releases**: pushing a `vX.Y.Z` tag publishes a GitHub release with the
  changelog notes and a source archive.

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

[Unreleased]: https://github.com/Psych0meter/sprite-studio/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Psych0meter/sprite-studio/releases/tag/v0.1.0
