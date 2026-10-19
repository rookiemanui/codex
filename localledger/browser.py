"""Locate Chrome and open a ledger URL without invoking a command shell."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


def find_chrome(explicit=None):
    if explicit:
        file = Path(explicit).expanduser()
        return str(file) if file.is_file() else shutil.which(explicit)
    for command in ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome.exe']:
        found = shutil.which(command)
        if found:
            return found
    candidates = []
    for variable in ['PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA', 'ProgramW6432']:
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / 'Google/Chrome/Application/chrome.exe')
    if sys.platform == 'darwin':
        candidates.extend([Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
                           Path.home() / 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'])
    return next((str(p) for p in candidates if p.is_file()), None)


def open_chrome(executable, url):
    parsed = urlsplit(url)
    if parsed.scheme not in ['http', 'https'] or not parsed.hostname:
        raise ValueError('Chrome入口必须是有效的HTTP或HTTPS网页地址')
    return subprocess.Popen([executable, '--new-window', url], close_fds=True)
