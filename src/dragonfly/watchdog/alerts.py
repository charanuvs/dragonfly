from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


log = logging.getLogger("dragonfly.alerts")


class Ntfy:
    def __init__(self, url: str, token: str = "") -> None:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.netloc or not parts.path.strip("/")
                or parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError("Invalid ntfy topic URL")
        self.url = url
        self.token = token

    @classmethod
    def from_environment(cls) -> Ntfy | None:
        url = os.environ.get("DRAGONFLY_NTFY_URL", "").strip()
        token = os.environ.get("DRAGONFLY_NTFY_TOKEN", "").strip()
        if not url and not token:
            return None
        if not url:
            raise ValueError("ntfy topic URL is missing")
        return cls(url, token)

    def send(self, message: str) -> None:
        headers = {"Title": "Dragonfly alert", "Priority": "high"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            self.url,
            data=message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise OSError("ntfy rejected the message")


class AlertManager:
    def __init__(self, sender: Ntfy, delay_s: float = 30, retry_s: float = 60) -> None:
        self.sender = sender
        self.delay_s = delay_s
        self.retry_s = retry_s
        self._states: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alerts")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=12)

    def observe(self, key: str, failed: bool, message: str) -> None:
        with self._lock:
            if not failed:
                self._states.pop(key, None)
            elif key not in self._states:
                self._states[key] = {
                    "message": message,
                    "since": time.monotonic(),
                    "next_attempt": 0.0,
                    "sent": False,
                }

    def _loop(self) -> None:
        while not self._stop.wait(1):
            self._process()

    def _process(self) -> None:
        now = time.monotonic()
        with self._lock:
            pending = [
                (key, state) for key, state in self._states.items()
                if not state["sent"] and now >= state["since"] + self.delay_s
                and now >= state["next_attempt"]
            ]
        for key, state in pending:
            if self._stop.is_set():
                break
            with self._lock:
                if self._states.get(key) is not state:
                    continue
            try:
                self.sender.send(state["message"])
            except Exception as error:
                log.warning("Alert delivery failed (%s); will retry", type(error).__name__)
                with self._lock:
                    if self._states.get(key) is state:
                        state["next_attempt"] = time.monotonic() + self.retry_s
            else:
                with self._lock:
                    if self._states.get(key) is state:
                        state["sent"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a Dragonfly ntfy test alert")
    parser.add_argument("--test", action="store_true", required=True)
    parser.parse_args()
    try:
        sender = Ntfy.from_environment()
        if sender is None:
            print("ntfy alerts are not configured")
            return 1
        sender.send("Dragonfly: notification test")
    except Exception as error:
        print(f"Notification test failed ({type(error).__name__})")
        return 1
    print("Notification accepted by ntfy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())