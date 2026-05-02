FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# GeoDjango runtime: GDAL + GEOS + PROJ
RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    libproj-dev \
    gdal-bin \
    libgeos-dev \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN mkdir -p config apps && touch config/__init__.py apps/__init__.py
RUN pip install uv && uv pip install --system -e ".[dev]"

COPY . .

EXPOSE 8000
