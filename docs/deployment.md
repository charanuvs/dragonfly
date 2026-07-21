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
sudo tailscale up --ssh --hostname=dragonfly-hub
```

`--ssh` turns on Tailscale SSH, so you don't need to separately manage SSH
keys — access is controlled by your Tailscale account/ACLs.

### On your Mac

1. Install Tailscale (https://tailscale.com/download/mac) and log into the
   same account/tailnet as the Pi.
2. Confirm you can reach it:

   ```bash
   tailscale ping dragonfly-hub
   ssh pi@dragonfly-hub
   ```

MagicDNS (on by default) means `dragonfly-hub` resolves without remembering
an IP.

### On the Pi: install the project

```bash
git clone https://github.com/<your-username>/dragonfly.git ~/dragonfly
cd ~/dragonfly
bash deploy/install_pi.sh
```

`deploy/install_pi.sh` creates a virtualenv, installs the project
(`pip install -e ".[camera]"`), copies `config/dragonfly.example.yaml` to
`config/dragonfly.yaml` if it doesn't exist yet, mounts/checks the external
HDD, and installs+enables the systemd service
(`deploy/dragonfly-hub.service`).

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
   bash deploy/deploy.sh
   ```

   This runs `ssh pi@dragonfly-hub` and, on the Pi: `git pull`,
   `pip install -e ".[camera]"` (picks up new dependencies), and
   `sudo systemctl restart dragonfly-hub`. It then tails the service log for
   a few seconds so you can see the new version came up cleanly.

No manual steps on the Pi are needed for a routine update — `deploy.sh` from
the Mac is the whole workflow.

## Rollback

```bash
ssh pi@dragonfly-hub "cd ~/dragonfly && git log --oneline -5"
ssh pi@dragonfly-hub "cd ~/dragonfly && git checkout <commit> && sudo systemctl restart dragonfly-hub"
```

## Logs / debugging

```bash
ssh pi@dragonfly-hub "journalctl -u dragonfly-hub -f"
```
