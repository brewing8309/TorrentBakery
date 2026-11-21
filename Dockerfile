# syntax=docker/dockerfile:1
FROM python:3.13-slim-trixie AS builder
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[web]"

FROM python:3.13-slim-trixie
ARG VERSION=dev
LABEL org.opencontainers.image.title="TorrentBakery" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/brewing8309/TorrentBakery" \
      org.opencontainers.image.licenses="CC-BY-NC-4.0"
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg mediainfo tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 torrentbakery \
 && useradd -u 1000 -g 1000 -d /data -s /usr/sbin/nologin torrentbakery
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY packaging/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENV TB_HOST=0.0.0.0 \
    TB_PORT=8080 \
    TB_CONFIG=/data/config.toml \
    TB_OUT=/data/out \
    HOME=/data \
    PYTHONUNBUFFERED=1
VOLUME /data
WORKDIR /data
EXPOSE 8080
# any HTTP answer (also 303/401 with the login on) means alive; only a refused connection fails
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import os, httpx; httpx.get('http://127.0.0.1:%s/busy' % os.environ['TB_PORT'], timeout=3)"]
ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["tb", "serve"]
