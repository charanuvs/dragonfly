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

A background thread checks hourly: any `YYYY-MM-DD` folder older than
`retention_days` gets deleted outright. As a safety net on top of that (in
case actual bitrate runs higher than configured), if disk usage still climbs
above 90%, the oldest remaining day gets removed regardless of
`retention_days` — so a miscalibrated bitrate can't silently fill the drive.

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

The honest takeaway: **8GB is genuinely small for continuous video**, even
at low bitrate. 200 kbps at 10fps is watchable for "was someone there"
purposes at a modest resolution, but it's noticeably compressed — don't
expect to read license plates. If more than ~3 days of retention or
noticeably better quality matters, the practical fix isn't a cleverer
encoding setting, it's a bigger drive — a 128GB–1TB USB SSD is cheap and
buys weeks-to-months of retention at bitrates that actually look good.

## Making the drive survive reboots

`dragonfly-hub.service` has `RequiresMountsFor=/mnt/dragonfly-hdd`, so
systemd won't start the hub until that path is actually mounted — but that
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

All of this is configurable per-camera in `config/dragonfly.yaml`
(`fps`, `bitrate_kbps`, `segment_seconds`, `overlap_seconds`,
`retention_days`) — adjust to trade off quality vs. retention as your
storage situation changes.
