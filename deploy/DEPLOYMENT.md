# EC2 Deployment (Docker-free)

Replaces `docker-compose.yml` with three systemd services on one instance:

| Old container(s)                              | New service            | What runs it |
|------------------------------------------------|-------------------------|--------------|
| `questdb`                                       | `questdb.service`        | native QuestDB binary |
| *(was your Windows host, outside Docker)*       | `ibgateway.service`       | IB Gateway headless under Xvfb, driven by IBC |
| `api` + all 6 `streamer-*` containers           | `ibkr-orchestrator.service` | `orchestrator.py` (subprocess supervisor) |

## 0. Instance sizing

- Ubuntu 22.04/24.04 LTS
- 2 vCPU / 4 GB RAM to get started; budget 4 vCPU / 8 GB once past initial
  testing (6 streamers + a 2-worker API + QuestDB's JVM + IB Gateway's JVM
  all share one box)
- 20 GB+ gp3 disk (QuestDB data grows with tick volume + retention)
- Security group: expose **8000** (API) only to your frontend/ALB.
  Leave 7496 (Gateway), 9000/9009/8812 (QuestDB) closed to the internet --
  everything now talks over `127.0.0.1`, so nothing else needs to be public.

## 1. Bootstrap the host

```bash
scp -r app/ ubuntu@<ec2-ip>:~/app          # the whole project tree
ssh ubuntu@<ec2-ip>
cd ~/app/deploy
sudo bash 00_ec2_bootstrap.sh
```

This installs Java, Xvfb, QuestDB (native), creates the `ibkr` service
user, and registers the three systemd units (without starting them yet).

## 2. Move the app code into place

```bash
sudo rsync -a --exclude='.git' ~/app/ /opt/ibkr-pipeline/app/
sudo chown -R ibkr:ibkr /opt/ibkr-pipeline/app
sudo chmod +x /opt/ibkr-pipeline/app/deploy/start_ibgateway.sh

sudo -u ibkr /opt/ibkr-pipeline/venv/bin/pip install -r \
    /opt/ibkr-pipeline/app/requirements.txt
```

At this point `/opt/ibkr-pipeline/app/orchestrator.py` should exist (it's
at the project root, a sibling of `runner.py`, not inside `deploy/`).

## 3. Set up `.env`

```bash
sudo -u ibkr cp /opt/ibkr-pipeline/app/_env.example /opt/ibkr-pipeline/app/.env
sudo -u ibkr nano /opt/ibkr-pipeline/app/.env
```

The template already has `IB_HOST=127.0.0.1` and `QDB_HOST=127.0.0.1` set
correctly for this layout. You only need to change:

```
QDB_PASSWORD=<real password>
API_KEYS=<real key(s)>
API_CORS_ALLOW_ORIGINS=<your frontend origin>
```

```bash
sudo chmod 600 /opt/ibkr-pipeline/app/.env
```

## 4. Install IB Gateway + IBC (one manual step)

This is the one part that can't be fully unattended, because IBKR's
installer and first login are interactive, and if your account uses
push-based 2FA, only your phone can approve that login.

```bash
sudo -u ibkr -i
cd /opt/ibkr-pipeline/ibgateway

# 1. Download IBKR's official Linux offline Gateway installer and IBC:
wget https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh
wget https://github.com/IbcAlpha/IBC/releases/latest/download/IBCLinux-<version>.zip

# 2. Run the Gateway installer (it will prompt for an install path --
#    use /opt/ibkr-pipeline/ibgateway/Jts):
bash ibgateway-stable-standalone-linux-x64.sh

# 3. Unzip IBC and fill in config.ini (username, password, TradingMode=live,
#    AcceptIncomingConnectionAction=accept):
unzip IBCLinux-*.zip -d ibc
nano ibc/config.ini
```

**First login must happen once over a VNC session**, so a human can clear
one-time dialogs and approve 2FA:

```bash
# from your local machine (needs an SSH tunnel + a VNC client, e.g. TigerVNC):
sudo -u ibkr Xvfb :99 -screen 0 1024x768x16 &
sudo -u ibkr x11vnc -display :99 -nopw -listen localhost -xkb &
ssh -L 5900:localhost:5900 ubuntu@<ec2-ip>   # then point a VNC client at localhost:5900
sudo -u ibkr DISPLAY=:99 /opt/ibkr-pipeline/app/deploy/start_ibgateway.sh
# log in, approve 2FA, confirm the API settings dialog, then Ctrl+C and kill the Xvfb/x11vnc above
```

After this, IBC remembers enough that subsequent starts are unattended --
**except** the account's daily forced logout around 23:45 US/Eastern.
If you're on **push notification 2FA**, that restart needs a human tap on
your phone every day; the practical fixes are:

- Switch to IBKR's **Security Code Card** (or read-only card-free mode) --
  IBC can then supply the code programmatically, so restarts stay
  unattended.
- Or accept one manual approval per day at a predictable time.
- Paper-trading accounts don't have this constraint, if you want to
  validate the whole pipeline before going live.

Also double check inside the Gateway API settings (same two settings the
original README already called out): **Enable ActiveX and Socket
Clients** is checked. You no longer need to touch "Allow connections from
localhost only" or Trusted IPs -- Gateway and the streamers are both on
`127.0.0.1` now, which is already trusted by default.

**Verify the connection works** before starting any streamers:

```bash
sudo -u ibkr /opt/ibkr-pipeline/venv/bin/python /opt/ibkr-pipeline/app/scripts/test_ib.py
```

This connects with a throwaway client ID (999, reserved -- never reused by
a real stream), qualifies a GC future, and prints 10 seconds of live
bid/ask/last. Bid/Ask staying `None` for the full 10 seconds usually means
no live market-data subscription for that exchange, not a connection
problem -- the script deliberately requests live data type explicitly so
this failure mode is visible rather than silent.

## 5. Start everything, in order

```bash
sudo systemctl enable --now questdb
sudo systemctl status questdb        # wait for it to be listening on 9000

sudo systemctl enable --now ibgateway
sudo journalctl -u ibgateway -f      # watch for a successful headless login

sudo systemctl enable --now ibkr-orchestrator
sudo journalctl -u ibkr-orchestrator -f
```

## 6. Verify

```bash
curl http://localhost:8000/health
curl -H "X-API-Key: $YOUR_KEY" "http://localhost:8000/questdb/commodity?limit=5"

tail -f /var/log/ibkr-pipeline/streamer-commodity-futures.log
tail -f /var/log/ibkr-pipeline/orchestrator.log
```

QuestDB console (SSH-tunnel only, since port 9000 isn't public):

```bash
ssh -L 9000:localhost:9000 ubuntu@<ec2-ip>
# then open http://localhost:9000 locally
```

## Docker has been fully removed

There is no `docker-compose.yml`, `Dockerfile.streamer`, or `Dockerfile.api`
in this project anymore -- QuestDB, IB Gateway, the API, and all six
streamers run as native processes (via venv + systemd on the server, plain
venv locally). If you ever need the old container-based setup back, it
would need to be rebuilt from scratch; nothing in `src/` changed in a way
that makes it Docker-incompatible, but no Docker config ships with the
project going forward.

## Adding a new API endpoint

See `PROJECT_STRUCTURE.md` -> "Adding a new API endpoint later". In short:
drop a new router file into `src/api/routers/`, register it in that
folder's `__init__.py`, and `systemctl restart ibkr-orchestrator` -- no
other file changes.

## Open items carried over from the original README

These weren't in scope for the Docker removal and still apply as-is:
contract-expiry rollover is still a single manual `.env` edit, there's
still no alerting on disconnects/failed flushes (worth wiring to
CloudWatch Logs + an alarm now that everything's on EC2 anyway), and
`ib_insync` is still the unmaintained upstream package.
