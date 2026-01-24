#!/usr/bin/env bash
set -euo pipefail

mkdir -p ~/.config/systemd/user
cp -f systemd/agent-control-surface.service ~/.config/systemd/user/agent-control-surface.service

systemctl --user daemon-reload
systemctl --user enable --now agent-control-surface.service
systemctl --user status agent-control-surface.service --no-pager
