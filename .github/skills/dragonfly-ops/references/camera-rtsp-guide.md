# Camera RTSP URL & Configuration Guide

This guide details RTSP URL patterns, stream selection, credential handling, and recording tuning for Dragonfly cameras.

---

## 1. URL Encoding Credentials

RTSP URLs embed username and password directly. Special characters in passwords (such as `@`, `:`, `/`, `#`, `?`, `%`, `&`, `!`) **must be percent-encoded** or the RTSP client/ffmpeg will fail to parse the URI.

| Character | Encoded | Character | Encoded |
|:---:|:---:|:---:|:---:|
| `@` | `%40` | `:` | `%3A` |
| `/` | `%2F` | `#` | `%23` |
| `?` | `%3F` | `%` | `%25` |
| `&` | `%26` | `!` | `%21` |
| `$` | `%24` | `+` | `%2B` |

**Example:**
- Password: `Pass@word:123!`
- Encoded: `Pass%40word%3A123%21`
- URL: `rtsp://admin:Pass%40word%3A123%21@192.168.1.100:554/h264Preview_01_main`

---

## 2. Common Camera Brand RTSP URL Patterns

Standard RTSP port is `554`. Replace `<user>`, `<password>`, and `<ip>` with the camera's values.

### Tapo / TP-Link
- **Main Stream (High Res)**: `rtsp://<user>:<password>@<ip>:554/stream1`
- **Sub Stream (Low Res)**: `rtsp://<user>:<password>@<ip>:554/stream2`
*Note: Create a "Camera Account" in the Tapo App (Settings → Advanced Settings → Camera Account).*

### Amcrest / Dahua
- **Main Stream**: `rtsp://<user>:<password>@<ip>:554/cam/realmonitor?channel=1&subtype=0`
- **Sub Stream**: `rtsp://<user>:<password>@<ip>:554/cam/realmonitor?channel=1&subtype=1`

### Hikvision / Annke
- **Main Stream**: `rtsp://<user>:<password>@<ip>:554/Streaming/Channels/101`
- **Sub Stream**: `rtsp://<user>:<password>@<ip>:554/Streaming/Channels/102`

### Reolink
- **Main Stream**: `rtsp://<user>:<password>@<ip>:554/h264Preview_01_main`
- **Sub Stream**: `rtsp://<user>:<password>@<ip>:554/h264Preview_01_sub`
*Note: Ensure RTSP port is enabled in Reolink Client under Network Settings → Advanced → Port Settings.*

### UniFi Protect
- RTSP / RTSPS is configured per-camera in UniFi Protect UI (manage camera → RTSP streams).
- High/Medium/Low feeds generate individual URLs.
- If using RTSPS on port 7441, change `rtsps://` to `rtsp://` and port from `7441` to `7447` for standard unencrypted RTSP.

### Wyze (with Wyze Bridge or RTSP firmware)
- `rtsp://<user>:<password>@<ip>:554/live`

### Generic ONVIF / RTSP IP Cameras
- `rtsp://<user>:<password>@<ip>:554/live/ch0`
- `rtsp://<user>:<password>@<ip>:554/onvif1`
- `rtsp://<user>:<password>@<ip>:554/media/video1`

---

## 3. Main Stream vs. Substream (`recording_rtsp_url`)

Dragonfly re-encodes segments using ffmpeg (`-vf fps=10`, `-b:v 200k`).
- **`rtsp_url`**: Used for watchdog reachability and browser live view HLS.
- **`recording_rtsp_url`**: Optional. If your camera exposes a lower-resolution substream (e.g. 640x480 or 720p), point `recording_rtsp_url` to it. This dramatically lowers the host CPU utilization during ffmpeg re-encoding compared to downsampling a 4K main stream.

```yaml
cameras:
  - id: driveway
    name: "Driveway Camera"
    rtsp_url: "rtsp://admin:pass@192.168.1.50:554/stream1"            # High-res
    record: true
    recording_rtsp_url: "rtsp://admin:pass@192.168.1.50:554/stream2" # Lower-res substream
```

---

## 4. Storage & Recording Parameter Tuning

| Parameter | Recommended Default | Description |
|---|---|---|
| `record` | `true` | Enables background segmented recorder |
| `fps` | `10` | Balances smooth motion and low CPU/storage overhead |
| `bitrate_kbps` | `200` | Target bitrate (~2.1 GB / day per camera) |
| `segment_seconds` | `300` | 5-minute video chunks (`1.mp4` - `12.mp4` per hour) |
| `overlap_seconds` | `10` | Buffer overlap to prevent footage gaps between segments |
| `high_watermark_pct` | `90.0` | Starts deleting oldest segment when drive reaches 90% |
| `low_watermark_pct` | `80.0` | Stops deletion once drive reaches 80% |
| `retention_days` | `null` | Optional ceiling; if unset, disk space watermarks govern cleanup |

### Testing RTSP Stream Reachability

Before adding to Dragonfly, test the stream directly on the hub host:
```bash
ffmpeg -rtsp_transport tcp -i "rtsp://<user>:<password>@<ip>:554/<path>" -t 5 -f null -
```
If ffmpeg connects and exits without error after 5 seconds, the RTSP URL is valid.
