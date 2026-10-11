#!/usr/bin/env bash
# Migrate PRODUCTION to the Batch 1 schema. Only runs after scripts/rehearse.sh passed
# in this same machine, for the SAME database, within the last 48 hours.
#
#   bash scripts/cutover.sh official.midotrix@gmail.com
#
set -euo pipefail

EMAIL="${1:-}"
[ -n "$EMAIL" ] || { echo "Usage: bash scripts/cutover.sh <the same email you used for the rehearsal>"; exit 2; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
die() { printf '\n\033[31mSTOPPED: %s\033[0m\n' "$*" >&2; exit 1; }
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# -- 0. the rehearsal must have passed, here, for this email, recently -----------------------------
[ -f .rehearsal_passed ] || die "No passed rehearsal found in this machine. Run: bash scripts/rehearse.sh $EMAIL"
field() { python3 -c "import json,sys;print(json.load(open('.rehearsal_passed'))['$1'])"; }
[ "$(field operator_email)" = "$EMAIL" ] || die "The rehearsal was done for $(field operator_email), not $EMAIL. Use the same email."
AGE=$(python3 - <<'PY'
import json, datetime as d
t = d.datetime.strptime(json.load(open('.rehearsal_passed'))['passed_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=d.timezone.utc)
print(int((d.datetime.now(d.timezone.utc) - t).total_seconds()))
PY
)
[ "$AGE" -lt 172800 ] || die "The rehearsal is older than 48 hours. Run it again: bash scripts/rehearse.sh $EMAIL"

# -- 1. which database? (hidden input), and is it the one that was rehearsed? ----------------------------
if [ -z "${RF_PROD_URL:-}" ]; then   # RF_PROD_URL exists only so this script can be tested unattended
  read -rsp "Paste the PRODUCTION external database URL (typing is hidden), then press Enter: " RF_PROD_URL; echo
fi
case "$RF_PROD_URL" in postgres://*|postgresql://*) ;; *) die "That does not look like a PostgreSQL URL.";; esac
PROD="$RF_PROD_URL"
rest="${PROD#*://}"; rest="${rest##*@}"; HOST="${rest%%[:/]*}"; path="${rest#*/}"; DB="${path%%\?*}"
[ "$HOST" = "$(field source_host)" ] && [ "$DB" = "$(field source_db)" ] \
  || die "This is '$DB' on '$HOST', but the rehearsal was done on '$(field source_db)' on '$(field source_host)'. Different database - refusing."
MAJOR="$(field pg_major)"; BIN="/usr/lib/postgresql/$MAJOR/bin"
[ -x "$BIN/pg_dump" ] || die "PostgreSQL $MAJOR tools are missing in this machine. Run the rehearsal again first."

say "Production database: '$DB' on $HOST"
STATE="$("$BIN/psql" "$PROD" -Atc "select coalesce((select version_num from alembic_version limit 1),'none')" 2>/dev/null || echo none)"
[ "$STATE" = "none" ] || die "This database is already migrated (revision $STATE). Nothing to do."
echo "   Before you continue: pause the n8n workflow (so nothing is running), and keep the Render dashboard open."
echo "   The next step changes the live database. Type exactly   MIGRATE PRODUCTION   to continue."
read -rp "> " CONFIRM
[ "$CONFIRM" = "MIGRATE PRODUCTION" ] || die "Not confirmed. Nothing was changed."

# -- 2. backup first --------------------------------------------------------------------------------------
say "1. Backup (kept in the 'backups' folder - download it before you delete this machine)"
mkdir -p backups
BK="backups/before_batch1_$(date -u +%Y%m%dT%H%M%SZ).dump"
"$BIN/pg_dump" "$PROD" -Fc --no-owner -f "$BK" || die "Backup failed - nothing was changed."
N="$("$BIN/pg_restore" --list "$BK" | wc -l)"
[ "$N" -gt 20 ] || die "The backup looks empty ($N entries) - nothing was changed."
echo "   backup written: $BK ($(du -h "$BK" | cut -f1), $N entries)"

# -- 3. migrate -------------------------------------------------------------------------------------------------
say "2. Migrating"
if [ ! -x "$REPO_ROOT/.venv-rehearsal/bin/python" ]; then die "Run the rehearsal first (it sets up the tools)."; fi
# shellcheck disable=SC1091
. "$REPO_ROOT/.venv-rehearsal/bin/activate"
cd "$REPO_ROOT/apps/backend/src"
export RECONFLOW_DATABASE_URL="$PROD"
alembic stamp 0001
alembic -x "operator_email=$EMAIL" upgrade head
CUR="$(alembic current 2>/dev/null | tail -1)"
echo "   alembic says: $CUR"

say "3. Checking the result"
q() { "$BIN/psql" "$PROD" -Atc "$1"; }
[ "$(q "select version_num from alembic_version")" = "0002" ] || die "Revision is not 0002. Restore the backup and send me this output."
[ "$(q "select count(*) from users where role='PLATFORM_OPERATOR' and email='$EMAIL' and organization_id is null")" = "1" ] || die "The operator account is not as expected. Restore the backup."
[ "$(q "select count(*) from users where role<>'PLATFORM_OPERATOR' and organization_id is null")" = "0" ] || die "Some users have no organization. Restore the backup."
echo "   revision 0002, exactly one operator ($EMAIL), every other user belongs to an organization"
printf '\n\033[32mDATABASE MIGRATED.\033[0m Now deploy the new code (runbook, section C step 3) - the app will not start on the new code before this, and the old code keeps running until you deploy.\n'
printf 'Download the backup file from the "backups" folder (right-click it in the left panel -> Download).\n'
