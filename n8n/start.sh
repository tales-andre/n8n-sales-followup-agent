#!/bin/sh
# Container entrypoint: load credentials and workflows from the repo, then start n8n.
# The import only runs when ./workflows or the connection settings changed since the
# last start (each n8n CLI call takes a while), so normal restarts are quick.
set -e

CREDS=/tmp/credentials.json
MARK=/home/node/.n8n/.demo-import-checksum
node /demo/n8n/render-credentials.js > "$CREDS"
SUM=$(cat "$CREDS" /demo/workflows/*.json /demo/n8n/start.sh | md5sum | cut -d' ' -f1)

if [ "$(cat "$MARK" 2>/dev/null)" != "$SUM" ]; then
  echo "[demo] importing credentials and workflows from the repo"
  n8n import:credentials --input="$CREDS"
  if [ ! -f /home/node/.n8n/.demo-outlook-placeholder ]; then
    node /demo/n8n/render-credentials.js outlook > /tmp/outlook.json
    n8n import:credentials --input=/tmp/outlook.json
    rm -f /tmp/outlook.json
    touch /home/node/.n8n/.demo-outlook-placeholder
  fi
  n8n import:workflow --separate --input=/demo/workflows
  # Switch the three workflows on (in n8n 2.x "publish" = active).
  # The error workflow must be published too, or n8n refuses to call it.
  for id in wfErrorAlert0001 wfDailyDrafts001 wfApprovalQueue1; do
    n8n publish:workflow --id="$id"
  done
  echo "$SUM" > "$MARK"
else
  echo "[demo] workflows unchanged since last start, skipping import"
fi
rm -f "$CREDS"

exec n8n start
