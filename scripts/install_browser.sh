#!/usr/bin/env bash
# Install headless Chromium for Playwright. The Ubuntu package mirrors sometimes fail to resolve on
# GitHub runners (seen 11 Nov 2026); retry, then fall back to the browser alone so the run still goes ahead.
for i in 1 2 3; do
  python -m playwright install --with-deps chromium && exit 0
  echo "browser install with system packages failed (try $i); retrying in 30s"; sleep 30
done
echo "::warning::system packages for the browser could not be installed; installing the browser alone"
python -m playwright install chromium
