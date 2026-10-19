from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from localledger.browser import find_chrome, open_chrome


class ChromeLaunchCase(unittest.TestCase):
    def test_detects_windows_chrome_with_spaces_in_path(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'Program Files'
            chrome = base / 'Google/Chrome/Application/chrome.exe'
            chrome.parent.mkdir(parents=True)
            chrome.touch()
            with patch('localledger.browser.shutil.which', return_value=None), patch.dict('os.environ', {'PROGRAMFILES': str(base)}, clear=True):
                self.assertEqual(find_chrome(), str(chrome))
                self.assertEqual(find_chrome(str(chrome)), str(chrome))

    def test_opens_url_as_single_argument_without_shell(self):
        with patch('localledger.browser.subprocess.Popen') as process:
            open_chrome('C:/Program Files/Google/Chrome/chrome.exe', 'http://127.0.0.1:8765')
            args, kwargs = process.call_args
            self.assertEqual(args[0], ['C:/Program Files/Google/Chrome/chrome.exe', '--new-window', 'http://127.0.0.1:8765'])
            self.assertFalse(kwargs.get('shell', False))
            with self.assertRaises(ValueError):
                open_chrome('chrome', 'file:///tmp/ledger.html')
