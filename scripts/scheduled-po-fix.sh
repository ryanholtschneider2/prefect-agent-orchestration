#!/usr/bin/env bash
# Scheduled fix run — invoked ~2h after dispatch by systemd-run --on-active=2h.
# Does: rebuild po tool venv on py3.13, retry tyf.1 if j2p closed, re-dispatch epic.
set -uo pipefail

LOG=/tmp/scheduled-po-fix-$(date -u +%Y%m%dT%H%M%SZ).log
exec >>"$LOG" 2>&1

REPO="${PO_REPO:-/home/ryan-24/Desktop/Code/personal/prefect-orchestration}"
PACK="${PO_PACK:-/home/ryan-24/Desktop/Code/personal/prefect-orchestration/packs/po-formulas-software-dev}"
SLACK_CH=C08LB4V9ZJ8

# Slack helper — falls back silently if token absent
slack() {
    local msg="$1"
    if [[ -n "${SLACK_BOT_TOKEN:-}" ]]; then
        curl -s -X POST https://slack.com/api/chat.postMessage \
            -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
            -H "Content-Type: application/json; charset=utf-8" \
            --data "$(jq -n --arg ch "$SLACK_CH" --arg t "$msg" \
                '{channel:$ch, text:$t}')" >/dev/null || true
    fi
}

cd "$REPO" || { slack ":warning: po-fix: cannot cd to repo"; exit 1; }
unset ANTHROPIC_API_KEY  # ensure OAuth path for any claude calls

# 1. Rebuild po tool venv on py3.13
echo "=== $(date -u) reinstalling po on py3.13 ==="
uv tool uninstall prefect-orchestration || true
uv tool install --python 3.13 --editable "$REPO" || {
    slack ":x: po-fix: \`uv tool install\` failed; see $LOG"
    exit 1
}
[[ -d "$PACK" ]] && po install --editable "$PACK" 2>&1 || true
po update 2>&1 || true

# 2. Verify
echo "=== verifying po watch ==="
if ! po watch --help >/dev/null 2>&1; then
    slack ":x: po-fix: \`po watch --help\` still broken after reinstall; see $LOG"
    exit 1
fi
echo "po watch OK"

# 3. Check j2p / tyf.1 state and retry if appropriate
j2p_status=$(bd show prefect-orchestration-j2p --json 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)[0].get("status","?"))' \
    2>/dev/null || echo "?")
tyf1_status=$(bd show prefect-orchestration-tyf.1 --json 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)[0].get("status","?"))' \
    2>/dev/null || echo "?")
echo "j2p=$j2p_status  tyf.1=$tyf1_status"

retried=""
if [[ "$j2p_status" == "closed" && "$tyf1_status" == "open" ]]; then
    echo "=== retrying tyf.1 ==="
    PO_BACKEND=tmux po retry prefect-orchestration-tyf.1 --keep-sessions \
        >>"$LOG" 2>&1 &
    retried="tyf.1 retry kicked off"
elif [[ "$j2p_status" == "in_progress" ]]; then
    retried="j2p still in_progress; tyf.1 retry skipped"
elif [[ "$tyf1_status" == "in_progress" ]]; then
    retried="tyf.1 already in_progress; not retrying"
else
    retried="j2p=$j2p_status tyf.1=$tyf1_status; no action"
fi

# 4. (Manual epic re-dispatch left for human — only safe once tyf.1 actually closes)
slack ":wrench: po-fix complete
- po venv: rebuilt on py3.13
- po watch --help: OK
- j2p=$j2p_status, tyf.1=$tyf1_status
- action: $retried
- log: $LOG
- next: once tyf.1 closes, run \`PO_BACKEND=tmux po run epic --epic-id prefect-orchestration-tyf --rig prefect-orchestration --rig-path $REPO\`"

echo "=== done ==="
