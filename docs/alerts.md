# Notifications

Dragonfly can send ntfy push notifications when a camera is unreachable,
recording stops, the capture service is offline, or recording storage is
unavailable or full. It also alerts when CPU or memory usage stays at or above
90% for one minute. Notifications are disabled until configured.

## Set up ntfy

Install the [ntfy app](https://docs.ntfy.sh/subscribe/phone/) on your phone.
Choose a long, random topic on `https://ntfy.sh`, or use an HTTPS self-hosted
ntfy server. The public ntfy.sh service can read alert contents, and anyone who
knows an unauthenticated topic URL can read and publish messages to it. Keep
the URL secret, or configure an authenticated topic and access token instead.

On Linux, create a root-only environment file if it does not already exist:

```bash
sudo install -d -m 755 /etc/dragonfly
sudo test -e /etc/dragonfly/alerts.env || sudo install -m 600 /dev/null /etc/dragonfly/alerts.env
sudo chmod 600 /etc/dragonfly/alerts.env
sudo nano /etc/dragonfly/alerts.env
```

Set the topic URL and, if required, an ntfy access token. Do not share or commit
these values.

```ini
DRAGONFLY_NTFY_URL=https://ntfy.sh/REPLACE_WITH_RANDOM_TOPIC
DRAGONFLY_NTFY_TOKEN=REPLACE_WITH_TOKEN_IF_NEEDED
```

Omit the token line for an unauthenticated topic. Subscribe to the health topic
in the ntfy app. For separate camera person notifications, also set:

```ini
DRAGONFLY_NTFY_PERSON_URL=https://ntfy.sh/REPLACE_WITH_RANDOM_PERSON_TOPIC
DRAGONFLY_NTFY_PERSON_TOKEN=REPLACE_WITH_TOKEN_IF_NEEDED
```

Omit the person token line for an unauthenticated topic, and subscribe to that
topic separately in the ntfy app. For self-hosted ntfy, iPhone notifications require an upstream
push relay for timely background delivery; see the
[ntfy iOS documentation](https://docs.ntfy.sh/config/#ios-instant-notifications).

The `dfly-watchdog` and `dfly-detect` Linux units load this environment file
directly. Restart those units after changing topics or tokens:

```bash
sudo systemctl restart dfly-watchdog dfly-detect
```

For a system checkout, send a test without printing the topic or token:

```bash
sudo systemd-run --wait --pipe --collect \
  --property=EnvironmentFile=/etc/dragonfly/alerts.env \
  --property="User=$USER" \
  --property="WorkingDirectory=$PWD" \
  "$PWD/.venv/bin/python" -m dragonfly.common.alerts --test
```

The test reports whether ntfy accepted the request; check the phone for actual
delivery. Check `journalctl -u dfly-watchdog -u dfly-detect` for configuration
or delivery errors. An accepted request is not a guarantee of push delivery.

## Behavior and limitations

- An outage must persist for 30 seconds before an alert is sent. A continuing
  outage produces one notification; a new outage after recovery can produce another.
- CPU and memory are sampled on each watchdog pass. Each must remain at or
  above 90% for one minute of consecutive samples; a lower reading or a long
  sampling gap resets its timer. These alerts do not have an extra 30-second delay.
- Camera notifications require Reolink AI people detection; generic motion alone
  does not send an alert. Each transition into the people-detected state sends
  a notification. Generic motion recording markers remain unchanged.
- Failed sends are retried every 60 seconds while the failure persists. A
  timeout can result in a duplicate if ntfy accepted the first request.
- Recording checks allow the configured segment length plus watchdog grace
  before considering a recording stale. A storage outage is alerted as storage,
  without an extra recording alert while the drive is unavailable.
- Alert state is in memory, so restarting `dfly-watchdog` during an outage can
  produce another notification. Recovery notifications are not sent.
- The relevant alert service, network, and ntfy must be available to send alerts.
  This cannot alert if the entire host is down; use external monitoring for that.