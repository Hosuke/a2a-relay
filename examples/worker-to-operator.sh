#!/usr/bin/env bash
set -euo pipefail
python -m a2a_relay send \
  --base /root/agent-mailbox \
  --from worker@example \
  --to operator@example \
  --type note \
  --subject "hello" \
  --body "知微你好，我是 worker。" \
  --needs-reply
