#!/usr/bin/env bash
# Clears a dead ("zombie") mount and remounts the recordings drive.
#
# Why this exists: when a USB drive is physically yanked rather than cleanly
# unmounted, the kernel keeps the mount entry in place — ext4 just flags it
# `shutdown` and fails every I/O against it. Two problems follow:
#
#   1. os.path.ismount() still returns true, so nothing in userspace
#      "notices" via the obvious check (the recorder detects it via write
#      failures instead — see docs/recording.md).
#   2. The dead mount squats on the mountpoint. When the drive is plugged
#      back in it re-enumerates under a *new* device node (/dev/sdb1 where
#      it was /dev/sda1), and mounting fails because the mountpoint is
#      occupied. Recording never resumes — every write keeps hitting the
#      zombie and returning EIO — until someone manually runs
#      `umount -l` + `mount -a`.
#
# A udev ACTION=="remove" rule was tried first and doesn't work reliably:
# on removal there's no device left to probe, so ENV{ID_FS_UUID} generally
# isn't available to match against, and the rule silently never fires.
# This watchdog doesn't depend on udev exposing anything — it just looks at
# whether the mountpoint currently works, which is the thing we actually
# care about.
set -uo pipefail

MOUNT_POINT="${DRAGONFLY_MOUNT_POINT:-/mnt/dragonfly-hdd}"

log() { echo "dragonfly-mount-watchdog: $*"; }

# Not mounted at all: let `mount -a` try (fstab has nofail, so this is a
# no-op when the drive genuinely isn't present).
if ! mountpoint -q "$MOUNT_POINT"; then
    mount -a 2>/dev/null || true
    exit 0
fi

# Mounted — but is it actually alive? `stat -f` reads the filesystem
# statistics, which fails with EIO on a shutdown/dead mount while
# succeeding normally on a healthy one. This is the cheapest reliable
# liveness probe: no writes, no directory walks, nothing that could disturb
# an in-progress recording.
if stat -f "$MOUNT_POINT" >/dev/null 2>&1; then
    exit 0  # healthy, nothing to do
fi

log "$MOUNT_POINT is mounted but unreadable (dead mount) — clearing it"

# Lazy unmount: detaches the mountpoint immediately even though processes
# may still hold references to it (the recorder's ffmpeg may still have the
# doomed path open). A plain umount would fail with EBUSY here.
if ! umount -l "$MOUNT_POINT"; then
    log "umount -l failed"
    exit 1
fi

# Now that the mountpoint is free, remount whatever fstab says should be
# there. If the drive is physically back, this picks it up under its new
# device node; if it isn't, nofail makes this a harmless no-op and we'll
# try again on the next timer tick.
if mount -a 2>/dev/null && mountpoint -q "$MOUNT_POINT"; then
    log "remounted $MOUNT_POINT successfully"
else
    log "cleared dead mount; drive not present yet (will retry)"
fi
