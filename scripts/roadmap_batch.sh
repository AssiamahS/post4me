#!/bin/zsh
# Generate roadmap months sequentially and push each one as it lands.
# Usage: scripts/roadmap_batch.sh 2 6
set -u
cd "$(dirname "$0")/.."
for n in $(seq "$1" "$2"); do
  f=$(printf "roadmap/month-%02d.json" "$n")
  [[ -s "$f" ]] && { echo "skip $f"; continue; }
  python3 scripts/roadmap.py --month "$n" || { echo "month $n failed"; continue; }
  git add "$f" && git commit -q -m "roadmap: month $n scripts" && git pull --rebase -q origin main && git push -q origin main && echo "pushed $f"
done
