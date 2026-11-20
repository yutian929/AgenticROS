#!/usr/bin/env python3
"""Isolated ALOHA native RViz integration; two robots, one Server, no motion."""
import subprocess
import sys
from pathlib import Path
if __name__=='__main__':
    subprocess.run([sys.executable,str(Path(__file__).with_name('check_lan.py')),
                    '--viewer-only','--environment','aloha_tabletop',*sys.argv[1:]],check=True)
