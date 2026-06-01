# VPS Docker Setup

Run the Swiss Case Law MCP server on a VPS with Docker Compose and a
persistent SQLite snapshot directory.

## Requirements

- Docker with the Compose plugin
- At least 100-120 GB free disk for the compressed snapshot, expanded SQLite DB, and margin
- A reverse proxy such as nginx or Caddy if exposing the server publicly

## First Boot

Create the host data directory:

```bash
sudo mkdir -p /srv/swiss-caselaw/data
```

If you want to use the prebuilt GitHub Container Registry image, download the
image Compose file and start it:

```bash
curl -fsSLO https://raw.githubusercontent.com/lserafin/caselaw-repo-1/main/docker-compose.image.yml
docker compose -f docker-compose.image.yml up -d
```

The default image is `ghcr.io/lserafin/swiss-caselaw-mcp:latest`. For a
fork or a different owner, override it:

```bash
MCP_IMAGE=ghcr.io/<owner>/swiss-caselaw-mcp:latest \
docker compose -f docker-compose.image.yml up -d
```

If you cloned the repo and want to build locally instead:

```bash
docker compose up -d --build
```

The first start downloads the advertised Hugging Face SQLite snapshot,
verifies its SHA-256, decompresses it to `/data/decisions.db`, and starts the
remote MCP server on container port `8765`. The host bind defaults to
`127.0.0.1:8765`.

Watch the first bootstrap:

```bash
docker compose logs -f swiss-caselaw-mcp
```

Check readiness:

```bash
curl http://127.0.0.1:8765/health
```

## Configuration

Override the host data directory or port with environment variables:

```bash
SWISS_CASELAW_DATA_DIR=/mnt/volume/swiss-caselaw \
MCP_BIND=127.0.0.1 \
MCP_PORT=8770 \
docker compose up -d
```

Keep `MCP_BIND=127.0.0.1` when nginx/Caddy terminates TLS on the same VPS.
Only bind `0.0.0.0` if a firewall restricts access.

## Reverse Proxy

Example Caddy site:

```caddyfile
mcp.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

For nginx, proxy SSE/Streamable HTTP traffic to `http://127.0.0.1:8765`.
The production nginx snapshot in `deploy/nginx-mcp-server.conf` shows the
headers used by the hosted deployment.

## Updating

Prebuilt image update:

```bash
docker compose -f docker-compose.image.yml pull
docker compose -f docker-compose.image.yml up -d
```

Local build update:

```bash
git pull
docker compose up -d --build
```

The container command includes `--bootstrap-snapshot`, which is a no-op when
`/data/decisions.db` already exists.

Replace the local DB from the latest published snapshot:

```bash
docker compose run --rm swiss-caselaw-mcp \
  python -m snapshot_bootstrap --data-dir /data --force
docker compose restart swiss-caselaw-mcp
```

## Operations

Show logs:

```bash
docker compose logs -f swiss-caselaw-mcp
```

Restart:

```bash
docker compose restart swiss-caselaw-mcp
```

Stop without deleting data:

```bash
docker compose down
```

The SQLite DB remains on the host under `/srv/swiss-caselaw/data` unless you
set `SWISS_CASELAW_DATA_DIR` to another path.
