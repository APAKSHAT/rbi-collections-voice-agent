#!/usr/bin/env bash
# Start everything a test call needs, in one terminal:
#   1. the FastAPI backend (Meera's tools and Retell's webhooks)
#   2. an ngrok tunnel so Retell can reach it
#   3. both Retell agents updated with the tunnel's address
#
# Usage:  scripts/dev.sh            (uses prompt v3)
#         scripts/dev.sh v4
# Stop with Ctrl+C. Keep this terminal open while you make test calls.

set -euo pipefail
cd "$(dirname "$0")/.."
PROMPT="${1:-v3}"
PY=.venv/bin/python
mkdir -p lab/logs

cleanup() {
  echo
  echo "Stopping backend and ngrok."
  kill "${BACKEND_PID:-}" "${NGROK_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting backend on port 8000..."
.venv/bin/uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000 >lab/logs/backend.log 2>&1 &
BACKEND_PID=$!
for _ in $(seq 1 40); do
  curl -s --max-time 1 http://127.0.0.1:8000/health >/dev/null && break
  sleep 0.25
done
curl -s --max-time 1 http://127.0.0.1:8000/health >/dev/null || { echo "Backend did not start. See lab/logs/backend.log"; exit 1; }

echo "Starting ngrok..."
ngrok http 8000 --log stdout >lab/logs/ngrok.log 2>&1 &
NGROK_PID=$!
URL=""
for _ in $(seq 1 40); do
  URL=$(curl -s --max-time 1 http://127.0.0.1:4040/api/tunnels 2>/dev/null \
    | "$PY" -c "import json,sys; t=json.load(sys.stdin)['tunnels']; print(next((x['public_url'] for x in t if x['public_url'].startswith('https')), ''))" 2>/dev/null || true)
  [ -n "$URL" ] && break
  sleep 0.25
done
[ -n "$URL" ] || { echo "ngrok did not start. See lab/logs/ngrok.log"; exit 1; }
echo "Public URL: $URL"

"$PY" - "$URL" <<'EOF'
import re, sys
from pathlib import Path
env = Path(".env")
text = env.read_text()
line = f"BACKEND_URL={sys.argv[1]}"
text = re.sub(r"^BACKEND_URL=.*$", line, text, flags=re.M) if re.search(r"^BACKEND_URL=", text, re.M) else text + f"\n{line}\n"
env.write_text(text)
EOF

echo "Updating the Hindi and English agents with prompt $PROMPT..."
"$PY" agent_config/create_agent.py --prompt "$PROMPT" --lang hi | tail -1
"$PY" agent_config/create_agent.py --prompt "$PROMPT" --lang en | tail -1

"$PY" - <<'EOF'
import json
ids = json.load(open("agent_config/retell_ids.json"))
print()
for lang, name in (("hi", "Hindi"), ("en", "English")):
    if lang in ids:
        print(f"{name} test call: https://dashboard.retellai.com/agents/{ids[lang]['agent_id']}")
print()
print("Test borrowers. Hindi: Sunil Verma, born 1991, Nagpur. English: Mohit Sethi, born 1999, Mumbai.")
EOF
echo "Ready. Keep this terminal open. Press Ctrl+C to stop."
wait
