"""Failure diagnostics for the tests that move whole leagues (persist, upgrade, archive).

Importing this module (a) records, in order, every test the process has run, and (b) registers a context hook with
`persist.mismatch_report`, so a `StateMismatch` report names the tests that ran before it, the module-level settings that other tests could
have changed, the random module's own state, and the temporary directory in use. Set DFL_DIAG_DIR to have the report written to a file.
"""
import hashlib
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import persist  # noqa: E402

ORDER = []
_INSTALLED = []


def _globals():
    out = {}
    try:
        from dfl.ledger import rules as R
        out["rules.CAP"] = str(R.CAP)                   # module constants: nothing a league does may change them any more
        from dfl.engine import production, squad
        out["production._CACHE.keys"] = sorted(production._CACHE)
        out["squad._TABLES.loaded"] = squad._TABLES is not None
    except Exception as e:                              # a diagnostic must never be the failure
        out["error"] = str(e)
    out["random.global_state_sha"] = hashlib.sha256(repr(random.getstate()).encode()).hexdigest()[:16]
    out["tempdir"] = tempfile.gettempdir()
    out["env.PYTHONHASHSEED"] = os.environ.get("PYTHONHASHSEED")
    return out


def tests_run_so_far():
    return {"count": len(ORDER), "last_40": ORDER[-40:], "all": list(ORDER)}


def module_globals():
    return _globals()


def install():
    if _INSTALLED:
        return
    _INSTALLED.append(True)
    original = unittest.TestCase.run

    def run(self, result=None):
        ORDER.append(self.id())
        return original(self, result)
    unittest.TestCase.run = run
    persist.DIAG_CONTEXT.append(tests_run_so_far)
    persist.DIAG_CONTEXT.append(module_globals)


install()
