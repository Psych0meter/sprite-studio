# Installing Sprite Studio

`scripts/install.sh` sets everything up on a Debian or Ubuntu system that
already sees its NVIDIA GPU. This page covers what it needs, how to give a
container the GPU, and what the script installs.

## 1. What runs where

```
host (NVIDIA kernel driver, the GPU)
 └─ machine or container, user "spritegen" owns and runs everything below
     ├─ ComfyUI            generation, web UI and API on port 8188 (service comfyui)
     │    models/checkpoints/   base models (SD 1.5, SDXL)
     │    models/loras/         installed LoRAs
     ├─ ~/lora/            models, training sets, training runs, palettes
     │    sd-scripts/, venv/    kohya sd-scripts and its Python environment
     ├─ ~/spritegen/       Sprite Studio: scripts/, webapp/ (service spritegen-web, port 8189)
     └─ ~/sprites/         data (SG_DATA): input/ and game/ to import from, output/ for results
```

There is one GPU and it is shared: ComfyUI and a training run do not both
fit on an 8 GB card, so the app stops ComfyUI during a training run and
starts it again afterwards.

## 2. Requirements

| What | Needed | Notes |
|---|---|---|
| OS | Debian 12/13, Ubuntu 22.04+ | tested on Debian 13 |
| NVIDIA GPU | 8 GB or more | 12 GB or more for comfortable SDXL training |
| NVIDIA driver | installed, `nvidia-smi` works | on the host, for a container |
| CPU / RAM | 4 cores / 16 GB | 2 cores / 12 GB at least; 32 GB RAM for SDXL training |
| Disk | 50 GB on an SSD | 35 GB at least; 80 GB with SDXL |
| Internet | GitHub, PyPI, download.pytorch.org, huggingface.co | during the installation |

Disk use, roughly: two Python environments with PyTorch (about 5 GB each),
SD 1.5 (4 GB), SDXL (7 GB, optional), caches (2 to 3 GB), and under 1 GB
per training run. Results go to `SG_DATA/output`, which can be a network
share.

**Old cards.** NVIDIA's 590 Linux drivers dropped Maxwell and Pascal
(GTX 9xx, GTX 10xx): keep those cards on the 580 driver branch. CUDA 12.6
PyTorch builds still support them; `install.sh` picks the build from the
card.

## 3. In a Proxmox LXC container

Skip this section on a plain machine with the driver installed.

### 3.1 The driver on the host

The kernel driver lives on the host; containers borrow it.

1. Install the NVIDIA driver on the host (NVIDIA's `.run` installer or the
   distribution's packages, with the Proxmox kernel headers).
2. Load the modules at boot, `nvidia-uvm` included:

   ```bash
   printf 'nvidia\nnvidia_uvm\n' > /etc/modules-load.d/nvidia.conf
   ```

3. Make sure the device files exist after a reboot (`/dev/nvidia-uvm` is
   often created only on first use); a small unit or udev rule running this
   at boot avoids surprises:

   ```bash
   nvidia-modprobe -u -c=0
   ls -l /dev/nvidia*      # note the major numbers
   nvidia-smi
   ```

### 3.2 Create the container

Any Debian container works. With the
[Proxmox VE Helper-Scripts](https://community-scripts.org/scripts/debian),
which can also pass the GPU through:

```bash
var_cpu=4 var_ram=16384 var_disk=50 var_version=13 var_unprivileged=1 \
var_gpu=yes var_hostname=spritegen var_container_storage=local-lvm \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/debian.sh)"
```

Put the container's disk on SSD-backed storage (`local-lvm`, ZFS, Ceph
RBD). A raw disk file on spinning disks makes every package install crawl.

### 3.3 Pass the GPU by hand

Only if the container was not created with `var_gpu=yes`. Container
stopped, in `/etc/pve/lxc/<CTID>.conf`:

```
# Proxmox VE 8.1 and newer
dev0: /dev/nvidia0
dev1: /dev/nvidiactl
dev2: /dev/nvidia-uvm
dev3: /dev/nvidia-uvm-tools
dev4: /dev/nvidia-modeset
```

On older versions, with the major numbers `ls -l /dev/nvidia*` printed on
the host (`195` for the card; the `nvidia-uvm` one varies):

```
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
```

Both work in an unprivileged container. Inside, `ls -l /dev/nvidia*` must
list `nvidia0`, `nvidiactl` and `nvidia-uvm`.

### 3.4 The driver's user-space part in the container

The container needs the same driver version as the host, without the
kernel module. `install.sh` does it when given the host driver's installer:
`NVIDIA_RUN=/path/NVIDIA-Linux-x86_64-<version>.run`. Without it, PyTorch
usually still finds the libraries, but `nvidia-smi` is missing.

### 3.5 A shared data folder

To keep sprites and results on a NAS, mount the share on the host and bind
it into the container, then point `SG_DATA` at it:

```bash
pct set <CTID> -mp0 /mnt/pve/<storage>/sprites,mp=/mnt/sprites
```

In an unprivileged container, user ids are shifted by 100000: the
container's `spritegen` (uid 1000) is uid 101000 on the host and must be
allowed to write to `output/` there.

## 4. Run the installer

As root, from a checkout of the repository:

```bash
git clone https://github.com/Psych0meter/sprite-studio.git
cd sprite-studio
sudo SG_DATA=/mnt/sprites scripts/install.sh      # SG_DATA optional
```

It checks the GPU devices, installs the packages, creates the user,
installs ComfyUI with a PyTorch build that supports the card (and stops
with a clear message if it does not), copies Sprite Studio to the
user's home, downloads SD 1.5, installs the `comfyui` service, installs
kohya sd-scripts with the same PyTorch, and installs the `spritegen-web`
service with the sudo rule that lets it stop and start ComfyUI.

| Variable | Default | Meaning |
|---|---|---|
| `SG_USER` | `spritegen` | the user that owns and runs everything |
| `SG_APP` | `/home/$SG_USER/spritegen` | where the app is installed |
| `SG_DATA` | `/home/$SG_USER/sprites` | data: `input/`, `game/`, `output/` |
| `TORCH_INDEX` | from the card (`cu128` for RTX 50, else `cu126`) | PyTorch wheel index |
| `TORCH_VERSION` | the index's latest | pin a PyTorch version |
| `NVIDIA_RUN` | none | the host driver's `.run`, in a container |

The script can be run again at any time: finished steps are skipped and the
app's files are replaced by the new version. Your models, LoRAs, palettes,
results and settings are not touched.

## 5. After installing

- Studio: `http://<address>:8189` (`systemctl status spritegen-web`)
- ComfyUI: `http://<address>:8188` (`systemctl status comfyui`)
- Scripts: `~spritegen/spritegen/scripts`, in that user's `PATH`
  (`su - spritegen`)

The app has no login by default (it is meant for a private network). To
require a password, add `Environment=SG_WEB_PASSWORD=...` to
`/etc/systemd/system/spritegen-web.service`, then
`systemctl daemon-reload && systemctl restart spritegen-web`.

**SDXL** is optional: the Train page downloads it (about 7 GB) when you
choose it, or `MODEL=sdxl train_lora.sh setup` as the user.

## 6. Settings

The Settings page holds the folders and ComfyUI's address; environment
variables give the defaults (the service sets the data folders):

| Variable | Default | Meaning |
|---|---|---|
| `SG_DATA` | `~/sprites` | base of the three folders below |
| `PX_INPUT`, `PX_GAME` | `$SG_DATA/input`, `$SG_DATA/game` | named source folders sprites are imported from |
| `PX_OUTPUT` | `$SG_DATA/output` | results |
| `PX_LORA_HOME` | `~/lora` | models, training, palettes |
| `COMFY` | `~/ComfyUI` | ComfyUI's folder |
| `PX_COMFY_URL` | `http://127.0.0.1:8188` | ComfyUI's API |
| `SG_WEB_PORT`, `SG_WEB_HOST` | `8189`, `0.0.0.0` | the app's address |
| `SG_WEB_PASSWORD` | none | ask for a password |
| `SG_WEB_STATE` | `~/.spritegen-web` | the app's own state: jobs, logs, settings, picks |

## 7. Moving to another machine

Keep `~/lora/` (models, training runs, palettes), the installed LoRAs in
`~/ComfyUI/models/loras/`, and `~/.spritegen-web/settings.json`. Install
on the new machine, copy them back, restart `spritegen-web`. Results live
in `SG_DATA/output`.
