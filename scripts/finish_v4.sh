#!/usr/bin/env bash
# Run v4 on the 4 high-risk personas v1 already ran, retrying every 20 minutes
# while Groq's rolling daily token limit frees up, then regenerate the results.
set -uo pipefail
cd "$(dirname "$0")/.."
PERSONAS=severe_distress,distress_subtle_hindi,job_loss,death_in_family
mkdir -p lab/logs
LOG=lab/logs/finish_v4_$(date +%Y%m%d_%H%M).log
# Groq's daily token limit is a rolling 24 hours. Optional first argument: wait until this time (HH:MM, local)
# before starting, so retries do not waste the little allowance that frees up in between.
if [ -n "${1:-}" ]; then
  echo "$(date +%H:%M) waiting until $1 for Groq's daily limit to reset" | tee -a "$LOG"
  while [ "$(date +%H:%M)" != "$1" ]; do sleep 30; done
fi
done_count() {
  sqlite3 data/collections.db "select count(*) from runs r join suites s using(suite_id) where r.status='ok' and s.prompt_version='v4' and r.persona_id in ('severe_distress','distress_subtle_hindi','job_loss','death_in_family')"
}
while [ "$(done_count)" -lt 4 ]; do
  echo "$(date +%H:%M) v4 done: $(done_count) of 4" | tee -a "$LOG"
  .venv/bin/python -u -m lab.run_suite --prompt v4 --personas "$PERSONAS" --seeds 1 --yes 2>&1 | grep -E "started|s1:|Stopped" | tee -a "$LOG"
  [ "$(done_count)" -ge 4 ] && break
  echo "$(date +%H:%M) waiting 30 minutes for the daily limit to free up" | tee -a "$LOG"
  sleep 1800
done
.venv/bin/python -m lab.report_results | tee -a "$LOG"
echo "$(date +%H:%M) all 4 done" | tee -a "$LOG"
