#!/usr/bin/env bash
set -eo pipefail

PG_VERSION=16
PG_DATA=/var/lib/postgresql/${PG_VERSION}/main
PG_CONF=/etc/postgresql/${PG_VERSION}/main

echo "==> Bootstrapping Postgres"

# The package's postinst creates a default cluster called "main" at install time,
# but our volume mount replaces /var/lib/postgresql/16/main with empty (or stale) data.
# We need to either: (a) reuse a valid cluster if present, or (b) drop+recreate.

sudo chown -R postgres:postgres "${PG_DATA}"

if sudo test -f "${PG_DATA}/PG_VERSION" && sudo test -f "${PG_CONF}/postgresql.conf"; then
    echo "    Valid cluster found (data + config), using as-is"
else
    echo "    Cluster missing or incomplete -> recreating"
    # pg_dropcluster fails gracefully if there's nothing to drop.
    sudo pg_dropcluster --stop ${PG_VERSION} main 2>/dev/null || true
    # Wipe any leftover data so pg_createcluster doesn't refuse.
    sudo find "${PG_DATA}" -mindepth 1 -not -name 'lost+found' -delete 2>/dev/null || true
    sudo pg_createcluster ${PG_VERSION} main \
        --start-conf=manual \
        --encoding=UTF8 \
        --locale=C.UTF-8 \
        -- --auth-local=trust --auth-host=md5
fi

echo "==> Starting Postgres"
sudo pg_ctlcluster ${PG_VERSION} main start || {
    echo "    pg_ctlcluster start failed. Logs:"
    sudo tail -50 /var/log/postgresql/postgresql-${PG_VERSION}-main.log 2>/dev/null || true
    exit 1
}

for _ in $(seq 1 30); do
    if pg_isready -h localhost -p 5432 -q; then
        break
    fi
    sleep 0.5
done

if ! pg_isready -h localhost -p 5432 -q; then
    echo "    Postgres did not become ready. Logs:"
    sudo tail -50 /var/log/postgresql/postgresql-${PG_VERSION}-main.log 2>/dev/null || true
    exit 1
fi

echo "==> Creating database and dev superuser (idempotent)"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_dev') THEN
        CREATE ROLE stele_dev LOGIN SUPERUSER PASSWORD 'dev';
    END IF;
END
$$;
SQL

if ! sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw stele; then
    sudo -u postgres createdb -O stele_dev stele
fi

echo "==> Applying init SQL (schemas, roles, grants)"
for f in /docker-entrypoint-initdb.d/*.sql; do
    echo "    $f"
    sudo -u postgres psql -v ON_ERROR_STOP=1 -d stele -f "$f"
done

echo "==> Python: uv sync"
cd /workspace
if [ -f pyproject.toml ]; then
    uv sync
else
    echo "    (no pyproject.toml yet -- skipping)"
fi

echo "==> dbt: writing ~/.dbt/profiles.yml"
mkdir -p ~/.dbt
cat > ~/.dbt/profiles.yml <<'YAML'
stele:
  target: dev
  outputs:
    dev:
      type: postgres
      host: localhost
      port: 5432
      user: stele_etl
      password: dev
      dbname: stele
      schema: stg
      threads: 4
YAML

echo "==> Claude Code: fix volume ownership and persistent bash history"
sudo chown -R vscode:vscode /home/vscode/.claude /home/vscode/.bash-persistent 2>/dev/null || true

HISTFILE_HOOK='
# Persistent bash history (Stele dev container)
export HISTFILE=/home/vscode/.bash-persistent/bash_history
export HISTSIZE=10000
export HISTFILESIZE=20000
shopt -s histappend
'
if ! grep -q "Persistent bash history" ~/.bashrc; then
    echo "${HISTFILE_HOOK}" >> ~/.bashrc
fi

if ! grep -q "alias ccy=" ~/.bashrc; then
    echo "alias ccy='claude --dangerously-skip-permissions'" >> ~/.bashrc
fi

PG_HOOK='
# Start Postgres if not running (Stele dev container)
if ! pg_isready -h localhost -p 5432 -q 2>/dev/null; then
    sudo pg_ctlcluster 16 main start >/dev/null 2>&1 || true
fi
'
if ! grep -q "Stele dev container" ~/.bashrc; then
    echo "${PG_HOOK}" >> ~/.bashrc
fi

echo "==> Done."
echo "    psql -d stele                       # connect"
echo "    uv run pytest                       # run tests"
echo "    claude                              # first run: sign in"
echo "    ccy                                 # subsequent: skip-permissions"
