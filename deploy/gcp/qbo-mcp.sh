#!/bin/sh
# QuickBooks MCP for Claude, run on the drapps VM over stdio. Called from bodhi through the IAP tunnel:
#   gcloud compute ssh drapps --zone us-central1-a --tunnel-through-iap --quiet --command 'sudo /opt/drscca/qbo-mcp.sh'
# Runs as drapps so it shares the one token file with the nightly sync. Delete stays disabled.
exec sudo -u drapps env HOME=/opt/drscca QUICKBOOKS_TOKEN_STORE_PATH=/opt/drscca/data/secrets/qbo/qbo.env \
  QUICKBOOKS_DISABLE_WRITE=false QUICKBOOKS_DISABLE_UPDATE=false QUICKBOOKS_DISABLE_DELETE=true \
  node /opt/drscca/qbo-mcp/dist/index.js
