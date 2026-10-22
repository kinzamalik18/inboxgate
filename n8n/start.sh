#!/bin/sh
set -e

# On Render, use the platform's port and public URL unless they were set explicitly.
export N8N_PORT="${N8N_PORT:-${PORT:-5678}}"
if [ -z "$WEBHOOK_URL" ] && [ -n "$RENDER_EXTERNAL_URL" ]; then
  export WEBHOOK_URL="$RENDER_EXTERNAL_URL/"
fi

# The setup below runs three n8n CLI commands, and on a small instance each one
# takes close to a minute to boot. Hosts like Render fail a deploy when no port
# opens in time, so answer on the port with a "starting" page until n8n is ready.
node -e '
require("http").createServer((req, res) => {
  res.writeHead(503, { "Content-Type": "text/plain", "Retry-After": "30" });
  res.end("n8n is starting. Try again in a minute.\n");
}).listen(process.env.N8N_PORT, "0.0.0.0");
' &
PLACEHOLDER_PID=$!

# Re-import on every start so the deployed workflow always matches the repo.
# A live workflow can't be overwritten, so unpublish it first (fails harmlessly
# on the very first boot, when it doesn't exist yet).
n8n unpublish:workflow --id=InboxGateFlow001 || true
n8n import:workflow --input=/home/node/workflows/inboxgate-workflow.json
n8n publish:workflow --id=InboxGateFlow001

kill "$PLACEHOLDER_PID" 2>/dev/null || true
wait "$PLACEHOLDER_PID" 2>/dev/null || true
exec n8n start
