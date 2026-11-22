FROM python:3.15-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m venv /opt/venv \
  && /opt/venv/bin/pip install --upgrade pip \
  && /opt/venv/bin/pip install .

FROM python:3.15-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    CONFIG_PATH="/config/config.yaml"
WORKDIR /app
# Apply Debian security updates published after the base image was built
# (e.g. zlib1g CVE-2026-76844).
RUN apt-get update \
  && apt-get upgrade -y --no-install-recommends \
  && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --system adguard-sync \
  && mkdir -p /config /data \
  && chown -R adguard-sync:adguard-sync /app /config /data
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY config.example.yaml ./config.example.yaml
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-m", "app.healthcheck"]
CMD ["python", "-m", "app.main"]
