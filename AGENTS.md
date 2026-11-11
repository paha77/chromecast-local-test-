# Project rules

Keep this experimental native macOS Cast recorder minimal. Reuse Open Screen
for discovery, authentication and streaming; never replace Cast with screen
capture. Do not touch the user's normal Chrome profile. Do not commit or push
without an explicit request. Keep development credentials and recordings out
of Git. No Docker, GUI, server, database or cloud services.

Validate shell syntax and the offline recorder test after recorder changes.
Report actual Chrome casting separately from synthetic media tests.
