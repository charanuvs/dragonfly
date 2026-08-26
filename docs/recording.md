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

`config/dragonfly.yaml`'s `storage.mount_point` (`/mnt/dragonfly-hdd` by
default) needs to be a real mount, backed by an `/etc/fstab` entry, not
just a one-off `mount` command — the recorder checks
`os.path.ismount(mount_point)` before every write (see below), so it needs
this to actually be a mount, not just an existing directory. Set that up
once:

**Format the drive ext4, not FAT32/exFAT.** This matters more than it
sounds: ext4 journals its metadata, so an unclean unplug (or power loss, or
the drive resetting itself on the USB bus) replays the journal on the next
mount and comes back consistent. FAT32 has no equivalent — learned the hard
way, a few unclean unplugs during testing left it throwing
`FAT-fs: Directory bread(block N) failed` / `OSError: [Errno 5]` on every
write, needing a manual `fsck.vfat` to recover, with some data lost to
truncation. For a device doing continuous writes that may get yanked, a
journaling filesystem isn't optional.

```bash
lsblk                          # find the device, e.g. /dev/sda1 — double-check
                               # this before mkfs; the wrong device wipes your SD card
sudo mkfs.ext4 -L DRAGONFLY /dev/sda1
sudo blkid /dev/sda1           # get the (new) UUID — formatting assigns a fresh one
sudo mkdir -p /mnt/dragonfly-hdd
sudo nano /etc/fstab
# add a line:
#   UUID=<uuid-from-blkid>  /mnt/dragonfly-hdd  ext4  defaults,nofail  0  2
sudo systemctl daemon-reload   # regenerate the .mount unit from the new fstab
sudo mount -a                  # mounts it now and validates the fstab line
sudo chown -R charan:charan /mnt/dragonfly-hdd   # ext4 has real ownership; the
                               # capture service runs as this user and must be
                               # able to write (vfat used uid/gid/umask mount
                               # options instead — those don't apply to ext4)
```

`nofail` matters: without it, a missing/failed drive can hang the Pi's boot
entirely waiting for a mount that'll never come.

The final `2` in the fstab line is the fsck pass number — it tells systemd
to check (and repair) this filesystem at boot, a backstop in case something
does get corrupted despite the journal.

## Auto-mounting on plug-in, and what happens if the drive disappears

The `/etc/fstab` entry above only gets applied at boot — if you unplug and
replug the drive while Phila is already running, nothing remounts it
automatically without the udev rule below.

**Auto-mount whenever this specific drive is plugged in** (not just at
boot), via a udev rule matching its filesystem UUID (the same UUID from
`blkid` above):

```bash
sudo tee /etc/udev/rules.d/99-dragonfly-hdd.rules <<'EOF'
SUBSYSTEM=="block", ENV{ID_FS_UUID}=="<uuid-from-blkid>", ACTION=="add", RUN+="/usr/bin/systemctl start mnt-dragonfly\x2dhdd.mount"
SUBSYSTEM=="block", ENV{ID_FS_UUID}=="<uuid-from-blkid>", ACTION=="remove", RUN+="/usr/bin/umount -l /mnt/dragonfly-hdd"
EOF
sudo udevadm control --reload-rules
```

Replace `<uuid-from-blkid>` with the actual UUID. `mnt-dragonfly\x2dhdd.mount`
is the unit name systemd generates from the `/mnt/dragonfly-hdd` fstab entry
(the escaped `\x2d` is a literal `-`) — the `add` rule tells udev to ask
systemd to (re-)mount it the instant the matching device shows up.

**The `remove` rule matters just as much, and is easy to overlook.** When a
USB drive is physically yanked (rather than cleanly unmounted), the kernel
keeps the mount entry in place — it just fails every I/O against it, and
ext4 flags the mount `shutdown` after the first errors. Two consequences:

- `os.path.ismount()` still returns **True**, so the recorder's mount check
  doesn't notice a physical unplug at all. Detection falls to the write
  failures instead (`write_failed` / `segment_failed`), which the monitor
  loop reports within ~10s. That path works, but don't expect the mount
  check to be what catches it.
- Worse, that dead mount squats on `/mnt/dragonfly-hdd`. When the drive is
  plugged back in it re-enumerates under a *new* device node (`/dev/sda1`
  where it used to be `/dev/sdb1`), and the `add` rule's mount silently
  fails because the mountpoint is already occupied. Recording then never
  resumes — every write keeps hitting the zombie mount and returning EIO,
  even though the drive is physically present and healthy.

The `remove` rule's lazy unmount (`umount -l`) clears the dead mount as soon
as the device disappears, leaving the mountpoint free so the `add` rule can
mount the drive properly when it returns. Without it, recovery requires a
manual `sudo umount -l /mnt/dragonfly-hdd && sudo mount -a`.

**What happens on the capture side if the drive is unplugged:**
`dragonfly-capture.service` has *no* systemd-level dependency on the mount
at all — not `RequiresMountsFor`, not `BindsTo`, nothing (just an `After=`
ordering hint so it starts after the mount unit at boot, best-effort, not
required). Two earlier attempts got this wrong, in the same way each time:

1. `BindsTo=mnt-dragonfly\x2dhdd.mount` tore the whole service down when the
   drive was unplugged, taking heartbeat/live view down as collateral
   damage even though neither touches that drive. `Restart=always` didn't
   even bring it back afterward, since systemd treats a `BindsTo`-triggered
   stop as intentional, not a failure.
2. Switching to `RequiresMountsFor=/mnt/dragonfly-hdd` was believed to only
   gate *startup* — but confirmed on real hardware that it **also** stops
   the already-running service once the mount unit later disappears. Same
   problem as `BindsTo`, just less obviously documented that way.

So capture's lifecycle is now fully decoupled from the mount at the
systemd level, in both directions. Instead, `SegmentedRecorder`
(`recorder.py`) handles storage itself, entirely in application code:
before every segment it checks `os.path.ismount(storage.mount_point)` —
without this check, `mkdir()` wouldn't fail when the drive isn't mounted,
it would silently create directories on the underlying root filesystem (the
SD card) instead of erroring, so recordings could quietly end up in the
wrong place. If the mount isn't there, or if any other write failure
occurs, it's reported over MQTT as "not recording" and retried on the next
scheduled segment — never crashing the recording thread or needing capture
to restart.

That check alone only runs once per segment (5 min by default), which
means unplugging the drive *mid*-segment could leave the dashboard showing
stale "Recording to disk" for nearly that whole interval before anything
noticed. A separate monitor loop (`_MONITOR_INTERVAL_S`, 10s) runs
independently of the segment schedule specifically to close that gap: it
reaps finished ffmpeg processes promptly (a segment that crashes from a
write error gets reported within ~10s, not at the next scheduled launch)
and watches for the mount transitioning to unmounted, reporting "not
recording" the moment either is detected — so the dashboard reflects
reality within about `_MONITOR_INTERVAL_S`, not `segment_seconds`.

Once the udev rule above remounts the drive, the next scheduled segment
attempt succeeds and recording resumes automatically, reporting "active"
again — that side is still on the `segment_seconds` schedule (resuming
isn't as time-sensitive as detecting the outage was). Heartbeat and live
view are unaffected throughout, since they never depended on the drive in
the first place.

Redeploy the unit file the normal way after pulling
(`bash deploy/install_pi.sh` re-copies it, or manually
`sudo cp deploy/dragonfly-capture.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart dragonfly-capture`).

All of this is configurable per-camera in `config/dragonfly.yaml`
(`fps`, `bitrate_kbps`, `segment_seconds`, `overlap_seconds`,
`high_watermark_pct`, `low_watermark_pct`, and the optional
`retention_days`) — adjust to trade off quality vs. retention as your
storage situation changes.
