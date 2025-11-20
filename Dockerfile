# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
# gcc and python3-dev are often needed for compiling C extensions (uvloop, orjson, hdrhistogram)
# if wheels are not available.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies
COPY pyproject.toml .
COPY src/ src/
COPY README.md .

# Install the package itself (and dependencies)
RUN pip install --no-cache-dir .

# Stage 2: Runner
FROM python:3.12-slim AS runner

WORKDIR /app

# Install runtime dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code (optional, if installed as package, but good for reference or config)
# Actually, since we installed it, we don't strictly need the src code here, 
# but we might need config files.
COPY config.example.toml /app/config.toml

# Create non-root user
RUN useradd -m -u 1000 pystatsd && \
    chown -R pystatsd:pystatsd /app

USER pystatsd

# Expose ports
# 8125/udp: StatsD Ingest
# 8126/tcp: Management/Observability (default)
EXPOSE 8125/udp
EXPOSE 8126/tcp

# Healthcheck
HEALTHCHECK --interval=30s --timeout=3s \
  CMD curl -f http://localhost:8126/health/live || exit 1

# Entrypoint
ENTRYPOINT ["pystatsd"]
CMD ["--config", "/app/config.toml"]
