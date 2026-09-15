FROM cgr.dev/chainguard/python@sha256:3d1f8858036c90e826f267baa1abbfd1b59e16d23b0d58ee8d84f4f216375702 AS build

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/requirements.txt \
    && uv pip install --target /app/out --requirements /tmp/requirements.txt \
    && cp -R src/hypershell_mobile_bridge /app/out/hypershell_mobile_bridge

FROM cgr.dev/chainguard/python@sha256:e1a792bc1e72e6395fbf0504bd040945df8999e3e0b22728c279767f771a04ee

ARG APP_VERSION=0.1.0
ARG APP_REVISION=unknown
ARG APP_CREATED=unknown
LABEL org.opencontainers.image.title="Hypershell Mobile Bridge" \
      org.opencontainers.image.description="On-demand reverse WebSocket control bridge for Hypershell mobile device control" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.created="${APP_CREATED}" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${APP_REVISION}"

WORKDIR /app
COPY --from=build /app/out /app/site
ENV PYTHONPATH="/app/site" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["/usr/bin/python"]
CMD ["-m", "hypershell_mobile_bridge.service"]
