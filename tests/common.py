"""Shared fixtures. Building an aged volume takes about a second, so do it once."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.bench import apply_layout, make_case, measure   # noqa: E402

_case = None


def case():
    global _case
    if _case is None:
        _case = make_case()
    return _case
