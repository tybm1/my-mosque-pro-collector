#!/usr/bin/env bash
# Install headless Chromium for Playwright. The Ubuntu package mirrors sometimes fail to resolve on
# GitHub runners (seen 11 Nov 2026, each failed try taking ~10 min); try twice with a time limit, then
# install the browser alone (runner images already carry most of its libraries) so the run still goes ahead.
for i in 1 2; do
  timeout 420 python -m playwright install --with-deps chromium && exit 0
  echo "browser install with system packages failed (try $i)"; sleep 20
done
echo "::warning::system packages for the browser could not be installed; installing the browser alone"
python -m playwright install chromium
