# Pinned runtimes and dependencies

Written 2026-09-28, after the coin-launch scorer failed silently for 2 days. The cause: `libomp` disappeared along with `/opt/homebrew`, so LightGBM and XGBoost couldn't load.

## What's pinned

| Runtime | Pinned to | Used by |
|---|---|---|
| Node **v22.22.3** | `~/.local/bin/node` → `~/.local/opt/node-v22.22.3/bin/node` (own copy; was borrowed from `~/.hermes`) | flip-notifier (headless + kalshi watcher), zillow-agent, market-lab recorders |
| Python **3.11.15** (uv) | venv `bin/python` → `~/.local/share/uv/python/cpython-3.11.15-macos-aarch64-none` (exact patch, not the floating `cpython-3.11` alias) | `~/.venvs/market-ml` (market-iv-agent merged in on 2026-09-28; its old `.venv` is kept as `.venv.retired-20260928` for rollback) |
| `~/.venvs/market-ml` packages | `market-ml.lock.txt` (122 packages) | coin-launch, event-desk, paper-lab, kalshi-btc, watchdog |
| market-iv packages | merged into `market-ml.lock.txt` (yfinance 1.7.0, openpyxl 3.1.5, installed with the lock as a constraint, so nothing existing changed) | market-iv-agent |
| OpenMP (`libomp`) | torch's bundled copy: coinlaunch LaunchAgents set `DYLD_FALLBACK_LIBRARY_PATH=<venv>/…/torch/lib` | LightGBM, XGBoost |

## Watchdog coverage (`ops/watchdog.py`)
- **launch agents**, every pass: alerts when any `com.dhruv.*` job's last exit is non-zero on 2 consecutive passes.
- **platform**, hourly:
  - the pinned symlinks above still resolve to their exact targets;
  - `import_check.py` imports every package in each venv, with the LaunchAgent environment, and alerts on any failure not in `<venv>.import-baseline.json`;
  - disk has 15 GB or more free.

To accept a new known-harmless failure, delete the baseline file; the next hourly pass records a fresh one.

## Rebuild a venv exactly
```bash
uv python install 3.11.15
uv venv --python 3.11.15 ~/.venvs/market-ml.new
uv pip install --python ~/.venvs/market-ml.new/bin/python -r ~/market-lab/ops/deps/market-ml.lock.txt
# verify, then swap:
DYLD_FALLBACK_LIBRARY_PATH=~/.venvs/market-ml.new/lib/python3.11/site-packages/torch/lib ~/.venvs/market-ml.new/bin/python ~/market-lab/ops/deps/import_check.py
mv ~/.venvs/market-ml ~/.venvs/market-ml.old && mv ~/.venvs/market-ml.new ~/.venvs/market-ml
```

## Rules
- Don't `pip install -U`, `uv python upgrade`, or `brew upgrade` anything these jobs use without re-running `import_check.py` and updating the lock (`uv pip freeze --python <venv>/bin/python > <lock>`).
- A new Node version goes into its own `~/.local/opt/node-vX` dir. Then repoint the symlink and update `PINS` in watchdog.py.
