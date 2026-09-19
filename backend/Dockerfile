# NEXA backend image (development flavour — a slimmer production image comes in Week 8).
FROM python:3.12-slim

# uv is a fast Python package manager. We copy just its binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Put the virtualenv OUTSIDE /app so the dev bind-mount of ./backend -> /app
    # (see docker-compose.yml) doesn't hide the installed packages.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Lets scripts in /scripts do `from app...` imports.
    PYTHONPATH=/app \
    # tiktoken downloads its tokenizer file on first use. We download it at build
    # time into this folder so the container never needs internet access for it.
    TIKTOKEN_CACHE_DIR=/opt/tiktoken

WORKDIR /app

# Install dependencies first (cached layer): only re-runs when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# Pre-fetch the cl100k_base tokenizer used by the chunker.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY . .

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
