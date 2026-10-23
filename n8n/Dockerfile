# n8n with the InboxGate workflow built in. On start it imports and publishes
# the workflow when that version isn't live yet, so a fresh instance works
# without anyone opening the editor. Version pinned to the one the workflow was
# tested on (deploy-marker.js also relies on its bundled pg driver).
FROM n8nio/n8n:2.44.5

COPY --chown=node:node inboxgate-workflow.json /home/node/workflows/inboxgate-workflow.json
COPY --chown=node:node deploy-marker.js webhook-check.js /home/node/
COPY --chown=node:node start.sh /home/node/start.sh

ENTRYPOINT ["tini", "--", "sh", "/home/node/start.sh"]
