# Deployment: Mac → GitHub → Pi over Tailscale

## Why Tailscale

The automation network is isolated and the home router has no inbound port
forwards (see [`network.md`](network.md)). Tailscale gives the Pi a private,
outbound-only VPN connection to a mesh network that only your devices are on,
so you can `ssh` into the Pi from anywhere without opening anything on your
home network.

## One-time setup

### On the Raspberry Pi

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname=phila
```

`--ssh` turns on Tailscale SSH, so you don't need to separately manage SSH
keys — access is controlled by your Tailscale account/ACLs.

### On your Mac

1. Install Tailscale (https://tailscale.com/download/mac) and log into the
   same account/tailnet as the Pi.
2. Confirm you can reach it:

   ```bash
   tailscale ping phila
   ssh charan@phila
   ```

MagicDNS (on by default) means `phila` resolves without remembering an IP.

### On the Pi: install the project

```bash
git clone https://github.com/<your-username>/dragonfly.git ~/dragonfly
cd ~/dragonfly
bash deploy/install_pi.sh
```

`deploy/install_pi.sh` creates a virtualenv, installs the project
(`pip install -e ".[camera]"`), copies `config/dragonfly.example.yaml` to
`config/dragonfly.yaml` if it doesn't exist yet, mounts/checks the external
HDD, and installs+enables the two systemd services
(`deploy/dragonfly-capture.service`, `deploy/dragonfly-portal.service`) — see
[`architecture.md`](architecture.md) for why capture and portal are split
into independent processes.

## Everyday workflow: shipping a change

1. Write and test code on the Mac as usual.
2. Commit and push to GitHub:

   ```bash
   git add -A
   git commit -m "..."
   git push
   ```

3. Deploy to the Pi over Tailscale:

   ```bash
   bash deploy/deploy.sh            # restart both capture + portal
   bash deploy/deploy.sh portal     # restart only the dashboard
   bash deploy/deploy.sh capture    # restart only camera capture
   ```

   This runs `ssh charan@phila` and, on the Pi: `git pull`,
   `pip install -e ".[camera]"` (picks up new dependencies), and
   `sudo systemctl restart` the selected service(s) (`dragonfly-capture`,
   `dragonfly-portal`, or both — default is both). It then tails the
   restarted service(s)' logs for a few seconds so you can see the new
   version came up cleanly. Use `portal`/`capture` after a change scoped to
   just the dashboard or just capture, so the other one keeps running
   uninterrupted (recording/live view for `portal` changes, the dashboard
   for `capture` changes).

No manual steps on the Pi are needed for a routine update — `deploy.sh` from
the Mac is the whole workflow.

## Rollback

```bash
ssh charan@phila "cd ~/dragonfly && git log --oneline -5"
ssh charan@phila "cd ~/dragonfly && git checkout <commit> && sudo systemctl restart dragonfly-capture dragonfly-portal"
```

## Logs / debugging

```bash
ssh charan@phila "journalctl -u dragonfly-capture -u dragonfly-portal -f"
```
