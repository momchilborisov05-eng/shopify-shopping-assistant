# syntax=docker/dockerfile:1
# --- build stage: install the dependencies into a virtualenv ---
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install -r requirements.txt

# --- runtime stage: the virtualenv, the app and nothing else ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/venv/bin:$PATH" \
    ASSISTANT_DB=/app/var/assistant.db
WORKDIR /app
# Runtime libraries of the Azure SQL driver (mssql-python)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/var && chown app:app /app/var
COPY --from=build /venv /venv
COPY --chown=app:app assistant ./assistant
COPY --chown=app:app web ./web
COPY --chown=app:app config ./config
COPY --chown=app:app data/demo_store.json ./data/demo_store.json
USER app
EXPOSE 8000
VOLUME ["/app/var"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)"
CMD ["uvicorn", "web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
