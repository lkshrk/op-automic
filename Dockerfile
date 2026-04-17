# syntax=docker/dockerfile:1.7

# ---------- slim build stage ----------
FROM python:3.12-slim AS builder-slim

RUN pip install --no-cache-dir uv==0.5.*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache-dir .

# ---------- slim runtime (default) ----------
FROM python:3.12-slim AS runtime

COPY --from=builder-slim /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 aromic
USER aromic
WORKDIR /work

ENTRYPOINT ["aromic"]
CMD ["--help"]

# ---------- alpine build stage ----------
FROM python:3.12-alpine AS builder-alpine

RUN pip install --no-cache-dir uv==0.5.*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache-dir .

# ---------- alpine runtime (tiny variant) ----------
FROM python:3.12-alpine AS runtime-alpine

COPY --from=builder-alpine /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN adduser -D -u 1000 aromic
USER aromic
WORKDIR /work

ENTRYPOINT ["aromic"]
CMD ["--help"]
