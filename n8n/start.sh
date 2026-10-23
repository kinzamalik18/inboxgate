#!/bin/sh
set -e

# On Render, use the platform's port and public URL unless they were set explicitly.
export N8N_PORT="${N8N_PORT:-${PORT:-5678}}"
if [ -z "$WEBHOOK_URL" ] && [ -n "$RENDER_EXTERNAL_URL" ]; then
  export WEBHOOK_URL="$RENDER_EXTERNAL_URL/"
fi

WORKFLOW=/home/node/workflows/inboxgate-workflow.json
HASH=$(sha256sum "$WORKFLOW" | cut -d' ' -f1)

# Each n8n CLI command takes close to a minute to boot on a small instance, so
# only import and publish when this version of the workflow isn't live yet.
# Free hosting restarts the container after every idle spell, and skipping the
# setup cuts those cold starts from about four minutes to one.
if node /home/node/deploy-marker.js check "$HASH"; then
  echo "Workflow already published at this version, starting n8n."
else
  # A live workflow can't be overwritten, so unpublish it first (fails
  # harmlessly on the very first boot, when it doesn't exist yet).
  n8n unpublish:workflow --id=InboxGateFlow001 || true
  n8n import:workflow --input="$WORKFLOW"
  n8n publish:workflow --id=InboxGateFlow001
  node /home/node/deploy-marker.js mark "$HASH" || echo "Could not record the workflow version; setup will run again next start."
fi

exec n8n start
