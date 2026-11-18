"""Gunicorn settings.

A single worker process is deliberate: background analysis runs execute in an
in-process thread pool, and startup recovery marks orphaned runs as failed —
both assume one process owns the run executor. Threads give request concurrency.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = 120
graceful_timeout = 30
accesslog = None  # access is logged by the app as structured JSON
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
