# Network design

## Goal

- Sensors/cameras live on a network with no route to the internet.
- The Raspberry Pi hub is the only device that bridges that network and the
  internet-connected home LAN.
- The dashboard is reachable from the home LAN (phone, Mac, etc.) but never
  from the internet.

## Physical setup

```
Internet
   │
   │ (WAN)
┌──▼───────────┐
│ TP-Link      │
│ Archer modem │
└──┬───────────┘
   │
┌──▼────────────────────────────┐
│ TP-Link Deco mesh (router)     │
│                                 │
│  Main network / LAN  ───────────┼──► Mac, phones, laptops (internet + LAN)
│  IoT network (SSID)  ───────────┼──► sensors, cameras (isolated segment)
└──┬─────────────────────────────┘
   │  (Ethernet, main LAN)
┌──▼───────────────────┐     Wi-Fi (IoT SSID)
│  Raspberry Pi (hub)   ├───────────────────► ESP32 / Pi Zero sensors, IP cams
│  - eth0: main LAN      │
│  - wlan0: IoT network  │
│  - external HDD        │
└────────────────────────┘
```

The Pi is **dual-homed**: wired Ethernet to the Deco's main network (internet
+ reachable from your other LAN devices for the dashboard), and Wi-Fi joined
to the Deco's IoT/guest SSID (talks to sensors/cameras only). No other device
needs to straddle both networks.

## Deco configuration (do this in the Deco app)

1. **Create the IoT/guest network.** In the Deco app: *More → IoT Network*
   (or *Guest Network*, depending on your Deco firmware version) → enable it,
   give it its own SSID/password (e.g. `dragonfly-iot`).
2. **Turn off "allow guests to access my local network"** (or equivalent) so
   IoT-network clients cannot initiate connections to your main LAN clients.
   This is the isolation boundary between sensors and your phones/laptops.
3. **Join the Pi's Wi-Fi to that SSID** in addition to its wired connection
   to the main network. Join every sensor/camera to that same SSID.
4. **Restrict internet access for the IoT SSID's clients**, if your Deco
   firmware supports it (`Access Control` / `Parental Controls` → select the
   sensor devices → block/pause internet for them). Note the caveat below.

### Caveat: Deco's IoT network isolates from your LAN, not necessarily from the internet

Deco's IoT/guest network is designed to keep IoT devices from talking to your
main LAN clients — it does not reliably guarantee those devices have *no*
path to the internet; that depends on your firmware version and whether
per-device access control is available and applied to every sensor.

Two ways to close that gap, in increasing order of effort:

1. **Apply per-device "block internet" access control** to every
   sensor/camera in the Deco app, if your firmware exposes it. Low effort,
   but easy to forget for a new device.
2. **Let the Pi enforce it instead of relying on the router.** Since sensors
   can only usefully reach the hub (they speak MQTT to the Pi, nothing else),
   this is mostly moot in practice — but if you want a hard guarantee later,
   the fallback is a small managed switch with real VLANs (or a second
   router in AP-only mode with no WAN), which was considered and set aside
   for now in favor of getting started quickly. Revisit if you add
   internet-capable sensors you don't fully trust.

## IP / addressing

Keep it simple to start:

- Main LAN: whatever the Deco already hands out (e.g. `192.168.68.0/24`).
- IoT network: Deco assigns its own subnet automatically when you enable it
  (commonly a separate range like `192.168.69.0/24`) — no manual config
  needed.
- Give the Pi a **DHCP reservation** on the main LAN in the Deco app so its
  LAN IP (and therefore the dashboard URL) never changes.

## Dashboard exposure

The dashboard (`src/dragonfly/dashboard`) binds to the Pi's main-LAN
interface only (`eth0`'s address), on a port not forwarded anywhere on the
Archer modem. It is reachable as `http://<pi-lan-ip>:8000` from any device on
your home network, and not reachable from the internet, ever, by construction
(no port forward = no path in).

## Remote access for development

Internet exposure for the Pi itself is limited to a single outbound
Tailscale connection — see [`deployment.md`](deployment.md). No inbound ports
are opened on the Archer modem or the Deco for this project.
