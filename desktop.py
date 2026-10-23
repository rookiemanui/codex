#!/usr/bin/env python3
"""Launch the Windows ledger controller (also the packaged EXE entry point)."""
from localledger.desktop import main


if __name__ == '__main__':
    raise SystemExit(main())
