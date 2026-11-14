#!/usr/bin/env python3
"""Noten-Scanner (Kommandozeile). Hilfe: python noten_scanner.py --help"""
import multiprocessing
import sys

from notenscanner.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
