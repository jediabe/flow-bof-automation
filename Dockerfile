# Phase 1: container runs the Python CLI only.
# Chrome stays on the Windows host — the container reaches it over CDP
# via host.docker.internal:9222 (configured in docker-compose.yml).
# We do NOT install Chromium in the image: Playwright's connect_over_cdp
# attaches to an existing browser and only needs the Python+Node driver
# that ships with the playwright pip package.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Minimal system deps — ca-certificates is needed to verify HTTPS
# endpoints (Flow Labs, the media-redirect URL) when sync_workflow or
# generate_image_for_product fetch content.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so this layer caches across code edits.
COPY requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

# Bake the source for `docker run`-style usage. docker-compose.yml also
# bind-mounts the project root over this so code edits don't need a
# rebuild — the COPY is the fallback when running the image standalone.
COPY . /app

# No ENTRYPOINT — callers pass the full command, typically:
#     docker compose run --rm app python main.py --check-browser
ENTRYPOINT []
CMD ["python", "main.py", "--help"]
