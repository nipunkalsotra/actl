FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

CMD ["uvicorn", "actl.main:app", "--host", "0.0.0.0", "--port", "8000"]
