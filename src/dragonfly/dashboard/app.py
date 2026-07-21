"""Local-LAN monitoring dashboard (FastAPI).

Bound to the Pi's LAN interface only (config.dashboard.bind_host) — see
docs/network.md. Reads live state from app.state.registry / app.state.camera_config,
which hub/main.py sets before starting uvicorn.

For local dev without the full hub running:

    uvicorn dragonfly.dashboard.app:app --reload

(will show "No modules reporting yet" since app.state.registry is None until
main.py wires it up.)
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="Dragonfly")

# Set by hub/main.py at startup. Left as None/{} so the app still imports
# (and /health works) if run standalone.
app.state.registry = None
app.state.camera_config = {}


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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX_HTML


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
  </style>
</head>
<body>
  <h1>Dragonfly</h1>
  <div id="grid" class="grid"><p class="empty">Loading...</p></div>
  <script>
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
        </div>
      `).join('');
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>"""
