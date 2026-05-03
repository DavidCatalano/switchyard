# Switchyard systemd deployment

This deployment path is for running Switchyard as a normal Linux service,
including inside an LXD container such as `switchyard.lan`. An optional generic
LXD profile template is available at `deploy/lxd-profiles/switchyard.yaml`.

The recommended topology is:

```text
switchyard.lan
  systemd: switchyard.service
  systemd: switchyard-docker-tunnel@trainbox.service
  systemd: switchyard-docker-tunnel@devbox.service

trainbox.lan
  Docker daemon
  switchyard user in docker group

devbox.lan
  Docker daemon
  switchyard user in docker group
```

The tunnel services forward each remote Docker socket to a local Unix socket:

```text
/run/switchyard/trainbox-docker.sock -> trainbox:/var/run/docker.sock
/run/switchyard/devbox-docker.sock   -> devbox:/var/run/docker.sock
```

Switchyard then uses `unix:///run/switchyard/<host>-docker.sock` in
`config.yaml`. This avoids exposing the Docker API on a local TCP port.

## Assumptions

- The LXD container has a stable LAN name such as `switchyard.lan`.
- The `ubuntu` user is the SSH/deploy user for the LXD container.
- A separate `switchyard` service user exists in the LXD container.
- The `ubuntu` user can run passwordless `sudo` inside the LXD container.
- `uv`, Python 3.12, OpenSSH client, curl, and git are installed in the LXD
  container.
- `trainbox` and `devbox` each have a `switchyard` user in the `docker` group.
- The LXD container can SSH as `switchyard` to each Docker host without a
  password.
- The repo is checked out at `/opt/switchyard`.
- Durable config lives at `/etc/switchyard/config.yaml`.
- Process-local settings live at `/etc/switchyard/switchyard.env`.

Membership in the remote `docker` group is effectively root-equivalent on that
remote host. Treat the SSH key used by Switchyard as privileged.

## Install packages

On the LXD container:

```bash
sudo apt update
sudo apt install -y curl git openssh-client
sudo -iu switchyard bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
```

Confirm `uv` is available for the `switchyard` user:

```bash
sudo -iu switchyard bash -lc 'uv --version'
```

## Prepare directories

```bash
sudo mkdir -p /opt /etc/switchyard/tunnels
sudo chown switchyard:switchyard /opt
sudo chown root:switchyard /etc/switchyard /etc/switchyard/tunnels
sudo chmod 0750 /etc/switchyard /etc/switchyard/tunnels
```

Clone or deploy the repo:

```bash
sudo -iu switchyard git clone <repo-url> /opt/switchyard
sudo -iu switchyard bash -lc 'cd /opt/switchyard/switchyard-api && uv sync'
```

## Configure SSH access

Generate an SSH key inside the LXD container if one does not already exist:

```bash
sudo -iu switchyard ssh-keygen -t ed25519 -C "switchyard@switchyard.lan"
```

Install the public key on each Docker host:

```bash
sudo -iu switchyard ssh-copy-id switchyard@trainbox.lan
sudo -iu switchyard ssh-copy-id switchyard@devbox.lan
```

Verify passwordless SSH and known-hosts setup:

```bash
sudo -iu switchyard ssh switchyard@trainbox.lan true
sudo -iu switchyard ssh switchyard@devbox.lan true
```

## Configure Switchyard

Create `/etc/switchyard/switchyard.env` from `switchyard.env.example`:

```bash
sudo cp /opt/switchyard/deploy/systemd/switchyard.env.example \
  /etc/switchyard/switchyard.env
sudo chown root:switchyard /etc/switchyard/switchyard.env
sudo chmod 0640 /etc/switchyard/switchyard.env
```

Create or copy `/etc/switchyard/config.yaml`. Host Docker settings should use
the local Unix sockets created by the tunnel units:

```yaml
hosts:
  trainbox:
    docker_host: unix:///run/switchyard/trainbox-docker.sock
    backend_host: trainbox.lan
    backend_scheme: http
    port_range: [18000, 18100]

  devbox:
    docker_host: unix:///run/switchyard/devbox-docker.sock
    backend_host: devbox.lan
    backend_scheme: http
    port_range: [18100, 18200]
```

The rest of `config.yaml` should define runtimes, models, and deployments as
usual.

Secure the config file after writing it:

```bash
sudo chown root:switchyard /etc/switchyard/config.yaml
sudo chmod 0640 /etc/switchyard/config.yaml
```

## Configure Docker socket tunnels

Copy the systemd template:

```bash
sudo cp /opt/switchyard/deploy/systemd/switchyard-docker-tunnel@.service \
  /etc/systemd/system/
```

Create one tunnel environment file per Docker host:

```bash
sudo tee /etc/switchyard/tunnels/trainbox.env >/dev/null <<'EOF'
REMOTE_USER=switchyard
REMOTE_HOST=trainbox.lan
LOCAL_SOCKET=/run/switchyard/trainbox-docker.sock
SSH_IDENTITY=/home/switchyard/.ssh/id_ed25519
EOF

sudo tee /etc/switchyard/tunnels/devbox.env >/dev/null <<'EOF'
REMOTE_USER=switchyard
REMOTE_HOST=devbox.lan
LOCAL_SOCKET=/run/switchyard/devbox-docker.sock
SSH_IDENTITY=/home/switchyard/.ssh/id_ed25519
EOF

sudo chown root:switchyard /etc/switchyard/tunnels/*.env
sudo chmod 0640 /etc/switchyard/tunnels/*.env
```

Enable and start the tunnels:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now switchyard-docker-tunnel@trainbox.service
sudo systemctl enable --now switchyard-docker-tunnel@devbox.service
```

Verify sockets:

```bash
ls -l /run/switchyard/*-docker.sock
```

Verify Docker SDK access through a socket:

```bash
sudo -iu switchyard bash -lc \
  'cd /opt/switchyard/switchyard-api && DOCKER_HOST=unix:///run/switchyard/trainbox-docker.sock uv run python -c "import docker; print(docker.from_env().ping())"'
```

## Configure the Switchyard service

Copy the service unit:

```bash
sudo cp /opt/switchyard/deploy/systemd/switchyard.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
```

Enable and start Switchyard:

```bash
sudo systemctl enable --now switchyard.service
```

Check status and logs:

```bash
systemctl status switchyard.service
journalctl -u switchyard.service -f
```

Verify the API:

```bash
curl http://switchyard.lan:8000/health
curl http://switchyard.lan:8000/deployments
curl http://switchyard.lan:8000/v1/models
```

## Operations

Restart after a code deploy:

```bash
sudo systemctl restart switchyard.service
```

Restart one Docker tunnel:

```bash
sudo systemctl restart switchyard-docker-tunnel@trainbox.service
```

View tunnel logs:

```bash
journalctl -u switchyard-docker-tunnel@trainbox.service -f
```

## Direct git deployment

For a direct container deployment, put the bare git repository inside the LXD
container. Your Mac pushes to `ubuntu@switchyard.lan`; the `post-receive` hook
updates `/opt/switchyard` as the `switchyard` service user and restarts the
systemd service.

Create the bare repo inside the container:

```bash
sudo mkdir -p /srv/git
sudo chown ubuntu:ubuntu /srv/git
sudo -iu ubuntu git init --bare /srv/git/switchyard.git
sudo chmod -R a+rX /srv/git/switchyard.git
```

Install the direct deploy hook:

```bash
sudo cp /opt/switchyard/deploy/systemd/post-receive.direct.example \
  /srv/git/switchyard.git/hooks/post-receive
sudo chown ubuntu:ubuntu /srv/git/switchyard.git/hooks/post-receive
sudo chmod 0755 /srv/git/switchyard.git/hooks/post-receive
```

Add a deploy remote on your Mac:

```bash
git remote add switchyard ubuntu@switchyard.lan:/srv/git/switchyard.git
git push switchyard main
```

The example hook deploys only `main`. Edit `DEPLOY_BRANCH` in the installed hook
if a different branch should drive the service.

## Deploy config files

Runtime configuration stays outside git. Push config files separately, then
restart Switchyard:

```bash
scp switchyard-api/config.yaml ubuntu@switchyard.lan:/tmp/switchyard-config.yaml
scp deploy/systemd/switchyard.env.example ubuntu@switchyard.lan:/tmp/switchyard.env

ssh ubuntu@switchyard.lan \
  'sudo install -o root -g switchyard -m 0640 /tmp/switchyard-config.yaml /etc/switchyard/config.yaml && \
   sudo install -o root -g switchyard -m 0640 /tmp/switchyard.env /etc/switchyard/switchyard.env && \
   sudo systemctl restart switchyard.service'
```

Replace `deploy/systemd/switchyard.env.example` with your real local
`switchyard.env` path when deploying secrets or host-specific settings.

## Manual deploy option

If `/opt/switchyard` is a deployed git checkout without a git hook, update it
manually over SSH:

```bash
sudo -iu switchyard bash -lc 'cd /opt/switchyard && git pull --ff-only'
sudo -iu switchyard bash -lc 'cd /opt/switchyard/switchyard-api && uv sync'
sudo systemctl restart switchyard.service
```

Keep `/etc/switchyard/config.yaml`, `/etc/switchyard/switchyard.env`, and
`/home/switchyard/.ssh` out of the repo and in the LXD backup/snapshot plan.
