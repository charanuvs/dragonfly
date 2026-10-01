#!/usr/bin/env bash
# Interactive USB storage configuration for Dragonfly.
#
# Detects connected USB storage drives, prompts the user to select one,
# formats it to ext4 (if desired), adds an entry to /etc/fstab with nofail,
# and mounts it at /mnt/dragonfly-hdd with proper permissions.
#
# Can be run during initial install or anytime later via:
#   sudo dragonfly-setup-storage
set -euo pipefail

MOUNT_POINT="${DRAGONFLY_MOUNT_POINT:-/mnt/dragonfly-hdd}"
SERVICE_USER="${DRAGONFLY_USER:-dragonfly}"
SERVICE_GROUP="${DRAGONFLY_GROUP:-dragonfly}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run as root (use sudo)." >&2
    exit 1
fi

echo ""
echo "========================================="
echo "  Dragonfly Storage Setup                "
echo "  Target Mount: $MOUNT_POINT             "
echo "========================================="

mkdir -p "$MOUNT_POINT"

# 1. Check if already mounted
if mountpoint -q "$MOUNT_POINT"; then
    echo "Storage is already mounted at $MOUNT_POINT:"
    df -h "$MOUNT_POINT" | tail -n 1 | awk '{printf "  Total: %s | Used: %s | Free: %s\n", $2, $3, $4}'
    # Ensure service user has write permission
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$MOUNT_POINT" 2>/dev/null || true
    chmod 755 "$MOUNT_POINT" 2>/dev/null || true
    echo "Storage is ready. No changes needed."
    exit 0
fi

# 2. Check if fstab already has an entry for this mountpoint
if grep -qs "[[:space:]]${MOUNT_POINT}[[:space:]]" /etc/fstab; then
    echo "Found existing entry for $MOUNT_POINT in /etc/fstab. Attempting mount..."
    if mount "$MOUNT_POINT" 2>/dev/null; then
        echo "Successfully mounted $MOUNT_POINT from /etc/fstab."
        chown -R "$SERVICE_USER:$SERVICE_GROUP" "$MOUNT_POINT" 2>/dev/null || true
        chmod 755 "$MOUNT_POINT" 2>/dev/null || true
        exit 0
    else
        echo "Notice: /etc/fstab has an entry for $MOUNT_POINT, but the drive could not be mounted."
        echo "If the drive is currently unplugged, Dragonfly's mount watchdog will mount it"
        echo "automatically as soon as it is plugged in."
        echo ""
        if [ -c /dev/tty ] && [ -r /dev/tty ]; then
            read -r -p "Do you want to reconfigure / select a different drive? [y/N]: " reconf </dev/tty || reconf="n"
        else
            reconf="n"
        fi
        case "$reconf" in
            [yY]|[yY][eE][sS]) ;;
            *) exit 0 ;;
        esac
    fi
fi

if [ ! -t 0 ] && [ ! -c /dev/tty ]; then
    echo "Non-interactive terminal detected. Skipping USB drive configuration."
    echo "You can configure storage interactively anytime by running:"
    echo "    sudo dragonfly-setup-storage"
    exit 0
fi

# 3. Scan for USB block devices (excluding system root/boot drives)
echo "Scanning for connected USB storage drives..."
usb_disks=$(lsblk -dno NAME,TRAN 2>/dev/null | awk '$2=="usb" {print $1}' || true)

if [ -z "$usb_disks" ]; then
    echo ""
    echo "No USB storage drives detected."
    echo "You can plug in an external USB drive at any time and run:"
    echo "    sudo dragonfly-setup-storage"
    echo ""
    echo "Note: Dragonfly portal and capture services will still run in the meantime."
    echo "(Camera capture will report 'not recording' until storage is mounted)."
    exit 0
fi

candidates=()
for disk in $usb_disks; do
    # Guard against selecting system drives (e.g. host booted from USB SSD)
    is_system=0
    for mnt in $(lsblk -nro MOUNTPOINT "/dev/$disk" 2>/dev/null); do
        if [ "$mnt" = "/" ] || [ "$mnt" = "/boot" ] || [ "$mnt" = "/boot/firmware" ]; then
            is_system=1
            break
        fi
    done
    [ $is_system -eq 1 ] && continue

    # Check for partitions
    parts=$(lsblk -nro NAME,TYPE "/dev/$disk" 2>/dev/null | awk '$2=="part" {print $1}' || true)
    if [ -n "$parts" ]; then
        for p in $parts; do
            candidates+=("/dev/$p")
        done
    else
        candidates+=("/dev/$disk")
    fi
done

if [ ${#candidates[@]} -eq 0 ]; then
    echo "No eligible (non-system) USB partitions found."
    echo "You can configure storage later with: sudo dragonfly-setup-storage"
    exit 0
fi

echo ""
echo "Detected candidate USB drive(s):"
idx=1
for dev in "${candidates[@]}"; do
    size=$(lsblk -dno SIZE "$dev" 2>/dev/null || echo "?")
    fstype=$(lsblk -dno FSTYPE "$dev" 2>/dev/null || echo "none")
    label=$(lsblk -dno LABEL "$dev" 2>/dev/null || echo "")
    uuid=$(lsblk -dno UUID "$dev" 2>/dev/null || blkid -s UUID -o value "$dev" 2>/dev/null || echo "")
    [ -z "$fstype" ] && fstype="unformatted"
    printf "  %d) %-12s Size: %-7s Filesystem: %-10s Label: %-15s UUID: %s\n" \
        "$idx" "$dev" "$size" "$fstype" "${label:--}" "${uuid:--}"
    ((idx++))
done
printf "  %d) Skip drive setup for now\n" "$idx"

echo ""
if [ -c /dev/tty ] && [ -r /dev/tty ]; then
    read -r -p "Select drive partition for Dragonfly recordings [1-$idx] (default: $idx): " choice </dev/tty || choice="$idx"
else
    choice="$idx"
fi
choice="${choice:-$idx}"

if ! [[ "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 1 ] || [ "$choice" -ge "$idx" ]; then
    echo "Skipping drive setup. You can run 'sudo dragonfly-setup-storage' at any time."
    exit 0
fi

selected_dev="${candidates[$((choice - 1))]}"
echo ""
echo "Selected: $selected_dev"

current_fs=$(lsblk -dno FSTYPE "$selected_dev" 2>/dev/null || echo "")
needs_format=0

if [ "$current_fs" != "ext4" ]; then
    echo ""
    echo "Notice: $selected_dev currently has filesystem '${current_fs:-none}'."
    echo "ext4 is strongly recommended for continuous camera recordings on Linux."
    if [ -c /dev/tty ] && [ -r /dev/tty ]; then
        read -r -p "Format $selected_dev as ext4 with label 'dragonfly-data'? (WARNING: ERASES ALL DATA ON $selected_dev) [y/N]: " format_ans </dev/tty || format_ans="n"
    else
        format_ans="n"
    fi
    case "$format_ans" in
        [yY]|[yY][eE][sS])
            needs_format=1
            ;;
        *)
            if [ -z "$current_fs" ]; then
                echo "Cannot mount an unformatted drive without formatting. Skipping mount setup."
                exit 0
            else
                echo "Proceeding with existing filesystem '$current_fs'."
            fi
            ;;
    esac
fi

if [ $needs_format -eq 1 ]; then
    echo "Unmounting $selected_dev if mounted..."
    umount "$selected_dev" 2>/dev/null || true
    echo "Formatting $selected_dev as ext4..."
    mkfs.ext4 -F -L dragonfly-data "$selected_dev"
fi

uuid=$(blkid -s UUID -o value "$selected_dev" 2>/dev/null || true)
if [ -z "$uuid" ]; then
    echo "Error: Could not retrieve UUID for $selected_dev." >&2
    exit 1
fi

fstype_to_mount=$(blkid -s TYPE -o value "$selected_dev" 2>/dev/null || echo "ext4")

echo "Configuring /etc/fstab..."
# Clean up any prior fstab entries pointing to this mount point
cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d%H%M%S)"
sed -i "\|[[:space:]]${MOUNT_POINT}[[:space:]]|d" /etc/fstab

echo "UUID=$uuid  $MOUNT_POINT  $fstype_to_mount  defaults,nofail,noatime  0  2" >> /etc/fstab

echo "Mounting $MOUNT_POINT..."
mount "$MOUNT_POINT"

if mountpoint -q "$MOUNT_POINT"; then
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$MOUNT_POINT" 2>/dev/null || true
    chmod 755 "$MOUNT_POINT" 2>/dev/null || true
    echo ""
    echo "== Success: Storage mounted at $MOUNT_POINT =="
    df -h "$MOUNT_POINT" | tail -n 1 | awk '{printf "  Capacity: %s | Free: %s\n", $2, $4}'
else
    echo "Warning: Mount command executed, but $MOUNT_POINT does not appear as a mount point."
fi
