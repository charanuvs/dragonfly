"""Local-LAN monitoring dashboard (FastAPI).

Bound to the Pi's LAN interface only (config.dashboard.bind_host) — see
docs/network.md. Runs in the portal process (hub/portal_main.py), which is
independent from the capture process (hub/capture_main.py) that actually
owns the heartbeat/recorder/live-stream ffmpeg — see docs/architecture.md.
This module has no direct reference to any capture-side object; state comes
from app.state.registry (built by portal's own MQTT subscription) and, for
live view, from files capture writes to a well-known shared directory plus
an MQTT request published through app.state.event_bus.

For local dev without the capture process running:

    uvicorn dragonfly.dashboard.app:app --reload

(will show "No modules reporting yet" since app.state.registry is None until
portal_main.py wires it up, and live view won't have anything to show since
nothing is publishing to the shared HLS directory.)
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from dragonfly.hub.sysinfo import get_system_stats

app = FastAPI(title="Dragonfly")

# Set by hub/portal_main.py at startup. Left as None/{}/"." so the app still
# imports (and /health works) if run standalone.
app.state.registry = None
app.state.camera_config = {}
app.state.storage_path = "/"
app.state.event_bus = None
app.state.live_dir = Path("/tmp/dragonfly-live")

# Staleness grace for any module that isn't a configured camera (so has no
# poll_interval_s of its own) — i.e. the watchdog's pseudo-modules. Comfortably
# above the watchdog's default 15s interval, so a couple of missed passes don't
# flap the card to offline.
_DEFAULT_STALE_GRACE_S = 45

# Friendly names for the watchdog's pseudo-modules — these aren't cameras or
# sensors, they're the system watching itself (see watchdog/monitor.py).
_PSEUDO_MODULE_NAMES = {
    "_capture": "Capture process",
    "_storage": "Recordings storage",
}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/modules")
def list_modules(request: Request) -> list[dict]:
    registry = request.app.state.registry
    camera_config = request.app.state.camera_config
    if registry is None:
        return []

    now = time.time()
    modules = []
    for status in registry.all():
        meta = camera_config.get(status.module_id)

        # A module is only "online" if we've heard from it recently — a
        # frozen last-known-true heartbeat (e.g. the whole capture process
        # died) shouldn't show green forever. Cameras have their own
        # poll_interval_s to size the grace period against; anything else
        # (e.g. capture's own process-health heartbeat) uses a flat default.
        online = status.online
        stale_after = meta.poll_interval_s * 3 + 10 if meta else _DEFAULT_STALE_GRACE_S
        if online and (now - status.last_seen) > stale_after:
            online = False

        name = meta.name if meta else _PSEUDO_MODULE_NAMES.get(
            status.module_id, status.module_id
        )

        # A recorder is only really "active" if we've heard from it recently.
        # It publishes on every segment launch (every segment_seconds); if
        # capture crashed without a clean stop, the last "active: true" event
        # goes stale rather than being updated to false, so treat an
        # overdue segment as not-recording instead of trusting it forever.
        recording_active = status.recording_active
        if recording_active and status.recording_last_event is not None:
            segment_s = meta.segment_seconds if meta else 300
            overlap_s = meta.overlap_seconds if meta else 10
            if now - status.recording_last_event > segment_s + overlap_s + 30:
                recording_active = False

        modules.append(
            {
                "module_id": status.module_id,
                "module_type": status.module_type,
                "name": name,
                "online": online,
                "last_seen": status.last_seen,
                "seconds_since_seen": round(now - status.last_seen, 1),
                "recording_enabled": bool(meta.record) if meta else False,
                "recording_active": recording_active,
                "recording_last_segment": status.recording_last_segment,
                "recording_seconds_since_event": (
                    round(now - status.recording_last_event, 1)
                    if status.recording_last_event is not None
                    else None
                ),
                "details": status.details,
            }
        )
    return modules


@app.get("/api/system")
def system_stats(request: Request) -> dict:
    return get_system_stats(request.app.state.storage_path)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX_HTML


@app.get("/live/{camera_id}", response_class=HTMLResponse)
def live_page(camera_id: str, request: Request):
    if camera_id not in request.app.state.camera_config:
        return HTMLResponse(f"<p>No live stream configured for '{camera_id}'.</p>", status_code=404)
    return _LIVE_HTML.replace("__CAMERA_ID__", camera_id)


@app.get("/live/{camera_id}/{filename}")
def live_file(camera_id: str, filename: str, request: Request):
    if camera_id not in request.app.state.camera_config or "/" in filename or ".." in filename:
        return Response(status_code=404)

    # Ask the capture process (a separate process — see docs/architecture.md)
    # to (ensure it) starts this camera's live ffmpeg. Every segment/playlist
    # fetch re-publishes this, which conveniently also acts as the "someone's
    # still watching" keep-alive for capture's idle-timeout reaper — no
    # separate touch mechanism needed.
    bus = request.app.state.event_bus
    if bus is not None:
        bus.publish(camera_id, "live_start_request", "1")

    file_path = request.app.state.live_dir / camera_id / filename

    # First request after starting cold has to wait for capture's ffmpeg to
    # produce the initial playlist/segment.
    for _ in range(20):
        if file_path.exists():
            break
        time.sleep(0.25)
    if not file_path.exists():
        return Response(status_code=503)

    media_type = "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
    return FileResponse(file_path, media_type=media_type, headers={"Cache-Control": "no-cache"})


_INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <!-- viewport-fit=cover so the page extends under the notch/home indicator
       on modern phones rather than being letterboxed by browser chrome. -->
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="dark">
  <meta name="theme-color" content="#111">
  <!-- Lets this be saved to a phone home screen and open chrome-less. -->
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>Dragonfly</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      background: #111; color: #eee; margin: 0;
      padding: 1.5rem max(1.5rem, env(safe-area-inset-right))
               max(1.5rem, env(safe-area-inset-bottom)) max(1.5rem, env(safe-area-inset-left));
      -webkit-text-size-adjust: 100%;
    }
    h1 { font-weight: 600; font-size: 1.5rem; margin: 0 0 1rem; }
    /* Auto-fitting columns: one per row on a phone, as many as fit on a
       desktop, without hard-coding breakpoints per card count. */
    .grid {
      display: grid; gap: 1rem;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr));
    }
    .card {
      border: 1px solid #333; border-radius: 10px; padding: 1rem 1.25rem;
      background: #1a1a1a; min-width: 0;
    }
    .card .name { font-size: 1.1rem; font-weight: 600; margin-bottom: .5rem; }
    .status { display: flex; align-items: center; gap: .5rem; font-size: .95rem; }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex: none; }
    .dot.online { background: #2ecc71; box-shadow: 0 0 6px #2ecc71; }
    .dot.offline { background: #e74c3c; box-shadow: 0 0 6px #e74c3c; }
    .meta {
      color: #888; font-size: .8rem; margin-top: .4rem;
      /* Long paths (segment filenames) must not stretch the card on a phone. */
      overflow-wrap: anywhere;
    }
    .empty { color: #888; }
    .sysbar {
      display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; align-items: center;
      background: #1a1a1a; border: 1px solid #333; border-radius: 10px;
      padding: .75rem 1.25rem; margin-bottom: 1.25rem; font-size: .85rem; color: #ccc;
    }
    .sysbar b { color: #eee; }
    .history-card {
      border: 1px solid #333; border-radius: 10px; padding: 1rem 1.25rem;
      background: #1a1a1a; margin-bottom: 1.25rem;
    }
    .history-card .name { font-size: 1.1rem; font-weight: 600; margin-bottom: .5rem; }
    /* Comfortable tap target, and enough contrast to find one-handed. */
    .card a { display: inline-block; padding: .35rem 0; }

    @media (max-width: 600px) {
      body { padding: 1rem max(1rem, env(safe-area-inset-right))
                     max(1rem, env(safe-area-inset-bottom)) max(1rem, env(safe-area-inset-left)); }
      h1 { font-size: 1.25rem; }
      .card, .history-card, .sysbar { padding: .85rem 1rem; }
      /* Each stat on its own line reads better than a wrapped run-on row. */
      .sysbar { flex-direction: column; align-items: flex-start; gap: .35rem; }
    }
  </style>
</head>
<body>
  <h1>Dragonfly</h1>
  <div id="sysbar" class="sysbar">Loading system stats&hellip;</div>
  <div class="history-card">
    <div class="name">CPU / memory</div>
    <!-- Wrapper gives Chart.js a definite height to fill; without it,
         responsive mode collapses the canvas on some mobile browsers. -->
    <div style="position:relative; height:clamp(120px, 22vh, 200px)">
      <canvas id="historyChart"></canvas>
    </div>
  </div>
  <div id="grid" class="grid"><p class="empty">Loading...</p></div>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js"></script>
  <script>
    // Purely client-side: polls /api/system every second and accumulates
    // points itself — nothing persisted on the Pi, resets whenever this page
    // reloads. Capped to the last 5 minutes so a tab left open for hours
    // doesn't grow without bound.
    const MAX_POINTS = 300;
    const liveData = { labels: [], cpu: [], mem: [] };
    let historyChart = null;

    function initChart() {
      const ctx = document.getElementById('historyChart').getContext('2d');
      historyChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: liveData.labels,
          datasets: [
            { label: 'CPU %', data: liveData.cpu, borderColor: '#4da3ff', backgroundColor: 'transparent', tension: .2, pointRadius: 0, borderWidth: 1.5 },
            { label: 'Mem %', data: liveData.mem, borderColor: '#2ecc71', backgroundColor: 'transparent', tension: .2, pointRadius: 0, borderWidth: 1.5 },
          ],
        },
        options: {
          responsive: true,
          // Fill the sized wrapper rather than holding a fixed aspect ratio,
          // which on a narrow screen would make the chart absurdly short.
          maintainAspectRatio: false,
          animation: false,
          scales: {
            y: { min: 0, max: 100, ticks: { color: '#888', maxTicksLimit: 5 }, grid: { color: '#292929' } },
            x: { ticks: { color: '#888', maxTicksLimit: 5, maxRotation: 0 }, grid: { display: false } },
          },
          plugins: { legend: { labels: { color: '#ccc', boxWidth: 12 } } },
        },
      });
    }

    function formatUptime(s) {
      const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
      return d > 0 ? `${d}d ${h}h` : `${h}h`;
    }
    function renderSysbar(s) {
      const mb = b => (b / 1e6).toFixed(0);
      const gb = b => (b / 1e9).toFixed(1);
      const disk = s.disk_ok
        ? `Disk <b>${gb(s.disk_used)}/${gb(s.disk_total)} GB</b> (${s.disk_percent}%)`
        : `Disk <b>not mounted</b> (${s.disk_path})`;
      document.getElementById('sysbar').innerHTML =
        `<span><b>${s.hostname}</b></span>` +
        `<span>CPU <b>${s.cpu_percent.toFixed(0)}%</b> (${s.cpu_count} cores)</span>` +
        `<span>Mem <b>${mb(s.mem_used)}/${mb(s.mem_total)} MB</b> (${s.mem_percent.toFixed(0)}%)</span>` +
        `<span>${disk}</span>` +
        `<span>up ${formatUptime(s.uptime_s)}</span>`;
    }

    // Single 1s poll drives both the stats bar and the live chart — no point
    // hitting /api/system from two separate timers.
    async function tickSystem() {
      const res = await fetch('/api/system');
      const s = await res.json();
      renderSysbar(s);

      liveData.labels.push(new Date().toLocaleTimeString([], {hour12: false}));
      liveData.cpu.push(s.cpu_percent);
      liveData.mem.push(s.mem_percent);
      if (liveData.labels.length > MAX_POINTS) {
        liveData.labels.shift(); liveData.cpu.shift(); liveData.mem.shift();
      }
      if (!historyChart) initChart();
      historyChart.update('none');
    }
    tickSystem();
    setInterval(tickSystem, 1000);

    function formatAgo(s) {
      if (s < 60) return `${s.toFixed(0)}s`;
      const m = Math.floor(s / 60);
      const rem = Math.round(s % 60);
      return rem ? `${m}m ${rem}s` : `${m}m`;
    }

    // Whatever facts the watchdog check produced for this module — disk
    // usage, systemd state, newest segment, last error. Rendered generically
    // so a new check can surface useful detail without touching this code.
    const DETAIL_LABELS = {
      used_pct: 'disk used', free_bytes: 'free', total_bytes: 'capacity',
      newest_segment: 'newest file', newest_age_s: 'file age',
      newest_size_bytes: 'file size', state: 'systemd', service: 'unit',
      host: 'host', port: 'port', mount_point: 'mount', error: 'error',
      capture_report: 'capture says', full: 'full', mounted: 'mounted',
      healthy: 'readable', stale_after_s: 'stale after', root: 'path',
    };
    const DETAIL_ORDER = Object.keys(DETAIL_LABELS);

    function fmtBytes(n) {
      const u = ['B','KB','MB','GB','TB']; let i = 0;
      while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
      return `${n.toFixed(1)} ${u[i]}`;
    }
    function fmtDetail(k, v) {
      if (v === null || v === undefined || v === '') return null;
      if (k === 'used_pct') return `${v}%`;
      if (k.endsWith('_bytes')) return fmtBytes(v);
      if (k.endsWith('_age_s') || k.endsWith('_after_s')) return formatAgo(v);
      if (k === 'newest_segment' || k === 'root') return String(v).split('/').slice(-3).join('/');
      if (typeof v === 'boolean') return v ? 'yes' : 'no';
      return String(v);
    }
    function renderDetails(details) {
      if (!details) return '';
      const keys = Object.keys(details).sort((a, b) => {
        const ia = DETAIL_ORDER.indexOf(a), ib = DETAIL_ORDER.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      });
      const rows = keys.map(k => {
        const v = fmtDetail(k, details[k]);
        if (v === null) return '';
        const label = DETAIL_LABELS[k] || k.replace(/_/g, ' ');
        const bad = k === 'error' || (k === 'full' && details[k]);
        return `<div class="meta"${bad ? ' style="color:#e74c3c"' : ''}>${label}: ${v}</div>`;
      }).filter(Boolean);
      return rows.join('');
    }

    async function refresh() {
      const res = await fetch('/api/modules');
      const modules = await res.json();
      const grid = document.getElementById('grid');
      if (!modules.length) {
        grid.innerHTML = '<p class="empty">No modules reporting yet.</p>';
        return;
      }
      grid.innerHTML = modules.map(m => `
        <div class="card">
          <div class="name">${m.name}</div>
          <div class="status">
            <span class="dot ${m.online ? 'online' : 'offline'}"></span>
            ${m.online ? 'Online' : 'Offline'}
          </div>
          <div class="meta">checked ${formatAgo(m.seconds_since_seen)} ago</div>
          ${m.recording_enabled ? `
          <div class="status">
            <span class="dot ${m.recording_active ? 'online' : 'offline'}"></span>
            ${m.recording_active ? 'Recording to disk' : 'Not recording'}
          </div>
          <div class="meta">
            ${m.recording_seconds_since_event !== null
              ? `verified ${formatAgo(m.recording_seconds_since_event)} ago`
              : 'no recording activity yet'}
          </div>` : ''}
          ${renderDetails(m.details)}
          ${m.module_type.includes('camera') && m.module_id.charAt(0) !== '_'
            ? `<div class="meta"><a href="/live/${m.module_id}" style="color:#4da3ff">Live view &rarr;</a></div>` : ''}
        </div>
      `).join('');
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>"""

_LIVE_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="dark">
  <meta name="theme-color" content="#111">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>Dragonfly &mdash; __CAMERA_ID__ live</title>
  <style>
    * { box-sizing: border-box; }
    body {
      background: #111; color: #eee; font-family: -apple-system, sans-serif; margin: 0;
      padding-top: env(safe-area-inset-top);
    }
    header {
      display: flex; align-items: center; justify-content: space-between; gap: 1rem;
      padding: .85rem 1rem;
    }
    h1 { font-size: 1.05rem; margin: 0; font-weight: 600; }
    /* Generous tap target — the old float:right link was fiddly on a phone. */
    header a {
      color: #4da3ff; text-decoration: none; font-size: .9rem;
      padding: .5rem .75rem; margin: -.5rem -.25rem -.5rem 0; flex: none;
    }
    video {
      width: 100%; max-width: 1280px; display: block; margin: 0 auto;
      background: #000;
      /* Cap height in landscape so controls and status stay reachable
         instead of the video pushing them off-screen. */
      max-height: 78vh;
    }
    .status { text-align: center; color: #888; font-size: .85rem; padding: .75rem; }
  </style>
</head>
<body>
  <header>
    <h1>__CAMERA_ID__ &mdash; live</h1>
    <a href="/">&larr; back</a>
  </header>
  <video id="v" controls autoplay muted playsinline webkit-playsinline></video>
  <div class="status" id="status">connecting&hellip;</div>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
  <script>
    const video = document.getElementById('v');
    const statusEl = document.getElementById('status');
    const src = '/live/__CAMERA_ID__/index.m3u8';
    if (Hls.isSupported()) {
      const hls = new Hls({ liveSyncDurationCount: 3 });
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => { statusEl.textContent = 'live'; });
      hls.on(Hls.Events.ERROR, (_e, data) => {
        statusEl.textContent = 'stream error: ' + data.details + ' (retrying...)';
        if (data.fatal) setTimeout(() => hls.loadSource(src), 2000);
      });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = src; // Safari's native HLS support
      statusEl.textContent = 'live';
    } else {
      statusEl.textContent = 'this browser cannot play HLS';
    }
  </script>
</body>
</html>"""
