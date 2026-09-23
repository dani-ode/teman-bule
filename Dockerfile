# Supply reviewed immutable image references in .env.deploy.
ARG PYTHON_IMAGE
ARG UV_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY requirements.lock ./
RUN uv venv /opt/venv && uv pip sync --python /opt/venv/bin/python --require-hashes requirements.lock

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --from=dependencies /opt/venv /opt/venv
# Deliberately fails until foundation provides these real artifacts.
COPY --chown=app:app src/ ./src/
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations/ ./migrations/
ENV PYTHONPATH=/app/src
USER 10001:10001
