#!/usr/bin/env bash
set -euo pipefail

mkdir -p ~/.config/systemd/user
cp -f systemd/jules-panel.service ~/.config/systemd/user/jules-panel.service

systemctl --user daemon-reload
systemctl --user enable --now jules-panel.service
systemctl --user status jules-panel.service --no-pager
