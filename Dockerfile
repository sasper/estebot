# The public demo: a made-up person's data, generated fresh at every start. No real data
# goes into this image or onto the server (see fly.toml and deploy/entrypoint.sh).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[web]" && rm -rf /app/src /app/build

COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN useradd --create-home --uid 1000 estebot && mkdir /data && chown estebot /data

USER estebot
ENV ESTEBOT_DEMO=1 ESTEBOT_DEMO_DIR=/data/demo ESTEBOT_CONFIG=/data/demo/estebot.toml PORT=8080
EXPOSE 8080
CMD ["/app/entrypoint.sh"]
