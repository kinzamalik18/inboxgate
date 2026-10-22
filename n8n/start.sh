#!/bin/sh
set -e

# On Render, use the platform's port and public URL unless they were set explicitly.
export N8N_PORT="${N8N_PORT:-${PORT:-5678}}"
if [ -z "$WEBHOOK_URL" ] && [ -n "$RENDER_EXTERNAL_URL" ]; then
  export WEBHOOK_URL="$RENDER_EXTERNAL_URL/"
fi

# Re-import on every start so the deployed workflow always matches the repo.
# A live workflow can't be overwritten, so unpublish it first (fails harmlessly
# on the very first boot, when it doesn't exist yet).
n8n unpublish:workflow --id=InboxGateFlow001 || true
n8n import:workflow --input=/home/node/workflows/inboxgate-workflow.json
n8n publish:workflow --id=InboxGateFlow001
exec n8n start
