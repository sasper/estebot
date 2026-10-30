# EsteBot on Fly. With no secrets set this is the public demo only: a made-up person's data,
# generated fresh at every start. Private mode (deploy/entrypoint.sh) adds signed-in people,
# whose data repo is cloned onto the volume at runtime; none of it is in the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# git and ssh for the data repo, tzdata so "today" is the user's day and not UTC's
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[web]" && rm -rf /app/src /app/build

COPY deploy/entrypoint.sh deploy/scheduler.py deploy/github_known_hosts /app/
RUN useradd --create-home --uid 1000 estebot && mkdir /data && chown estebot /data

USER estebot
ENV ESTEBOT_DEMO=1 ESTEBOT_DEMO_DIR=/data/demo ESTEBOT_CONFIG=/data/demo/estebot.toml \
    ESTEBOT_DEMO_CHAT_METER=/data/demo-chat.sqlite \
    DATA_DIR=/data/health-data PORT=8080
EXPOSE 8080
CMD ["/app/entrypoint.sh"]
