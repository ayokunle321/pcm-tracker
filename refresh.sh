#!/bin/sh
# Re-render the page whenever a new measurement lands, so the served copy is
# never behind the data. Rendering costs milliseconds, so this polls rather
# than watching, and only renders when something actually changed.
HERE=$(dirname "$(readlink -f "$0")")
ORDER=${1:-dedup-squashed~21..dedup-squashed}
cd "$HERE" || exit 1
while true; do
  newest=$(ls -t data/*.json 2>/dev/null | head -1)
  if [ -n "$newest" ] && { [ ! -f docs/index.html ] || [ "$newest" -nt docs/index.html ]; }; then
    python3 report.py --order "$ORDER"
  fi
  sleep 20
done
