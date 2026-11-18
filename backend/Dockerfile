# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY wsgi.py gunicorn.conf.py ./

# Unprivileged user; SQLite lives on a mounted volume at /data
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser

ENV DATABASE_URL=sqlite:////data/ai_visibility.db \
    PORT=5000
EXPOSE 5000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=4).status == 200 else 1)"

# One process (the run executor and restart-recovery are per-process), many threads.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]

# ---- test image: `docker build --target test -t aivp-backend-test backend && docker run --rm aivp-backend-test`
FROM base AS test
USER root
RUN pip install -r requirements-dev.txt
COPY tests ./tests
COPY pytest.ini ./
USER appuser
CMD ["python", "-m", "pytest", "-q"]

FROM base AS runtime
