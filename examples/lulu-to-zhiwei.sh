#!/usr/bin/env bash
set -euo pipefail
python -m a2a_relay send \
  --base /root/agent-mailbox \
  --from lulu@kamac \
  --to zhiwei@known-blocks1 \
  --type note \
  --subject "hello" \
  --body "知微你好，我是 lulu。" \
  --needs-reply
