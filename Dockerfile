# Docker image for the backend. Optional: Render builds with pip directly.
# Kept so the service can run anywhere, including locally.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first, so a source change does not reinstall the world.
COPY pyproject.toml ./
COPY backend/app/__init__.py backend/app/__init__.py
RUN pip install --no-cache-dir .

# The policy markdown is source content, not a build artefact, so it has to be
# in the image. bootstrap.py refuses to serve without it.
COPY backend ./backend
COPY data/policies ./data/policies

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
