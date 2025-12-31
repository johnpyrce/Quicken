#!/usr/bin/env bash
#
# Launch chrome, opening the schwab web site, with remote debugging port
# open for later attachment by playwright for remote execution.
# 
# This starts with a unique profile directory for playwright.
#
#
PROFILE=$(cygpath -w /c/Users/John/pw-schwab)

"/cygdrive/c/Program Files/Google/Chrome/Application/chrome.exe" \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE" \
  https://www.schwab.com
