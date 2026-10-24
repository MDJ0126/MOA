"""Regression tests; run from the repository root."""
import os
from pathlib import Path
import sys
_root=Path(__file__).resolve().parent.parent
_paths=[str(_root/'src'),str(_root/'tools'),str(_root)]
sys.path[:0]=_paths
os.environ['PYTHONPATH']=os.pathsep.join(_paths+([os.environ['PYTHONPATH']] if os.environ.get('PYTHONPATH') else []))
