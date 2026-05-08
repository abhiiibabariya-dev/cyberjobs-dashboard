FROM python:3.11-slim

WORKDIR /app

# Build deps for any C extensions (bcrypt, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (data files come from /data volume in production)
COPY dashboard.py soc_job_hunter.py service_runner.py run_scan.py ./
COPY templates/ templates/
COPY config.example.json ./config.example.json

# Data directory is mounted from a volume in production (Fly.io: /data)
# Default to /data if mounted, fallback to /app for local builds.
ENV DATA_DIR=/data
ENV PORT=8080
EXPOSE 8080

# Healthcheck — Render will use the path in render.yaml; this helps Docker hosts.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:${PORT}/api/health || exit 1

CMD ["sh", "-c", "gunicorn dashboard:app --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 120 --access-logfile -"]
