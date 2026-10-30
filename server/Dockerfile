FROM python:3.14-slim

RUN pip install --no-cache-dir uv==0.13.0

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project
COPY src ./src
RUN uv sync --locked

RUN useradd --system --no-create-home gungnir
USER gungnir
ENV PATH=/app/.venv/bin:$PATH
EXPOSE 8080
CMD ["gungnir-core", "serve", "--host", "0.0.0.0", "--port", "8080"]
