# icloudpd-web Docker Image

Current package version: 2026.7.24

## Quick Start

```bash
docker run -d \
  --name icloudpd-web \
  -p 5000:5000 \
  -v ./data:/data \
  -v ./downloads:/downloads \
  spicadust/icloudpd-web:latest
```

`/data` holds policies, secrets, cookies and run logs. `/downloads` is where
your policies write photos — mount any host directories your policies'
`directory` field points at.

> [!WARNING]
> Without `PASSWORD_HASH` the server starts **passwordless** while binding to
> `0.0.0.0`: anyone who can reach the port gets full access to your policies,
> run logs, and iCloud session (and can trigger downloads or deletions). Only
> run passwordless on a trusted, isolated network — and preferably set
> `PASSWORD_HASH` anyway (see below). The UI shows a persistent warning
> banner while authentication is disabled.

## Environment Variables

- `PUID` / `PGID`: user and group ID the app runs as (default: `1000`/`1000`).
  Set these to the owner of your bind-mounted host directories (run `id` on
  the host to find them) so the app can write policies and photos — typically
  needed on Synology and other NAS setups. The container starts as root,
  chowns `/data` (recursively) and `/downloads` (top level only) to
  `PUID:PGID`, then drops privileges. If you change `PUID` over an existing
  download tree, run a one-time `chown -R` on the host.
- `HOST`: host to bind to (default: `0.0.0.0`)
- `PORT`: port to bind to (default: `5000`)
- `DATA_DIR`: path for persistent state inside the container (default: `/data`)
- `PASSWORD_HASH`: scrypt hash of the server password. Generate with:
  ```bash
  docker run --rm spicadust/icloudpd-web:latest icloudpd-web init-password 'yourpw'
  ```
  If unset, the server runs passwordless (see the warning above), logs a
  warning on startup, and the UI shows a persistent banner.
- `SESSION_SECRET`: stable session secret across restarts. If unset, a random
  one is generated on each boot and all sessions are invalidated on restart.
  Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

## Using Docker Compose

1. Clone the repository from Github
2. Modify the `docker-compose.yml` file as needed
3. Run:

```bash
docker compose up -d
```
