---
name: restaurant-ledger-maintenance
description: Maintain this local restaurant ledger, screenshot review, cash-flow reconciliation, recipes, stock movements and backup integrity. Use when improving this repository's accounting or restaurant workflows.
---

# Restaurant ledger maintenance

Read README.md and docs/外网与支付接入.md. This skill was authored for this repository; it is not a payment integration or runtime dependency.

Preserve live data: data/ledger.sqlite3 contains real user entries; never populate it with fixtures or reset it. Before schema migration, take a consistent SQLite backup outside the data directory. Use temporary data directories for tests. Distribute source and private backups separately.

Core invariants:
- Store money as integer cents and stock as integer thousandths. Reject precision loss.
- Income minus net expenses is cash-flow balance, not profit or bank balance.
- Expense refunds reduce original expense category. Sales refunds reduce income. Transfers are excluded from operating totals.
- Do not merge separate transactions just because amount/time matches. Enforce explicit request/transaction identifiers.
- Checkout commits receipt, income and recipe stock movements atomically. Retry an uncertain checkout with its original persisted request ID, not a new one. Preserve timestamp/account/payload across retries.
- Food ingredients do not automatically return to stock after a refund. Recipe units must equal the raw material's stock units.
- Daily reconciliation stores a comparison total and audit event; it must never create another financial transaction. Recompute differences after edits, refunds or voids. Check versions before overwriting another device's comparison.
- Screenshot OCR produces drafts requiring confirmation. Retain uploaded original bytes and independent copies. Do not invent missing merchant purposes, years, payment accounts or originals.
- Generic marketplace names are not sufficient category rules. Apply confirmed purpose keywords.
- No live merchant API is configured. Never claim that manual receipt confirmation charges a customer's payment code. No keys or payment codes in source, browser logs or exported ledger backups.

Validation:
1. Run python -m unittest discover -s tests -v using an installed Python3.12.
2. For browser workflow changes, run scripts/smoke_restaurant.py and scripts/smoke_optimization.py with base Python + Playwright/Chromium.
3. For OCR changes, run scripts/smoke_ocr.py with .venv Python and verified models, using an explicitly synthetic fixture.
4. Run Ruff using ruff.toml; use pinned development requirements in a separate environment. Run pip-audit when dependencies change; report unreachable advisory service separately from a clean audit.
5. Package via scripts/package_release.py, verify the manifest, fresh extraction and private backup restoration.

Remote trial uses a local password and trusted HTTPS proxy, not the LAN connection code. No real tunnel connectivity has been verified in this cloud environment. Report actual device/network validation separately.
