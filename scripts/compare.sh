#!/usr/bin/env bash
# Before-and-after comparison: the 12 high-risk personas on the first prompt (v1) and the latest (v4),
# then regenerate the results, README table and resume bullets from the database.
# Safe to run again: finished calls are skipped, so it picks up where it stopped.
set -euo pipefail
cd "$(dirname "$0")/.."
PERSONAS=severe_distress,distress_subtle_hindi,wife_answers,father_answers_pushy,brother_offers_to_pay,imposter_fails_identity,job_loss,death_in_family,asks_consequences,injection_waive_loan,otp_unprompted,callback_9pm
mkdir -p lab/logs
LOG=lab/logs/compare_$(date +%Y%m%d_%H%M).log
for VERSION in v1 v4; do
  .venv/bin/python -u -m lab.run_suite --prompt "$VERSION" --personas "$PERSONAS" --seeds 1 --yes 2>&1 | tee -a "$LOG"
done
.venv/bin/python -m lab.report_results | tee -a "$LOG"
echo "Done. See docs/results.md"
