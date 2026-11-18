"""WSGI entrypoint: ``gunicorn wsgi:app`` (container) or ``python wsgi.py`` (local dev)."""

from __future__ import annotations

import os

from app import create_app


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader for local runs (Docker passes env vars directly). Never overrides real env vars."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), threaded=True)
