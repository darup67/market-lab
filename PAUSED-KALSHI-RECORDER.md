# KALSHI RECORDER PAUSED October 8, 2026 (user request)
com.dhruv.marketlab (record.js: Kalshi 15-minute contracts, every 10 min) and com.dhruv.marketlab.report (07:00 recorder report email) are unloaded; plists renamed to .plist.disabled.
NOT paused: com.dhruv.marketlab.futures (the CME futures recorder).
WARNING: Kalshi keeps only about 2 days of settled markets, so the capture gap while this is paused CANNOT be backfilled.
Resume: for l in marketlab marketlab.report; do mv ~/Library/LaunchAgents/com.dhruv.$l.plist.disabled ~/Library/LaunchAgents/com.dhruv.$l.plist; launchctl load ~/Library/LaunchAgents/com.dhruv.$l.plist; done; then rm this file. The first report afterwards will list the gap.
