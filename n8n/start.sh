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

n8n start &
N8N_PID=$!
trap 'kill -TERM "$N8N_PID" 2>/dev/null' TERM INT

# n8n unpublishes the workflow if activating it fails (a database timeout is
# enough), and the webhook then answers 404 until someone republishes it.
# Watch the webhook, and if it goes missing, stop so the host restarts the
# instance; the next start sees it unpublished and publishes it again.
if ! node /home/node/webhook-check.js "http://127.0.0.1:$N8N_PORT/webhook/inboxgate-inbound" "$N8N_PID"; then
  echo "Webhook is not registered, stopping n8n so the instance restarts."
  kill -TERM "$N8N_PID" 2>/dev/null || true
  wait "$N8N_PID" 2>/dev/null || true
  exit 1
fi

wait "$N8N_PID"
