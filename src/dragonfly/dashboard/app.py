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

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from kasa.exceptions import KasaException

from dragonfly.hub.kasa_switch import KasaSwitchController, run_auto_off_schedule
from dragonfly.hub.sysinfo import get_system_stats

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
  controller = application.state.kasa_switch_controller
  switch_config = application.state.kasa_switch_config
  schedule_task = None
  button = None

  if controller is not None and switch_config is not None:
    schedule_task = asyncio.create_task(
      run_auto_off_schedule(controller, switch_config.timezone)
    )
    try:
      from gpiozero import Button

      button = Button(switch_config.gpio_pin, pull_up=True, bounce_time=0.05)
      loop = asyncio.get_running_loop()

      def toggle_from_button() -> None:
        future = asyncio.run_coroutine_threadsafe(controller.toggle(), loop)

        def report_result(result) -> None:
          try:
            state = result.result()
          except KasaException as exc:
            log.error("GPIO button failed to toggle %s: %s", controller.name, exc)
          else:
            log.info("GPIO button set %s to %s", controller.name, "on" if state else "off")

        future.add_done_callback(report_result)

      button.when_pressed = toggle_from_button
    except Exception:
      log.exception("GPIO button unavailable; dashboard switch control remains enabled")

  try:
    yield
  finally:
    if button is not None:
      button.close()
    if schedule_task is not None:
      schedule_task.cancel()
      try:
        await schedule_task
      except asyncio.CancelledError:
        pass


app = FastAPI(title="Dragonfly", lifespan=lifespan)

# Set by hub/portal_main.py at startup. Left as None/{}/"." so the app still
# imports (and /health works) if run standalone.
app.state.registry = None
app.state.camera_config = {}
app.state.storage_path = "/"
app.state.recordings_path = None
app.state.event_bus = None
app.state.live_dir = Path("/tmp/dragonfly-live")
app.state.kasa_switch_config = None
app.state.kasa_switch_controller: KasaSwitchController | None = None

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

    now = time.time()
    modules = []
    for status in registry.all() if registry is not None else ():
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

    switch_controller = request.app.state.kasa_switch_controller
    switch_config = request.app.state.kasa_switch_config
    if switch_controller is not None:
        modules.append(
            {
                "module_id": "_kasa_switch",
                "module_type": "service",
                "name": "Kasa switch manager",
                "online": True,
                "last_seen": now,
                "seconds_since_seen": 0,
                "recording_enabled": False,
                "recording_active": False,
                "recording_last_segment": None,
                "recording_seconds_since_event": None,
                "details": {
                  "host": switch_config.host if switch_config else switch_controller.host,
                    "schedule": "off at 12:00 AM and 12:00 PM",
                  "timezone": switch_config.timezone if switch_config else "America/Los_Angeles",
                  "gpio_pin": switch_config.gpio_pin if switch_config else 17,
                },
            }
        )
    return modules


@app.get("/api/system")
def system_stats(request: Request) -> dict:
    return get_system_stats(request.app.state.storage_path)


@app.get("/api/switch")
async def switch_status(request: Request) -> dict:
    controller = request.app.state.kasa_switch_controller
    if controller is None:
        raise HTTPException(status_code=404, detail="Switch not configured")
    try:
      switch_config = request.app.state.kasa_switch_config
      return {
        "name": controller.name,
        "on": await controller.get_state(),
        "timezone": switch_config.timezone if switch_config else "America/Los_Angeles",
      }
    except Exception as exc:
        log.warning("Could not read Kasa switch %s: %s", controller.name, exc)
        raise HTTPException(status_code=503, detail="Switch unavailable") from exc


@app.post("/api/switch/toggle")
async def toggle_switch(request: Request) -> dict:
    controller = request.app.state.kasa_switch_controller
    if controller is None:
        raise HTTPException(status_code=404, detail="Switch not configured")
    try:
      switch_config = request.app.state.kasa_switch_config
      return {
        "name": controller.name,
        "on": await controller.toggle(),
        "timezone": switch_config.timezone if switch_config else "America/Los_Angeles",
      }
    except Exception as exc:
        log.warning("Could not toggle Kasa switch %s: %s", controller.name, exc)
        raise HTTPException(status_code=503, detail="Switch unavailable") from exc


def _recording_path(request: Request, path: str) -> Path:
  root = request.app.state.recordings_path
  if root is None:
    raise HTTPException(status_code=404, detail="Recordings unavailable")
  try:
    root = Path(root).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
      raise HTTPException(status_code=404, detail="Not found")
    return target
  except (OSError, RuntimeError, ValueError):
    raise HTTPException(status_code=404, detail="Not found")


@app.get("/api/recordings")
def list_recordings(request: Request, path: str = "") -> dict:
  directory = _recording_path(request, path)
  if not directory.is_dir():
    raise HTTPException(status_code=404, detail="Directory not found")
  try:
    entries = []
    for entry in directory.iterdir():
      try:
        root = Path(request.app.state.recordings_path).resolve()
        _recording_path(request, str(entry.relative_to(root)))
        if entry.is_dir():
          entries.append({"name": entry.name, "type": "directory"})
        elif entry.is_file() and entry.suffix.lower() == ".mp4":
          entries.append({"name": entry.name, "type": "video"})
      except (HTTPException, OSError):
        continue
    entries.sort(key=lambda entry: (entry["type"] != "directory", entry["name"].casefold()))
    return {"path": path, "entries": entries}
  except OSError:
    raise HTTPException(status_code=503, detail="Recordings unavailable")


@app.get("/api/recordings/video")
def recording_video(request: Request, path: str):
  video = _recording_path(request, path)
  if video.suffix.lower() != ".mp4" or not video.is_file():
    raise HTTPException(status_code=404, detail="Video not found")
  return FileResponse(video, media_type="video/mp4")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX_HTML


@app.get("/devices", response_class=HTMLResponse)
def devices_page() -> str:
  return _DEVICES_HTML


@app.get("/recordings", response_class=HTMLResponse)
def recordings_page() -> str:
  return _RECORDINGS_HTML


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
  <meta name="color-scheme" content="light">
  <meta name="theme-color" content="#e7ebe8">
  <!-- Lets this be saved to a phone home screen and open chrome-less. -->
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <title>Dragonfly</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --ink: #202c29; --muted: #64716c; --paper: #f7f8f4;
      --line: #aab4ae; --green: #2a7764; --red: #c84b38; --yellow: #e7bd53;
    }
    * { box-sizing: border-box; }
    body {
      font-family: 'Space Grotesk', sans-serif;
      background: repeating-linear-gradient(0deg, #e7ebe8, #e7ebe8 3px, #e3e8e4 3px, #e3e8e4 4px);
      color: var(--ink); margin: 0;
      padding: max(1.5rem, env(safe-area-inset-top)) max(1.5rem, env(safe-area-inset-right))
               max(2rem, env(safe-area-inset-bottom)) max(1.5rem, env(safe-area-inset-left));
      -webkit-text-size-adjust: 100%;
    }
    .dashboard { max-width: 1440px; margin: 0 auto; }
    .masthead {
      display: flex; align-items: flex-end; justify-content: space-between; gap: 1rem;
      border-bottom: 3px solid var(--ink); padding-bottom: 1rem; margin-bottom: 1.5rem;
    }
    .brand { display: flex; align-items: center; gap: .85rem; min-width: 0; }
    .brand-mark {
      display: grid; place-items: center; width: 43px; height: 43px; flex: none;
      background: #e0eee2; border: 2px solid var(--ink);
    }
    .brand-mark svg { display: block; width: 35px; height: 35px; }
    .brand-copy { min-width: 0; }
    h1 { font-weight: 700; font-size: 1.65rem; line-height: 1; margin: 0; text-transform: uppercase; }
    .eyebrow, .masthead-note, .section-kicker {
      font: 500 .65rem 'DM Mono', monospace; text-transform: uppercase;
    }
    .eyebrow { color: var(--muted); margin-top: .35rem; }
    .masthead-note { color: var(--muted); text-align: right; }
    .section-heading {
      display: flex; align-items: baseline; justify-content: space-between; gap: 1rem;
      margin: 0 0 .75rem;
    }
    .section-heading h2 { font-size: 1rem; margin: 0; text-transform: uppercase; }
    .section-kicker { color: var(--muted); }
    .section-kicker a { color: #176957; text-decoration-thickness: 1px; text-underline-offset: 3px; }
    .section-kicker a:hover { color: var(--red); }
    .grid {
      display: grid; gap: 1rem; align-items: stretch; grid-auto-rows: 1fr;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 390px), 1fr));
    }
    .service-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .device-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .physical-grid { display: contents; }
    .switch-card {
      display: grid; grid-template-columns: minmax(0, 1fr); align-content: start; gap: .75rem;
      border-top-color: var(--green);
    }
    .switch-card button { width: 100%; }
    .switch-card button {
      min-width: 112px; min-height: 42px; border: 1px solid var(--ink); border-radius: 2px;
      padding: .6rem .9rem; background: var(--ink); color: #f4f6ee;
      font: 600 .85rem 'Space Grotesk', sans-serif; cursor: pointer;
    }
    .switch-card button:hover:not(:disabled) { background: var(--green); }
    .switch-card button:focus-visible { outline: 3px solid var(--yellow); outline-offset: 3px; }
    .switch-card button:disabled { opacity: .55; cursor: not-allowed; }
    .switch-error { grid-column: 1 / -1; color: var(--red); }
    .card {
      border: 1px solid var(--ink); border-top: 5px solid var(--ink); border-radius: 2px;
      padding: 1rem 1.15rem; background: var(--paper); min-width: 0;
      box-shadow: 3px 3px 0 #bec8c1;
    }
    .card .name { font-size: 1.1rem; font-weight: 700; margin-bottom: .85rem; overflow-wrap: anywhere; }
    .status { display: flex; align-items: center; gap: .55rem; font-size: .9rem; font-weight: 600; margin-top: .55rem; }
    .dot { width: 9px; height: 9px; border: 1px solid var(--ink); border-radius: 50%; display: inline-block; flex: none; }
    .dot.online { background: #69b68f; }
    .dot.offline { background: #ed6b50; }
    .meta {
      color: var(--muted); font: 400 .72rem/1.5 'DM Mono', monospace; margin-top: .3rem;
      overflow-wrap: anywhere;
    }
    .empty { color: var(--muted); }
    .sysbar {
      display: grid; grid-template-columns: repeat(5, minmax(0, 1fr));
      border: 1px solid var(--ink); background: var(--paper); margin-bottom: 2rem;
      font: 400 .72rem/1.5 'DM Mono', monospace;
    }
    .sysbar span { padding: .85rem 1rem; min-width: 0; overflow-wrap: anywhere; }
    .sysbar span + span { border-left: 1px solid var(--line); }
    .sysbar span:first-child { background: var(--ink); color: #f4f6ee; }
    .sysbar b { font-weight: 500; }
    .history-card {
      border-top: 1px solid var(--ink); border-bottom: 1px solid var(--line);
      padding: 1rem 0 1.25rem; margin-bottom: 2rem;
    }
    .history-card .name { font-size: .95rem; font-weight: 700; margin-bottom: .75rem; text-transform: uppercase; }
    .telemetry-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1.5rem; }
    .telemetry-panel { min-width: 0; }
    .telemetry-panel .name { margin-bottom: .2rem; }
    .telemetry-unit { color: var(--muted); font: 400 .65rem 'DM Mono', monospace; margin-bottom: .5rem; }
    .chart-wrap { position: relative; height: 180px; }
    .card a { display: inline-block; padding: .4rem 0; color: #176957; font-weight: 500; text-decoration-thickness: 1px; text-underline-offset: 3px; }
    .card a:hover { color: var(--red); }
    .card a:focus-visible { outline: 2px solid var(--red); outline-offset: 3px; }
    .meta.file { color: #3b4d45; }
    .filename {
      font-family: 'DM Mono', monospace; font-size: .72rem; color: var(--ink);
    }
    @media (max-width: 850px) {
      .telemetry-grid { grid-template-columns: 1fr; gap: 1.25rem; }
      .sysbar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .sysbar span + span { border-left: 0; border-top: 1px solid var(--line); }
      .sysbar span:nth-child(even) { border-left: 1px solid var(--line); }
      .sysbar span:nth-child(2) { border-top: 0; }
    }
    @media (max-width: 600px) {
      body { padding: max(1rem, env(safe-area-inset-top)) max(1rem, env(safe-area-inset-right))
                     max(1.5rem, env(safe-area-inset-bottom)) max(1rem, env(safe-area-inset-left)); }
      .grid { grid-template-columns: minmax(0, 1fr); }
      h1 { font-size: 1.35rem; }
      .masthead-note { display: none; }
      .masthead { margin-bottom: 1rem; }
      .sysbar { margin-bottom: 1.5rem; }
      .sysbar span { padding: .7rem .75rem; }
      .history-card { margin-bottom: 1.5rem; }
      .switch-card { grid-template-columns: minmax(0, 1fr); }
      .switch-card button { width: 100%; }
    }
  </style>
</head>
<body>
  <main class="dashboard">
  <header class="masthead">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
          <g stroke="#202c29" stroke-width="1.5" stroke-linejoin="round">
            <path d="M22 18C17 8 8 7 3 9c2 6 8 12 19 13Z" fill="#9d78c5"/>
            <path d="M26 18C31 8 40 7 45 9c-2 6-8 12-19 13Z" fill="#9d78c5"/>
            <path d="M22 23C13 20 7 22 4 28c6 3 13 2 19-2Z" fill="#c5a5dd"/>
            <path d="M26 23c9-3 15-1 18 5-6 3-13 2-19-2Z" fill="#c5a5dd"/>
            <path d="M22 26h4l-1 16-1 3-1-3Z" fill="#2a7764"/>
            <ellipse cx="24" cy="21" rx="4" ry="6" fill="#4b9b70"/>
            <circle cx="24" cy="13" r="3" fill="#2a7764"/>
          </g>
        </svg>
      </div>
      <div class="brand-copy"><h1>Dragonfly</h1><div class="eyebrow">Security &amp; monitoring</div></div>
    </div>
    <div class="masthead-note">System monitor<br>Local network</div>
  </header>
  <div id="sysbar" class="sysbar">Loading system stats&hellip;</div>
  <div class="history-card">
    <div class="telemetry-grid">
      <div class="telemetry-panel">
        <div class="name">CPU / memory</div><div class="telemetry-unit">Percent</div>
        <div class="chart-wrap"><canvas id="historyChart"></canvas></div>
      </div>
      <div class="telemetry-panel">
        <div class="name">Disk activity</div><div class="telemetry-unit">KiB/s &middot; all devices</div>
        <div class="chart-wrap"><canvas id="diskChart"></canvas></div>
      </div>
      <div class="telemetry-panel">
        <div class="name">Network traffic</div><div class="telemetry-unit">KiB/s &middot; all interfaces</div>
        <div class="chart-wrap"><canvas id="networkChart"></canvas></div>
      </div>
    </div>
  </div>
  <div class="section-heading"><h2>Service health</h2><span class="section-kicker">Portal &amp; system services</span></div>
  <div id="service-grid" class="grid service-grid"><p class="empty">Loading...</p></div>
  <div class="section-heading"><h2>Devices</h2><span class="section-kicker">Live status &middot; <a href="/devices">Open device page</a></span></div>
  <div id="grid" class="grid device-grid">
    <div id="switch-card" class="card switch-card">
      <div>
        <div class="name" id="switch-name">Bedroom light</div>
        <div class="status"><span id="switch-dot" class="dot"></span><span id="switch-state">Checking</span></div>
        <div class="meta" id="switch-schedule">Daily auto-off at 12:00 AM and 12:00 PM.</div>
      </div>
      <button id="switch-toggle" type="button" disabled>Loading</button>
      <div id="switch-error" class="meta switch-error" aria-live="polite"></div>
    </div>
    <div id="physical-grid" class="physical-grid"><p class="empty">Loading...</p></div>
  </div>
  </main>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js"></script>
  <script>
    // Purely client-side: polls /api/system every second and accumulates
    // points itself — nothing persisted on the Pi, resets whenever this page
    // reloads. Capped to the last 5 minutes so a tab left open for hours
    // doesn't grow without bound.
    const MAX_POINTS = 300;
    const liveData = { labels: [], cpu: [], mem: [], diskRead: [], diskWrite: [], netRecv: [], netSent: [] };
    let charts = null;
    let previousIo = null;

    function createChart(id, series, max = undefined) {
      return new Chart(document.getElementById(id).getContext('2d'), {
        type: 'line',
        data: {
          labels: liveData.labels,
          datasets: series.map(([label, data, color]) => ({
            label, data, borderColor: color, backgroundColor: 'transparent',
            tension: .2, pointRadius: 0, borderWidth: 2,
          })),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          scales: {
            y: { min: 0, max, ticks: { color: '#64716c', maxTicksLimit: 5 }, grid: { color: '#c9d1cb' } },
            x: { ticks: { color: '#64716c', maxTicksLimit: 5, maxRotation: 0 }, grid: { display: false } },
          },
          plugins: { legend: { labels: { color: '#202c29', boxWidth: 12 } } },
        },
      });
    }

    function initCharts() {
      charts = [
        createChart('historyChart', [['CPU %', liveData.cpu, '#c84b38'], ['Mem %', liveData.mem, '#2a7764']], 100),
        createChart('diskChart', [['Read', liveData.diskRead, '#9a5d21'], ['Write', liveData.diskWrite, '#4d716d']]),
        createChart('networkChart', [['Receive', liveData.netRecv, '#365d9c'], ['Send', liveData.netSent, '#a24f69']]),
      ];
    }

    function ioRate(current, previous, elapsed) {
      if (current == null || previous == null || elapsed <= 0 || current < previous) return null;
      return (current - previous) / elapsed / 1024;
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

      const elapsed = previousIo ? s.io_timestamp - previousIo.io_timestamp : 0;
      liveData.labels.push(new Date().toLocaleTimeString([], {hour12: false}));
      liveData.cpu.push(s.cpu_percent);
      liveData.mem.push(s.mem_percent);
      liveData.diskRead.push(ioRate(s.disk_read_bytes, previousIo?.disk_read_bytes, elapsed));
      liveData.diskWrite.push(ioRate(s.disk_write_bytes, previousIo?.disk_write_bytes, elapsed));
      liveData.netRecv.push(ioRate(s.network_received_bytes, previousIo?.network_received_bytes, elapsed));
      liveData.netSent.push(ioRate(s.network_sent_bytes, previousIo?.network_sent_bytes, elapsed));
      if (!previousIo || elapsed > 0) previousIo = s;
      if (liveData.labels.length > MAX_POINTS) {
        Object.values(liveData).forEach(values => values.shift());
      }
      if (!charts) initCharts();
      charts.forEach(chart => chart.update('none'));
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
      host: 'host', port: 'port', mount_point: 'mount', path: 'recordings', error: 'error',
      schedule: 'schedule', timezone: 'timezone', gpio_pin: 'button GPIO',
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
    // Shown on their own line under the recording status, so don't repeat
    // them in the generic details list below it.
    const RECORDING_DETAIL_KEYS = ['newest_segment', 'newest_size_bytes', 'newest_age_s'];

    // <date>/<hour>/<n>.mp4 — enough to locate the file on disk without
    // the full mount path eating the card.
    function segmentLabel(path) {
      return String(path).split('/').slice(-3).join('/');
    }

    function escapeHtml(str) {
      if (str === null || str === undefined) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function renderDetails(details, skip = []) {
      if (!details) return '';
      const keys = Object.keys(details).filter(k => !skip.includes(k)).sort((a, b) => {
        const ia = DETAIL_ORDER.indexOf(a), ib = DETAIL_ORDER.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      });
      const rows = keys.map(k => {
        const v = fmtDetail(k, details[k]);
        if (v === null) return '';
        const label = DETAIL_LABELS[k] || k.replace(/_/g, ' ');
        const bad = k === 'error' || (k === 'full' && details[k]);
        return `<div class="meta"${bad ? ' style="color:#e74c3c"' : ''}>${escapeHtml(label)}: ${escapeHtml(v)}</div>`;
      }).filter(Boolean);
      return rows.join('');
    }

    function detailKeysToSkip(module) {
      const skip = [...RECORDING_DETAIL_KEYS];
      const details = module.details || {};
      if (module.module_type === 'storage' && module.online) {
        skip.push('full', 'healthy', 'mounted', 'root', 'used_bytes', 'total_bytes');
      }
      if (module.module_type === 'capture_process' && module.online) {
        skip.push('state', 'service');
      }
      if (module.module_type.includes('camera') && module.online) {
        skip.push('host', 'port', 'stale_after_s');
      }
      if (details.capture_report === 'recording' && module.recording_active) {
        skip.push('capture_report');
      }
      return skip;
    }

    function renderModuleCard(m) {
      return `
        <div class="card">
          <div class="name">${escapeHtml(m.name)}</div>
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
          ${m.recording_last_segment ? `
          <div class="meta file">
            <span class="filename">${escapeHtml(segmentLabel(m.recording_last_segment))}</span>
            ${m.details && m.details.newest_size_bytes
              ? ` &middot; ${fmtBytes(m.details.newest_size_bytes)}` : ''}
            ${m.details && m.details.newest_age_s !== undefined
              ? ` &middot; written ${formatAgo(m.details.newest_age_s)} ago` : ''}
          </div>` : ''}
          ${m.recording_seconds_since_event === null
            ? '<div class="meta">no recording activity yet</div>' : ''}` : ''}
          ${renderDetails(m.details, detailKeysToSkip(m))}
          ${m.module_type.includes('camera') && m.module_id.charAt(0) !== '_'
            ? `<div class="meta"><a href="/live/${encodeURIComponent(m.module_id)}">Live view &rarr;</a></div>` : ''}
          ${m.module_type.includes('camera') && m.module_id.charAt(0) !== '_'
            ? `<div class="meta"><a href="/recordings?path=${encodeURIComponent(m.module_id)}">Browse recordings &rarr;</a></div>` : ''}
        </div>
      `;
    }

    async function refresh() {
      const res = await fetch('/api/modules');
      const modules = await res.json();
      const serviceTypes = new Set(['capture_process', 'storage', 'service']);
      const services = modules.filter(m => serviceTypes.has(m.module_type));
      const devices = modules.filter(m => !serviceTypes.has(m.module_type));
      document.getElementById('service-grid').innerHTML = services.length
        ? services.map(renderModuleCard).join('')
        : '<p class="empty">No services reporting yet.</p>';
      document.getElementById('physical-grid').innerHTML = devices.length
        ? devices.map(renderModuleCard).join('')
        : '<p class="empty">No devices reporting yet.</p>';
    }
    refresh();
    setInterval(refresh, 5000);

    const switchCard = document.getElementById('switch-card');
    const switchName = document.getElementById('switch-name');
    const switchState = document.getElementById('switch-state');
    const switchDot = document.getElementById('switch-dot');
    const switchSchedule = document.getElementById('switch-schedule');
    const switchToggle = document.getElementById('switch-toggle');
    const switchError = document.getElementById('switch-error');
    let switchBusy = false;
    let switchAvailable = false;

    function renderSwitch(state) {
      switchAvailable = true;
      switchCard.hidden = false;
      switchName.textContent = state.name;
      switchState.textContent = state.on ? 'ON' : 'OFF';
      switchDot.className = `dot ${state.on ? 'online' : 'offline'}`;
      switchSchedule.textContent = `Daily auto-off at 12:00 AM and 12:00 PM (${state.timezone}).`;
      switchToggle.textContent = state.on ? 'Turn off' : 'Turn on';
      switchToggle.disabled = switchBusy;
      switchError.textContent = '';
    }

    async function refreshSwitch() {
      try {
        const response = await fetch('/api/switch', { cache: 'no-store' });
        if (response.status === 404) {
          switchAvailable = false;
          switchCard.hidden = true;
          return;
        }
        if (!response.ok) throw new Error('Switch unavailable');
        renderSwitch(await response.json());
      } catch {
        switchAvailable = false;
        switchCard.hidden = false;
        switchState.textContent = 'UNAVAILABLE';
        switchDot.className = 'dot offline';
        switchToggle.textContent = 'Unavailable';
        switchToggle.disabled = true;
        switchError.textContent = 'Could not read the switch state.';
      }
    }

    switchToggle.addEventListener('click', async () => {
      if (switchBusy || !switchAvailable) return;
      switchBusy = true;
      switchToggle.disabled = true;
      switchError.textContent = '';
      try {
        const response = await fetch('/api/switch/toggle', { method: 'POST' });
        if (!response.ok) throw new Error('Switch unavailable');
        renderSwitch(await response.json());
      } catch {
        switchError.textContent = 'Could not toggle the switch.';
        await refreshSwitch();
      } finally {
        switchBusy = false;
        switchToggle.disabled = !switchAvailable;
      }
    });
    refreshSwitch();
    setInterval(refreshSwitch, 1000);
  </script>
</body>
</html>"""

_DEVICES_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="light">
  <meta name="theme-color" content="#e7ebe8">
  <title>Dragonfly &mdash; Devices</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --ink: #202c29; --muted: #64716c; --paper: #f7f8f4;
      --line: #aab4ae; --green: #2a7764; --red: #c84b38;
    }
    * { box-sizing: border-box; }
    body {
      font-family: 'Space Grotesk', sans-serif;
      background: repeating-linear-gradient(0deg, #e7ebe8, #e7ebe8 3px, #e3e8e4 3px, #e3e8e4 4px);
      color: var(--ink); margin: 0;
      padding: max(1.5rem, env(safe-area-inset-top)) max(1.5rem, env(safe-area-inset-right))
               max(2rem, env(safe-area-inset-bottom)) max(1.5rem, env(safe-area-inset-left));
      -webkit-text-size-adjust: 100%;
    }
    main { max-width: 1440px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; border-bottom: 3px solid var(--ink); padding-bottom: 1rem; }
    h1 { margin: 0; font-size: 1.65rem; text-transform: uppercase; }
    a { color: #176957; text-underline-offset: 3px; }
    a:hover { color: var(--red); }
    .section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin: 1.5rem 0 .75rem; }
    .section-heading h2 { margin: 0; font-size: 1rem; text-transform: uppercase; }
    .section-kicker, .meta { color: var(--muted); font: 400 .72rem/1.5 'DM Mono', monospace; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 390px), 1fr)); gap: 1rem; align-items: stretch; }
    .card { min-width: 0; border: 1px solid var(--ink); border-top: 5px solid var(--ink); border-radius: 2px; padding: 1rem 1.15rem; background: var(--paper); box-shadow: 3px 3px 0 #bec8c1; }
    .switch-card { border-top-color: var(--green); display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 1rem; }
    .name { margin-bottom: .85rem; font-size: 1.1rem; font-weight: 700; overflow-wrap: anywhere; }
    .status { display: flex; align-items: center; gap: .55rem; margin-top: .55rem; font-size: .9rem; font-weight: 600; }
    .dot { width: 9px; height: 9px; flex: none; display: inline-block; border: 1px solid var(--ink); border-radius: 50%; }
    .dot.online { background: #69b68f; }
    .dot.offline { background: #ed6b50; }
    .meta { margin-top: .3rem; overflow-wrap: anywhere; }
    .file { color: var(--ink); }
    button { min-width: 112px; min-height: 42px; border: 1px solid var(--ink); border-radius: 2px; padding: .6rem .9rem; background: var(--ink); color: #f4f6ee; font: 600 .85rem 'Space Grotesk', sans-serif; cursor: pointer; }
    button:hover:not(:disabled) { background: var(--green); }
    button:focus-visible { outline: 3px solid #e7bd53; outline-offset: 3px; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .error { grid-column: 1 / -1; color: var(--red); }
    .empty { color: var(--muted); }
    @media (max-width: 850px) {
      .switch-card { grid-template-columns: minmax(0, 1fr); align-content: start; }
      .switch-card button { width: 100%; }
    }
    @media (max-width: 600px) {
      body { padding: max(1rem, env(safe-area-inset-top)) max(1rem, env(safe-area-inset-right)) max(1.5rem, env(safe-area-inset-bottom)) max(1rem, env(safe-area-inset-left)); }
      header { align-items: flex-start; flex-direction: column; }
      h1 { font-size: 1.35rem; }
    }
  </style>
</head>
<body>
  <main>
    <header><h1>Devices</h1><a href="/">&larr; Dashboard</a></header>
    <div class="section-heading"><h2>Connected devices</h2><span class="section-kicker">Live status</span></div>
    <div class="grid">
      <article id="switch-card" class="card switch-card">
        <div>
          <div class="name" id="switch-name">Bedroom light</div>
          <div class="status"><span id="switch-dot" class="dot"></span><span id="switch-state">Checking</span></div>
          <div class="meta" id="switch-schedule">Daily auto-off at 12:00 AM and 12:00 PM.</div>
        </div>
        <button id="switch-toggle" type="button" disabled>Loading</button>
        <div id="switch-error" class="meta error" aria-live="polite"></div>
      </article>
      <div id="physical-grid" style="display:contents"><p class="empty">Loading devices...</p></div>
    </div>
  </main>
  <script>
    const switchCard = document.querySelector('#switch-card');
    const switchName = document.querySelector('#switch-name');
    const switchState = document.querySelector('#switch-state');
    const switchDot = document.querySelector('#switch-dot');
    const switchSchedule = document.querySelector('#switch-schedule');
    const switchToggle = document.querySelector('#switch-toggle');
    const switchError = document.querySelector('#switch-error');
    const physicalGrid = document.querySelector('#physical-grid');
    let switchBusy = false;
    let switchAvailable = false;

    function escapeHtml(value) {
      return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function renderSwitch(state) {
      switchAvailable = true;
      switchName.textContent = state.name;
      switchState.textContent = state.on ? 'ON' : 'OFF';
      switchDot.className = `dot ${state.on ? 'online' : 'offline'}`;
      switchSchedule.textContent = `Daily auto-off at 12:00 AM and 12:00 PM (${state.timezone}).`;
      switchToggle.textContent = state.on ? 'Turn off' : 'Turn on';
      switchToggle.disabled = switchBusy;
      switchError.textContent = '';
    }

    async function refreshSwitch() {
      try {
        const response = await fetch('/api/switch', { cache: 'no-store' });
        if (!response.ok) throw new Error('Switch unavailable');
        renderSwitch(await response.json());
      } catch {
        switchAvailable = false;
        switchState.textContent = 'UNAVAILABLE';
        switchDot.className = 'dot offline';
        switchToggle.textContent = 'Unavailable';
        switchToggle.disabled = true;
        switchError.textContent = 'Could not read the switch state.';
      }
    }

    switchToggle.addEventListener('click', async () => {
      if (switchBusy || !switchAvailable) return;
      switchBusy = true;
      switchToggle.disabled = true;
      switchError.textContent = '';
      try {
        const response = await fetch('/api/switch/toggle', { method: 'POST' });
        if (!response.ok) throw new Error('Switch unavailable');
        renderSwitch(await response.json());
      } catch {
        switchError.textContent = 'Could not toggle the switch.';
        await refreshSwitch();
      } finally {
        switchBusy = false;
        switchToggle.disabled = !switchAvailable;
      }
    });

    async function refreshDevices() {
      try {
        const response = await fetch('/api/modules', { cache: 'no-store' });
        if (!response.ok) throw new Error('Devices unavailable');
        const modules = await response.json();
        const devices = modules.filter(module =>
          !['_capture', '_storage', '_kasa_switch'].includes(module.module_id)
          && !['service', 'storage', 'capture_process'].includes(module.module_type)
        );
        physicalGrid.innerHTML = devices.length ? devices.map(module => {
          const isCamera = module.module_type.includes('camera');
          const recording = module.recording_enabled
            ? `<div class="status"><span class="dot ${module.recording_active ? 'online' : 'offline'}"></span>${module.recording_active ? 'Recording to disk' : 'Not recording'}</div>`
            : '';
          const filename = module.recording_last_segment
            ? `<div class="meta file">${escapeHtml(String(module.recording_last_segment).split('/').slice(-3).join('/'))}</div>`
            : '';
          const liveLink = isCamera
            ? `<div class="meta"><a href="/live/${encodeURIComponent(module.module_id)}">Live view &rarr;</a></div>`
            : '';
          const recordingsLink = isCamera
            ? `<div class="meta"><a href="/recordings?path=${encodeURIComponent(module.module_id)}">Browse recordings &rarr;</a></div>`
            : '';
          return `<article class="card"><div class="name">${escapeHtml(module.name)}</div>
            <div class="status"><span class="dot ${module.online ? 'online' : 'offline'}"></span>${module.online ? 'Online' : 'Offline'}</div>
            ${recording}${filename}${liveLink}${recordingsLink}</article>`;
        }).join('') : '<p class="empty">No devices reporting yet.</p>';
      } catch {
        physicalGrid.innerHTML = '<p class="empty">Unable to load devices.</p>';
      }
    }

    refreshSwitch();
    refreshDevices();
    window.setInterval(refreshSwitch, 1000);
    window.setInterval(refreshDevices, 5000);
  </script>
</body>
</html>"""

_RECORDINGS_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="dark">
  <meta name="theme-color" content="#111">
  <title>Dragonfly &mdash; Recordings</title>
  <style>
    * { box-sizing: border-box; }
    body {
      background: #111; color: #eee; font-family: -apple-system, sans-serif; margin: 0;
      padding: 1.5rem max(1.5rem, env(safe-area-inset-right))
               max(1.5rem, env(safe-area-inset-bottom)) max(1.5rem, env(safe-area-inset-left));
    }
    main { max-width: 1000px; margin: auto; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
    h1 { font-size: 1.5rem; margin: 0; }
    a { color: #4da3ff; }
    nav { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; margin: 1.5rem 0 1rem; overflow-wrap: anywhere; }
    nav a { padding: .3rem 0; }
    video { display: block; width: 100%; max-height: 65vh; background: #000; }
    #player[hidden] { display: none; }
    #player { margin-bottom: 1.5rem; }
    #playing { overflow-wrap: anywhere; }
    #status { color: #aaa; }
    ul { list-style: none; margin: 0; padding: 0; border-top: 1px solid #333; }
    li { border-bottom: 1px solid #333; }
    li a { display: block; padding: .85rem .25rem; overflow-wrap: anywhere; text-decoration: none; }
    li a:hover, li a:focus-visible { background: #222; text-decoration: underline; }
    .kind { color: #999; display: inline-block; width: 4.5rem; font-size: .8rem; }
    @media (max-width: 600px) {
      body { padding: 1rem max(1rem, env(safe-area-inset-right))
                     max(1rem, env(safe-area-inset-bottom)) max(1rem, env(safe-area-inset-left)); }
      h1 { font-size: 1.25rem; }
    }
  </style>
</head>
<body>
  <main>
    <header><h1>Recordings</h1><a href="/">&larr; Dashboard</a></header>
    <nav id="breadcrumbs" aria-label="Recording folders"></nav>
    <section id="player" hidden>
      <p id="playing"></p>
      <video id="video" controls autoplay playsinline></video>
      <p id="video-error" role="status"></p>
    </section>
    <p id="status" role="status">Loading&hellip;</p>
    <ul id="entries"></ul>
  </main>
  <script>
    const path = new URLSearchParams(location.search).get('path') || '';
    const folderUrl = value => '/recordings?' + new URLSearchParams({path: value});
    const videoUrl = value => '/api/recordings/video?' + new URLSearchParams({path: value});
    const joinPath = (parent, name) => parent ? parent + '/' + name : name;
    const breadcrumbs = document.getElementById('breadcrumbs');
    const rootLink = document.createElement('a');
    rootLink.href = folderUrl('');
    rootLink.textContent = 'Recordings';
    breadcrumbs.append(rootLink);
    let current = '';
    for (const part of path.split('/').filter(Boolean)) {
      current = joinPath(current, part);
      breadcrumbs.append(document.createTextNode(' / '));
      const link = document.createElement('a');
      link.href = folderUrl(current);
      link.textContent = part;
      breadcrumbs.append(link);
    }
    const video = document.getElementById('video');
    video.addEventListener('error', () => {
      document.getElementById('video-error').textContent = 'Unable to play this recording. It may still be recording or no longer available.';
    });
    async function load() {
      const status = document.getElementById('status');
      try {
        const response = await fetch('/api/recordings?' + new URLSearchParams({path}));
        if (!response.ok) throw new Error('Unable to load this folder.');
        const data = await response.json();
        data.entries.sort((a, b) => {
          if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
          return a.name.localeCompare(b.name, undefined, {numeric: true}) * (a.type === 'directory' ? -1 : 1);
        });
        status.textContent = data.entries.length ? '' : 'No recordings in this folder.';
        const list = document.getElementById('entries');
        for (const entry of data.entries) {
          const item = document.createElement('li');
          const link = document.createElement('a');
          const target = joinPath(path, entry.name);
          link.href = entry.type === 'directory' ? folderUrl(target) : videoUrl(target);
          const kind = document.createElement('span');
          kind.className = 'kind';
          kind.textContent = entry.type === 'directory' ? 'Folder' : 'Video';
          link.append(kind, document.createTextNode(entry.name));
          if (entry.type === 'video') link.addEventListener('click', event => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            document.getElementById('player').hidden = false;
            document.getElementById('playing').textContent = entry.name;
            document.getElementById('video-error').textContent = '';
            video.src = link.href;
            video.play().catch(() => {});
          });
          item.append(link);
          list.append(item);
        }
      } catch (error) {
        status.textContent = error.message || 'Unable to load recordings.';
      }
    }
    load();
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
