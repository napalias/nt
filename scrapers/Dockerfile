FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    libgeos-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN mkdir -p realestate_spiders && touch realestate_spiders/__init__.py
RUN pip install uv && uv pip install --system -e .

RUN playwright install --with-deps chromium

COPY . .
