#!/usr/bin/env bash
# Start everything in the background. Logs in data/*.log.   Stop: ./dev.sh stop
#   docs MCP      :9102   our MCP server over Ramp's public API reference
#   Ramp adapter  :9101   A2A seller (uses the docs MCP)
#   demo sellers  :9103   two FICTIONAL A2A sellers
#   Brex adapter  :9104   A2A seller (uses the docs MCP)
#   live adapters :9106 Airtable, :9107 monday.com (real ops via the vendors' official MCP)
#   sandbox MCP   :9105   our MCP server of SYNTHETIC vendor-shaped transactions (demos)
#   web UI        :8700   buyer app -> http://127.0.0.1:8700
cd "$(dirname "$0")"
for m in seller.live_server docs_mcp.server sandbox_mcp.server seller.server seller.vendor_server seller.demo_sellers web.app; do pkill -f -- "-m $m" 2>/dev/null; done; sleep 0.5
[ "$1" = "stop" ] && { echo "stopped"; exit 0; }
mkdir -p data
.venv/bin/python -m docs_mcp.server     > data/docs_mcp.log     2>&1 &
.venv/bin/python -m sandbox_mcp.server  > data/sandbox_mcp.log  2>&1 &
.venv/bin/python -m seller.live_server airtable > data/airtable_live.log 2>&1 &
.venv/bin/python -m seller.live_server monday   > data/monday_live.log   2>&1 &
.venv/bin/python -m seller.server       > data/seller.log       2>&1 &
.venv/bin/python -m seller.vendor_server > data/brex_seller.log  2>&1 &
.venv/bin/python -m seller.demo_sellers > data/demo_sellers.log 2>&1 &
.venv/bin/python -m web.app             > data/web.log          2>&1 &
for url in http://127.0.0.1:9102/mcp http://127.0.0.1:9105/mcp http://127.0.0.1:9101/.well-known/agent-card.json \
           http://127.0.0.1:9103/ledgerleaf/.well-known/agent-card.json \
           http://127.0.0.1:9104/.well-known/agent-card.json http://127.0.0.1:9106/.well-known/agent-card.json http://127.0.0.1:9107/.well-known/agent-card.json http://127.0.0.1:8700/; do
  for i in $(seq 30); do curl -s -o /dev/null "$url" && break; sleep 0.3; done
  curl -s -o /dev/null "$url" && echo "up   $url" || echo "DOWN $url  (see data/*.log)"
done
echo; echo "Open http://127.0.0.1:8700/judas  (Discovery Lab: http://127.0.0.1:8700)"
