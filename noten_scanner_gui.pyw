#!/usr/bin/env python3
"""Noten-Scanner mit Fenster. Unter Windows reicht ein Doppelklick auf diese Datei."""
import multiprocessing

from notenscanner.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
