# Local multi-stage build. The web application is copied from this checkout;
# only the custom icloudpd fork is cloned from GitHub.
#
# Build args default to the currently required plugin-support branches.

# Build icloudpd wheel from GitHub.
FROM python:3.13-slim AS icloudpd-builder

ARG ICLOUDPD_REPO=https://github.com/spmp/icloud_photos_downloader.git
ARG ICLOUDPD_BRANCH=feature/until-skip-created-before

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth=1 --branch "${ICLOUDPD_BRANCH}" "${ICLOUDPD_REPO}" .

RUN pip install --no-cache-dir build \
 && python -m build --wheel --outdir /wheels

# Build the React frontend from this checkout.
FROM node:20-slim AS frontend-builder

WORKDIR /project

COPY web/package.json web/package-lock.json web/
RUN cd web && npm ci

COPY web/ web/
COPY src/ src/
RUN cd web && npm run build

# Runtime image.
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# README.md is excluded by .dockerignore, so provide the metadata placeholder.
COPY pyproject.toml LICENSE ./
RUN printf '# icloudpd-web\n' > README.md
COPY src/ src/
COPY --from=frontend-builder /project/src/icloudpd_web/web_dist src/icloudpd_web/web_dist
RUN pip install --no-cache-dir .

# The custom wheel has incomplete dependency metadata. Keep its dependencies
# from being re-resolved after installing the web application's requirements.
COPY --from=icloudpd-builder /wheels/icloudpd-*.whl /tmp/
RUN pip install --no-cache-dir --force-reinstall --no-deps /tmp/icloudpd-*.whl \
 && python -c "from anyio.abc import ObjectReceiveStream; from typing_extensions import sentinel; from icloudpd.cli import cli" \
 && rm /tmp/icloudpd-*.whl

# The fork's plugins/ directory is outside src/ and is not included in its wheel.
COPY --from=icloudpd-builder /build/plugins /tmp/plugins
RUN python -c "import site; print(site.getsitepackages()[0])" > /tmp/site-packages-dir \
 && cp -r /tmp/plugins "$(cat /tmp/site-packages-dir)/plugins" \
 && rm -rf /tmp/plugins /tmp/site-packages-dir

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN useradd -m -u 1000 appuser \
 && mkdir -p /data /downloads /.pyicloud \
 && chown -R appuser:appuser /data /downloads /.pyicloud

USER appuser
WORKDIR /home/appuser

VOLUME ["/data", "/downloads", "/.pyicloud"]

EXPOSE 5000

ENV HOST=0.0.0.0 \
    PORT=5000 \
    DATA_DIR=/data \
    ICLOUDPD_COOKIE_DIR=/.pyicloud

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/auth/status" >/dev/null || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
