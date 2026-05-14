FROM python:3.12.8-slim

ARG VERSION

# curl is used by HEALTHCHECK.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "icloudpd-web==${VERSION}"

# Install entrypoint with execute bit BEFORE switching to non-root user.
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Non-root user. Data lives under /data (mounted volume); downloads under
# /downloads (user-mounted). Both directories are pre-created and chown'd
# so a simple `-v host:/data` works without host-side `chown` gymnastics.
RUN useradd -m -u 1000 appuser \
 && mkdir -p /data /downloads /.pyicloud \
 && chown -R appuser:appuser /data /downloads /.pyicloud

USER appuser
WORKDIR /home/appuser

# Declare volumes so users see them in `docker inspect` and orchestrators
# auto-create host paths.
VOLUME ["/data", "/downloads", "/.pyicloud"]

EXPOSE 5000

# Defaults. The entrypoint only forwards flags for vars that are set, so
# unsetting any of these returns you to the CLI's built-in defaults.
# ICLOUDPD_COOKIE_DIR should be volume-mounted for persistence across restarts
# (cookies store authentication tokens — losing them forces re-auth/2FA).
ENV HOST=0.0.0.0 \
    PORT=5000 \
    DATA_DIR=/data \
    ICLOUDPD_COOKIE_DIR=/.pyicloud

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/auth/status" >/dev/null || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
