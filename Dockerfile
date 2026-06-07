# Form 956 PDF Generation Service
#
# Production container for the Python form-fill engine. Run the
# gunicorn server (2 workers x 4 threads) on port 5000.

FROM python:3.12-slim

# --- system deps ---
# libgomp for pymupdf; tini for clean signal handling.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- python deps ---
# Copy the requirements first so the layer caches when only source
# changes.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- source ---
COPY . /app

# --- runtime ---
# forms/ + pdfs/ are mounted read-only in compose/k8s so template
# updates don't require a rebuild. uploads/ is read-write for the
# idempotence cache.
ENV PYTHONUNBUFFERED=1 \
    GUNICORN_CMD_ARGS="--timeout 30 --graceful-timeout 30" \
    LOG_LEVEL=INFO

EXPOSE 5000

# tini reaps zombies + forwards signals so gunicorn shuts down cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", \
     "-w", "2", \
     "-k", "gthread", \
     "--threads", "4", \
     "-b", "0.0.0.0:5000", \
     "app:app"]
