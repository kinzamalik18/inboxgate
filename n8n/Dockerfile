# n8n with the InboxGate workflow built in. On every start it imports the
# workflow and publishes it, so a fresh instance works without anyone opening
# the editor. Version pinned to the one the workflow was tested on.
FROM n8nio/n8n:2.44.5

COPY --chown=node:node inboxgate-workflow.json /home/node/workflows/inboxgate-workflow.json
COPY --chown=node:node start.sh /home/node/start.sh

ENTRYPOINT ["tini", "--", "sh", "/home/node/start.sh"]
