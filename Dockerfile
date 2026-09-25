FROM ghcr.io/astral-sh/uv:latest AS uv_bin

FROM python:3.12-slim

# Copy uv binaries
COPY --from=uv_bin /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install curl for container healthcheck and sqlite3
RUN apt-get update && apt-get install -y --no-install-recommends curl sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies using uv
COPY pyproject.toml uv.lock* requirements.txt ./
RUN uv sync --frozen --no-install-project || uv pip install --system -r requirements.txt

# Copy project files
COPY . .

# Place the virtualenv on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Collect static files via WhiteNoise
RUN python manage.py collectstatic --noinput || true

# Ensure data directory exists
RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health/ || exit 1

CMD ["sh", "-c", "python manage.py migrate && python manage.py seed_user_one && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 4"]
