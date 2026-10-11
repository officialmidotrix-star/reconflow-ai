#!/usr/bin/env bash
# Rehearse the Batch 1 migration on a COPY of the production database.
#
# Run this in a disposable Linux machine (a GitHub Codespace) - see
# docs/BATCH1_RUNBOOK.md. It only READS from production (a standard pg_dump),
# restores into a temporary PostgreSQL inside this machine, and runs the full
# rehearsal there. Production is never written to.
#
#   bash scripts/rehearse.sh official.midotrix@gmail.com
#
set -euo pipefail

EMAIL="${1:-}"
[ -n "$EMAIL" ] || { echo "Usage: bash scripts/rehearse.sh <the email you log in with as yourself>"; exit 2; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mSTOPPED: %s\033[0m\n' "$*" >&2; exit 1; }
quiet_apt() { $SUDO env DEBIAN_FRONTEND=noninteractive apt-get "$@" >/dev/null; }

if [ "$(id -u)" = 0 ]; then SUDO=""; PG_AS=(runuser -u postgres --); else SUDO="sudo"; PG_AS=(sudo -u postgres); fi

# -- 1. the database to copy (typed hidden: it contains the password) -------------
if [ -z "${RF_SOURCE_URL:-}" ]; then   # RF_SOURCE_URL exists only so this script can be tested unattended
  read -rsp "Paste the EXTERNAL database URL from Render (typing is hidden), then press Enter: " RF_SOURCE_URL; echo
fi
case "$RF_SOURCE_URL" in postgres://*|postgresql://*) ;; *) die "That does not look like a PostgreSQL URL (it should start with postgresql://).";; esac
SRC="$RF_SOURCE_URL"
rest="${SRC#*://}"; rest="${rest##*@}"; SRC_HOST="${rest%%[:/]*}"; path="${rest#*/}"; SRC_DB="${path%%\?*}"

say "1. Checking the database and its PostgreSQL version"
if ! command -v psql >/dev/null 2>&1; then $SUDO apt-get update -qq >/dev/null; quiet_apt install -y -qq postgresql-client; fi
VNUM="$(psql "$SRC" -Atc 'show server_version_num' 2>/tmp/rehearse_err.txt)" || die "Could not connect to that database: $(tr -d '\r' </tmp/rehearse_err.txt | tail -1). Check you pasted the EXTERNAL url, completely."
MAJOR=$(( VNUM / 10000 ))
echo "   reachable: database '$SRC_DB' on $SRC_HOST, PostgreSQL $MAJOR"

# -- 2. a matching PostgreSQL inside this disposable machine ------------------------
BIN="/usr/lib/postgresql/$MAJOR/bin"
if [ ! -x "$BIN/pg_dump" ] || [ ! -x "$BIN/postgres" ]; then
  say "2. Installing PostgreSQL $MAJOR (temporary - it lives and dies with this machine)"
  $SUDO apt-get update -qq >/dev/null
  if ! apt-cache show "postgresql-$MAJOR" >/dev/null 2>&1; then
    quiet_apt install -y -qq postgresql-common curl ca-certificates
    $SUDO /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null 2>&1 || true
    $SUDO apt-get update -qq >/dev/null
  fi
  quiet_apt install -y -qq "postgresql-$MAJOR" || die "Could not install PostgreSQL $MAJOR. Send me this message."
fi
pg_lsclusters 2>/dev/null | awk -v v="$MAJOR" '$1==v && $2=="main"{f=1} END{exit !f}' || $SUDO pg_createcluster "$MAJOR" main >/dev/null
$SUDO pg_ctlcluster "$MAJOR" main start 2>/dev/null || true
LPORT="$(pg_lsclusters | awk -v v="$MAJOR" '$1==v && $2=="main"{print $3}')"
for _ in $(seq 1 30); do pg_isready -q -h 127.0.0.1 -p "$LPORT" && break; sleep 1; done
pg_isready -q -h 127.0.0.1 -p "$LPORT" || die "The temporary PostgreSQL did not start."

cd /tmp
"${PG_AS[@]}" psql -p "$LPORT" -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rehearsal')
THEN CREATE ROLE rehearsal LOGIN SUPERUSER PASSWORD 'rehearsal'; END IF; END $$;
SQL
"${PG_AS[@]}" psql -p "$LPORT" -q -c "DROP DATABASE IF EXISTS rehearsal_copy WITH (FORCE)" -c "CREATE DATABASE rehearsal_copy OWNER rehearsal"
cd "$REPO_ROOT"
LOCAL="postgresql://rehearsal:rehearsal@127.0.0.1:$LPORT/rehearsal_copy"

# -- 3. copy production into it ------------------------------------------------------------
say "3. Copying the production database (read-only on production)"
DUMP="$(mktemp /tmp/prod_dump.XXXXXX)"; chmod 600 "$DUMP"
trap 'rm -f "$DUMP" /tmp/rehearse_err.txt' EXIT
"$BIN/pg_dump" "$SRC" --no-owner --no-privileges -f "$DUMP" || die "pg_dump failed."
"$BIN/psql" -v ON_ERROR_STOP=1 -q "$LOCAL" -f "$DUMP" >/dev/null || die "Restoring the copy failed."
mism=()
for t in $("$BIN/psql" "$LOCAL" -Atc "select tablename from pg_tables where schemaname='public' order by 1"); do
  a="$("$BIN/psql" "$SRC" -Atc "select count(*) from \"$t\"")"; b="$("$BIN/psql" "$LOCAL" -Atc "select count(*) from \"$t\"")"
  [ "$a" = "$b" ] || mism+=("$t (live $a, copy $b)")
done
[ ${#mism[@]} -eq 0 ] || die "The copy does not match production: ${mism[*]}. Production changed while copying (an analysis was running?). Run the script again."
echo "   the copy has exactly the same number of rows as production in every table"
rm -f "$DUMP"   # the copy now lives only in the temporary database

# -- 4. the project's Python packages ----------------------------------------------------------
say "4. Installing the project's Python packages (first time only)"
if ! python3 -c "import venv, ensurepip" >/dev/null 2>&1; then $SUDO apt-get update -qq >/dev/null; quiet_apt install -y -qq python3-venv python3-pip; fi
python3 -m venv "$REPO_ROOT/.venv-rehearsal"
# shellcheck disable=SC1091
. "$REPO_ROOT/.venv-rehearsal/bin/activate"
pip install -q -r apps/backend/requirements.txt

# -- 5. the rehearsal itself ----------------------------------------------------------------------
say "5. Running the rehearsal on the copy"
cd "$REPO_ROOT/apps/backend/src"
set +e
python -m bootstrap.verify_migration --url "$LOCAL" --operator-email "$EMAIL" --confirm-copy
RC=$?
set -e
cd "$REPO_ROOT"
if [ "$RC" -ne 0 ]; then
  rm -f .rehearsal_passed
  die "The rehearsal FAILED (see above). Production is untouched. Copy the red/FAIL lines and send them to me."
fi
printf '{"passed_at":"%s","operator_email":"%s","source_host":"%s","source_db":"%s","pg_major":%s}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$EMAIL" "$SRC_HOST" "$SRC_DB" "$MAJOR" > .rehearsal_passed

printf '\n\033[32mREHEARSAL PASSED.\033[0m Nothing has been changed in production.\n'
echo "Next: tell the assistant it passed. When you both agree, run:  bash scripts/cutover.sh $EMAIL"
