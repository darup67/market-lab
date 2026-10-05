#!/bin/bash
cd "$HOME/market-lab/perp-lab" || exit 1
PY="$HOME/.venvs/market-ml/bin/python"
"$PY" lab.py > results/report.txt 2>&1
"$PY" lab.py --signals > results/signals.txt 2>&1
"$PY" scan.py > results/setups.txt 2>&1
"$PY" daily_email.py >> results/email.log 2>&1
/usr/local/bin/git add results >/dev/null 2>&1
/usr/local/bin/git commit -q -m "perp-lab: daily results" -- results >/dev/null 2>&1 && /usr/local/bin/git push -q origin HEAD >/dev/null 2>&1
