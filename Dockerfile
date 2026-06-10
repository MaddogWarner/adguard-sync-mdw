FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m venv /opt/venv \
  && /opt/venv/bin/pip install --upgrade pip \
  && /opt/venv/bin/pip install .

FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    CONFIG_PATH="/config/config.yaml"
WORKDIR /app
RUN useradd --create-home --system adguard-sync \
  && mkdir -p /config /data \
  && chown -R adguard-sync:adguard-sync /app /data
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
USER adguard-sync
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"
CMD ["python", "-m", "app.main"]
