---
name: low-cpu-video-to-phila
description: 'Use when the user gives a local source folder and wants videos converted for low-CPU Jellyfin playback or direct play on Raspberry Pi phila, including DVD VOB/VIDEO_TS sources, then copied to phila.'
argument-hint: 'Local source folder and destination folder on phila'
user-invocable: true
disable-model-invocation: false
---

# Low-CPU Video Conversion to phila

Convert a user-provided local media folder into Raspberry Pi-friendly files and copy them to the Jellyfin library on `phila`. The goal is direct play by the client, not real-time server transcoding. Do not claim that a container change alone guarantees direct play.

## Workflow

1. Resolve the local source folder and destination folder on `phila`. If the destination is missing or ambiguous, ask before copying. The SSH host is `charan@phila`; the Jellyfin USB mount is `/srv/jellyfin-media`. Never assume the USB is mounted: verify it remotely with `mountpoint -q /srv/jellyfin-media` before writing. Check free space and existing destination names. Do not overwrite or delete existing files.
2. Inventory the source files and inspect streams with `ffprobe`. For DVD sources, identify `VIDEO_TS.IFO` and title structure with `HandBrakeCLI --scan`; encode complete program titles, not individual size-split VOB chunks. Prefer the local DVD folder with its IFO/BUP navigation files. If copied VOBs show CSS decode errors, stop and check the original/mounted disc rather than accepting a damaged output.
3. Choose a client-compatible output. Prefer MP4 with H.264/AVC, 8-bit 4:2:0 video and AAC-LC audio. Do not upscale, increase frame rate, or choose HEVC/AV1 for a Raspberry Pi 3 target. Preserve a compatible H.264 video stream with a remux when practical; otherwise encode on the Mac using the available hardware H.264 encoder. For DVD sources, preserve the original display aspect ratio and use deinterlacing when the source is interlaced. Keep quality and bitrate appropriate to the source rather than needlessly increasing them.
4. Keep subtitles separate and never burn them into the picture. Do not mux subtitle streams into MP4. Preserve text subtitles as external `.srt` files. For DVD image subtitles, identify the English track and prefer OCR to an external `.srt` when Subtitle Edit `seconv` is available. On macOS, prefer Apple Vision (`--ocr-engine:applevision --ocr-language:en-US`); Tesseract is a fallback (`--ocr-engine:tesseract --ocr-language:eng`). Review OCR warnings and suspicious timings/text; OCR can misrecognize dialogue. If OCR is unavailable or unreliable, keep the English `.idx`/`.sub` pair, explain that VobSub is image-based and some Jellyfin clients may require subtitle burn-in, and recommend a client that supports it or turning subtitles off. Never label image subtitles as SRT or claim they are text-searchable.
5. Use collision-resistant, episode-order-preserving filenames. For DVD titles, map complete titles to sequential episode names only after checking neighboring files or confirming the intended numbering. Keep conversion logs outside the destination library.
6. Validate before copying: probe each output's video/audio codecs and duration; confirm no subtitle stream is embedded; decode the whole MP4 with FFmpeg; confirm subtitle sidecars are nonempty and correctly paired. Treat decoder errors or a failed encode as a stop condition and report it, rather than copying a partial file.
7. Copy only finished media and required sidecars to the confirmed destination using `rsync` without deletion or overwrite options. Compare SHA-256 hashes for every copied file with the local originals. Keep local files unless the user explicitly asks to move or remove them.
8. If Jellyfin still uses high CPU, check the client Playback Info for Direct Play versus Transcoding. Check subtitle selection first, then container, video/audio codec, bitrate limits, and client support. Fix the specific incompatibility; do not stop active playback or restart Jellyfin without the user's approval.

## Command Patterns

For a DVD title on macOS, choose a bitrate appropriate to the source and encode on the Mac. This template explicitly disables subtitle muxing and burn-in:

```bash
HandBrakeCLI -i "$SOURCE" -t "$TITLE" -o "$OUTPUT_MP4" -f av_mp4 -e vt_h264 -b "$BITRATE_KBPS" --comb-detect --decomb --crop-mode none -a 1 -E av_aac -B 160 -s none --subtitle-burned=none -m --optimize
```

If the source already has compatible H.264 video and AAC audio, prefer a remux over a lossy re-encode:

```bash
ffmpeg -i "$INPUT" -map 0:v:0 -map 0:a:0 -c copy -movflags +faststart "$OUTPUT_MP4"
```

For a DVD VobSub sidecar, select the verified English subtitle track and create a temporary Matroska file with the track passed through, not burned:

```bash
HandBrakeCLI -i "$SOURCE" -t "$TITLE" -o "$TEMP_MKV" -f av_mkv -e vt_h264 -b 1200 --crop-mode none -a none -s "$ENGLISH_TRACK" --subtitle-burned=none
mkvmerge -i "$TEMP_MKV"
mkvextract tracks "$TEMP_MKV" "$SUBTITLE_TRACK_ID:$ABSOLUTE_BASE.idx"
```

Use the subtitle track ID reported by `mkvmerge`. `mkvextract` creates the matching `.sub` beside the `.idx`. Use an absolute output path after the colon; a literal `~` there will not expand. Remove the temporary Matroska file after both sidecars have been verified.

For OCR of an English VobSub pair to a separate SRT with Subtitle Edit `seconv` on macOS:

```bash
seconv "$INPUT_IDX" subrip --ocr-engine:applevision --ocr-language:en-US --no-language-suffix --output-folder:"$OUTPUT_DIR" --json
```

Run `seconv lint` on the generated SRTs, verify cue text and timing, and disclose OCR proofreading needs. Keep the SRT external to the video.

## Reporting

State the output codecs, episode/file count, subtitle format and whether it is external, destination path, and checksum result. Be explicit if the client may still transcode when VobSub subtitles are enabled.
