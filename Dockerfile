# syntax=docker/dockerfile:1.7

# ---------- build stage ----------
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv==0.5.*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# Install project + runtime deps into an isolated venv.
# --no-dev skips pytest/ruff/mypy; we only need runtime deps in the image.
RUN uv venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache-dir .

# ---------- runtime stage ----------
FROM python:3.12-slim AS runtime

# Copy the pre-built venv from the builder. No uv, no build tools, no cache.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run as non-root; mount manifests under /work at runtime.
RUN useradd --create-home --uid 1000 aromic
USER aromic
WORKDIR /work

ENTRYPOINT ["aromic"]
CMD ["--help"]
