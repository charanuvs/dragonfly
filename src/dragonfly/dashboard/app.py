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
        modules.append(
            {
                "module_id": status.module_id,
                "module_type": status.module_type,
                "name": meta.name if meta else status.module_id,
                "online": status.online,
                "last_seen": status.last_seen,
                "seconds_since_seen": round(now - status.last_seen, 1),
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
  <title>Dragonfly</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #111; color: #eee; margin: 2rem; }
    h1 { font-weight: 600; }
    .grid { display: flex; flex-wrap: wrap; gap: 1rem; }
    .card {
      border: 1px solid #333; border-radius: 10px; padding: 1rem 1.25rem;
      min-width: 220px; background: #1a1a1a;
    }
    .card .name { font-size: 1.1rem; font-weight: 600; margin-bottom: .25rem; }
    .card .id { color: #888; font-size: .8rem; margin-bottom: .75rem; }
    .status { display: flex; align-items: center; gap: .5rem; font-size: .95rem; }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .dot.online { background: #2ecc71; box-shadow: 0 0 6px #2ecc71; }
    .dot.offline { background: #e74c3c; box-shadow: 0 0 6px #e74c3c; }
    .meta { color: #888; font-size: .8rem; margin-top: .4rem; }
    .empty { color: #888; }
    .sysbar {
      display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: center;
      background: #1a1a1a; border: 1px solid #333; border-radius: 10px;
      padding: .6rem 1.25rem; margin-bottom: 1.25rem; font-size: .85rem; color: #ccc;
    }
    .sysbar b { color: #eee; }
    .history-card {
      border: 1px solid #333; border-radius: 10px; padding: 1rem 1.25rem;
      background: #1a1a1a; margin-bottom: 1.25rem;
    }
    .history-card .name { font-size: 1.1rem; font-weight: 600; margin-bottom: .5rem; }
  </style>
</head>
<body>
  <h1>Dragonfly</h1>
  <div id="sysbar" class="sysbar">Loading system stats&hellip;</div>
  <div class="history-card">
    <div class="name">CPU / memory &mdash; live (since page opened)</div>
    <canvas id="historyChart" height="70"></canvas>
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
          animation: false,
          scales: {
            y: { min: 0, max: 100, ticks: { color: '#888' }, grid: { color: '#292929' } },
            x: { ticks: { color: '#888', maxTicksLimit: 8 }, grid: { display: false } },
          },
          plugins: { legend: { labels: { color: '#ccc' } } },
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
          <div class="id">${m.module_id} &middot; ${m.module_type}</div>
          <div class="status">
            <span class="dot ${m.online ? 'online' : 'offline'}"></span>
            ${m.online ? 'Online' : 'Offline'}
          </div>
          <div class="meta">last check ${m.seconds_since_seen.toFixed(0)}s ago</div>
          ${m.module_type.includes('camera') ? `<div class="meta"><a href="/live/${m.module_id}" style="color:#4da3ff">Live view &rarr;</a></div>` : ''}
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
  <title>Dragonfly &mdash; __CAMERA_ID__ live</title>
  <style>
    body { background: #111; color: #eee; font-family: -apple-system, sans-serif; margin: 0; }
    h1 { padding: 1rem; font-size: 1.1rem; }
    h1 a { color: #4da3ff; text-decoration: none; font-size: .85rem; float: right; }
    video { width: 100%; max-width: 1280px; display: block; margin: 0 auto; background: #000; }
    .status { text-align: center; color: #888; font-size: .85rem; padding: .5rem; }
  </style>
</head>
<body>
  <h1>__CAMERA_ID__ &mdash; live <a href="/">&larr; back</a></h1>
  <video id="v" controls autoplay muted playsinline></video>
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
