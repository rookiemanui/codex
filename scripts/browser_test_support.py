"""Use an installed browser for portable local browser tests."""
import os

from localledger.desktop import find_app_browser


def launch_browser(playwright):
    executable = os.environ.get('LEDGER_BROWSER') or find_app_browser()
    if not executable:
        raise RuntimeError('Browser tests require Chrome/Edge or LEDGER_BROWSER')
    return playwright.chromium.launch(executable_path=executable, headless=True,
                                      args=['--no-sandbox'] if os.name != 'nt' else [])
