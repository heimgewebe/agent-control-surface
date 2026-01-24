#!/usr/bin/env bash
set -euo pipefail

mkdir -p ~/.config/systemd/user

# Remove legacy unit if present (prevents drift)
systemctl --user disable --now jules-panel.service >/dev/null 2>&1 || true
rm -f ~/.config/systemd/user/jules-panel.service
rm -f ~/.config/systemd/user/default.target.wants/jules-panel.service

cp -f systemd/agent-control-surface.service ~/.config/systemd/user/agent-control-surface.service

systemctl --user daemon-reload
systemctl --user enable --now agent-control-surface.service
systemctl --user status agent-control-surface.service --no-pager
