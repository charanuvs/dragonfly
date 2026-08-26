# Video recording

Covers the segmented recorder (`src/dragonfly/modules/camera/recorder.py`):
how footage is captured, laid out on disk, and cleaned up, and the storage
math behind the defaults.

## Capture

Each camera with `record: true` in config gets its own `ffmpeg` subprocess
pipeline. Rather than trusting whatever fps/bitrate the camera happens to be
configured for, Dragonfly re-encodes: `-vf fps=10` forces a consistent output
frame rate regardless of the camera's native stream, and `-b:v`/`-maxrate`
pin the bitrate so storage usage is predictable (see the table below). This
trades a bit of CPU for not being at the mercy of whatever settings happen to
be configured on the camera itself.

## Disk layout

```
<storage.recordings_path>/<camera-id>/YYYY-MM-DD/HH/N.mp4
```

`N` runs 1..(3600/segment_seconds) and resets every hour — with the default
5-minute segments, that's `1.mp4` through `12.mp4` per hour. The date/hour
used is the segment's *scheduled* start time, not the exact moment recording
began (see cutover below).

## Gapless cutover

A new `ffmpeg` process is launched every `segment_seconds` (default 300s/5
min), and each process is told to run for `segment_seconds + overlap_seconds`
(default 310s) before it exits on its own. So when segment N+1 starts,
segment N is still running for its last `overlap_seconds` (default 10s) —
both files contain that window. If the cutover is ever delayed by a second
or two (process scheduling, disk I/O, whatever), there's still no actual gap
in coverage, because the overlap absorbs it.

## Retention

Cleanup is storage-usage-based, not a fixed day count: a background thread
checks every couple of minutes (storage can fill quickly on a small drive),
and once usage crosses `high_watermark_pct` (default 90%), it deletes the
single oldest completed segment file, rechecks usage, and repeats — one file
at a time — until usage drops back to `low_watermark_pct` (default 80%). The
gap between the two watermarks exists so it doesn't thrash (delete one file,
dip just under 90%, immediately need to delete again).

Segments are walked oldest-first using their `YYYY-MM-DD/HH/N.mp4` layout
(not filesystem mtime), and a segment that's still being actively written is
never a deletion candidate. Deleting a whole segment file is a cheap
metadata-only filesystem operation — no rewriting file contents — and since
every segment starts on a fresh keyframe, nothing is ever left corrupt. This
is effectively a ring buffer at segment granularity: the practical version of
"truncate old footage as new footage comes in," since a true byte-level
ring buffer isn't feasible for MP4 (or any GOP-based codec) — truncating
from the front of a single growing file would mean rewriting the entire
remaining file every time (filesystems have no cheap "remove from the
front" operation) and would invalidate MP4's global frame index, which has
to be rebuilt from scratch on every truncation.

This means retention in days isn't configured directly — it falls out of
whatever `bitrate_kbps`/`fps` you pick and how big your drive is (see the
storage math below), and self-adjusts if you change either later.

If you want a hard privacy/legal cutoff independent of free space — never
keep footage past N days even if there's room — set `retention_days`; it
runs as an extra, optional pass alongside the watermark cleanup. Leave it
unset (the default) to let storage usage be the only thing that drives
deletion.

## Storage math (why the defaults are what they are)

Bitrate is the only real lever for how many days of footage fit in a given
amount of storage — fps and resolution matter, but only insofar as they
affect the bitrate you need for acceptable quality. For an **8GB USB drive**
(~7GB usable after filesystem overhead and a safety margin):

| Bitrate | ~Size/day | ~Retention (7GB usable) |
|---|---|---|
| 100 kbps | 1.1 GB | ~6.5 days |
| 150 kbps | 1.6 GB | ~4.3 days |
| **200 kbps (default)** | **2.2 GB** | **~3.2 days** |
| 250 kbps | 2.7 GB | ~2.6 days |
| 300 kbps | 3.2 GB | ~2.2 days |
| 500 kbps | 5.4 GB | ~1.3 days |
| 1000 kbps | 10.8 GB | <1 day |

("Retention" above is illustrative — with watermark-based cleanup, actual
retention is however many days it takes to fill the drive from
`low_watermark_pct` to `high_watermark_pct`, which is a somewhat smaller
window than 0-100%, but the same bitrate-vs-days relationship holds.)

The honest takeaway: **8GB is genuinely small for continuous video**, even
at low bitrate. 200 kbps at 10fps is watchable for "was someone there"
purposes at a modest resolution, but it's noticeably compressed — don't
expect to read license plates. If more than ~3 days of retention or
noticeably better quality matters, the practical fix isn't a cleverer
encoding setting, it's a bigger drive — a 128GB–1TB USB SSD is cheap and
buys weeks-to-months of retention at bitrates that actually look good.

## Making the drive survive reboots

`dragonfly-capture.service` has `RequiresMountsFor=/mnt/dragonfly-hdd`, so
systemd won't start capture until that path is actually mounted — but that
only works if it's a real mount, backed by an `/etc/fstab` entry, not just a
one-off `mount` command. Set that up once:

```bash
lsblk                          # find the device, e.g. /dev/sda1
sudo blkid /dev/sda1           # get its UUID
sudo mkdir -p /mnt/dragonfly-hdd
sudo nano /etc/fstab
# add a line (adjust filesystem type — ext4/vfat/exfat — to match the drive):
#   UUID=<uuid-from-blkid>  /mnt/dragonfly-hdd  ext4  defaults,nofail  0  2
sudo mount -a                  # mounts it now and validates the fstab line
```

`nofail` matters: without it, a missing/failed drive can hang the Pi's boot
entirely waiting for a mount that'll never come.

## Auto-mounting on plug-in, and auto-restarting capture

The `/etc/fstab` entry above only gets applied at boot — if you unplug and
replug the drive while Phila is already running, nothing remounts it
automatically without the udev rule below, and `dragonfly-capture` won't
pick a remount back up on its own either without the service change below.

**1. Auto-mount whenever this specific drive is plugged in** (not just at
boot), via a udev rule matching its filesystem UUID (the same UUID from
`blkid` above):

```bash
sudo tee /etc/udev/rules.d/99-dragonfly-hdd.rules <<'EOF'
SUBSYSTEM=="block", ENV{ID_FS_UUID}=="<uuid-from-blkid>", ACTION=="add", RUN+="/usr/bin/systemctl start mnt-dragonfly\x2dhdd.mount", RUN+="/usr/bin/systemctl start dragonfly-capture.service"
EOF
sudo udevadm control --reload-rules
```

Replace `<uuid-from-blkid>` with the actual UUID. `mnt-dragonfly\x2dhdd.mount`
is the unit name systemd generates from the `/mnt/dragonfly-hdd` fstab entry
(the escaped `\x2d` is a literal `-`) — this rule tells udev, the instant the
matching device shows up, to (re-)start both the mount *and* capture
directly — see why both are needed below.

**2. Auto-restart `dragonfly-capture` once the drive is back.**
`deploy/dragonfly-capture.service` uses `BindsTo=mnt-dragonfly\x2dhdd.mount`
(instead of only `RequiresMountsFor`): unplugging the drive tears capture
down cleanly instead of leaving it running with a dead recorder thread (see
the "what happens if I disconnect storage" discussion — this is what fixes
that).

Important nuance: `Restart=always` does **not** bring capture back on its
own here. `BindsTo=` stopping a unit counts as a clean, intentional stop
(systemd logs "Stopping... / Deactivated successfully"), and systemd's
`Restart=` policies — even `always` — don't kick in after a stop like that,
only after the process actually crashes/exits unexpectedly. Confirmed this
in practice: after unplugging the drive, `systemctl status dragonfly-capture`
just sat at `inactive (dead)` indefinitely, with no retry attempts logged.
So the udev rule above is what actually brings capture back — it explicitly
`systemctl start`s capture the instant the drive reappears and remounts,
rather than relying on systemd to notice and retry by itself. (`Restart=always`
is still worth keeping in the unit file — it covers a genuine crash of the
capture process for reasons unrelated to the drive.) Redeploy this updated unit file the
normal way (`bash deploy/install_pi.sh` re-copies it, or manually
`sudo cp deploy/dragonfly-capture.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart dragonfly-capture`).

All of this is configurable per-camera in `config/dragonfly.yaml`
(`fps`, `bitrate_kbps`, `segment_seconds`, `overlap_seconds`,
`high_watermark_pct`, `low_watermark_pct`, and the optional
`retention_days`) — adjust to trade off quality vs. retention as your
storage situation changes.
