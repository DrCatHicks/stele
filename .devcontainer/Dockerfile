FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# --- Remove the broken Yarn apt source from the base image ---
# The Yarn GPG key has rotated and the base image's pinned source fails apt-get update.
# Yarn itself is already installed in the base image; we don't need this repo.
RUN rm -f /etc/apt/sources.list.d/yarn.list

# --- System packages: Postgres 16 + tools ---
# Bookworm ships Postgres 15; pull 16 from PGDG.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg lsb-release \
        build-essential \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
        https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-16 postgresql-client-16 \
        jq make \
    && rm -rf /var/lib/apt/lists/*

# --- uv for Python dependency management ---
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx

# --- Postgres setup ---
COPY postgres-init/ /docker-entrypoint-initdb.d/

# Let the vscode user manage postgres without sudo for dev convenience.
RUN usermod -aG postgres vscode
RUN echo "vscode ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode
USER vscode
