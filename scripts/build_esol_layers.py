#!/usr/bin/env python3
"""Stub - canonical script is build_esol_v2.py.

Run with:
    python3 scripts/build_esol_v2.py
"""
import runpy, pathlib
runpy.run_path(str(pathlib.Path(__file__).parent / 'build_esol_v2.py'),
               run_name='__main__')
