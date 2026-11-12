# Project rules

Keep this experimental native macOS Cast recorder minimal. Reuse Open Screen
for discovery, authentication and streaming; never replace Cast with screen
capture. Do not touch the user's normal Chrome profile. Do not commit or push
without an explicit request. Keep development credentials and recordings out
of Git. No Docker runtime, GUI, database or cloud services. A native local
HTTP control service is authorized; Docker/VM clients may use its API.

Before committing, inspect the changes being committed for sensitive information
and credentials, including tokens, passwords, private keys and secrets in logs
or configuration. Remove or redact any such information before committing.

Validate shell syntax and the offline recorder test after recorder changes.
Report actual Chrome casting separately from synthetic media tests.
