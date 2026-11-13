"""CI configuration must refer only to files that exist, and to the specification edition that is actually in the repository.

Found 2026-11-11: .github/workflows/ci.yml still named docs/spec/DFL_Design_Document_Ed8.54.md, an edition that is no longer in the
repository, so the 'Specification hash' step could never pass. These tests fail on that and on any later drift of the same kind.
"""
import ast
import glob
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CI = os.path.join(ROOT, ".github", "workflows", "ci.yml")
PATH_RE = re.compile(r"(?<![\w/.-])((?:tools|tests|docs|data|deploy|src|site)/[\w./-]+)")


def ci_run_lines():
    out = []
    with open(CI, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"\s*run:\s*(.*\S)\s*$", line)
            if m:
                out.append(m.group(1))
    return out


class CiReferences(unittest.TestCase):
    def test_ci_file_exists_and_has_run_steps(self):
        self.assertTrue(os.path.isfile(CI))
        self.assertGreater(len(ci_run_lines()), 5)

    def test_every_repository_path_in_a_run_step_exists(self):
        missing = []
        for line in ci_run_lines():
            for p in PATH_RE.findall(line):
                if p.endswith("/"):
                    ok = os.path.isdir(os.path.join(ROOT, p))
                else:
                    ok = os.path.exists(os.path.join(ROOT, p))
                # 'runs/ci' style output folders are not in the tree; only the listed source roots are checked
                if not ok:
                    missing.append((p, line))
        self.assertEqual(missing, [], "CI names files that do not exist: %r" % (missing,))

    def test_ci_uses_the_only_specification_edition_in_the_repository(self):
        specs = sorted(glob.glob(os.path.join(ROOT, "docs", "spec", "DFL_Design_Document_Ed*.md")))
        self.assertEqual(len(specs), 1, "expected exactly one specification edition in docs/spec, found %r" % (specs,))
        rel = os.path.relpath(specs[0], ROOT)
        docs = [m for line in ci_run_lines() for m in re.findall(r"--doc\s+(\S+)", line)]
        self.assertTrue(docs, "CI no longer checks the specification hash")
        for d in docs:
            self.assertEqual(d, rel)

    def test_readme_and_ci_name_the_same_edition(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        docs = {m for line in ci_run_lines() for m in re.findall(r"--doc\s+(\S+)", line)}
        for d in docs:
            self.assertIn(d, readme, "README does not mention the specification file CI validates")


HISTORY_USE = re.compile(r"cat-file|ls-tree|merge-base|'show'|git archive|\"git\", \"-C\", ROOT, \"cat-file\"|git -C \{self\.repo\} archive")


class CiHistory(unittest.TestCase):
    def test_checkout_fetches_history_when_tests_read_commits(self):
        """Found 2026-11-11: tests/test_vtest.py asserts that the pinned candidate commit exists in the repository, and other tests `git show` / `git archive` named commits.
        actions/checkout fetches ONE commit by default, so on GitHub those tests could never pass. The checkout step must fetch the whole history."""
        users = []
        for f in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py"))):
            if f.endswith("test_ci_config.py"):
                continue
            with open(f, encoding="utf-8") as fh:
                if HISTORY_USE.search(fh.read()):
                    users.append(os.path.basename(f))
        self.assertTrue(users, "no test reads git history any more: delete this test and the fetch-depth setting")
        with open(CI, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"uses:\s*actions/checkout@\S+\s*\n(\s+with:\s*\n(?:\s+\S.*\n)+)?", text)
        self.assertIsNotNone(m, "no checkout step")
        self.assertIn("fetch-depth: 0", m.group(0), "checkout is shallow but these tests read commits: %s" % ", ".join(users))


class CiImports(unittest.TestCase):
    def test_no_test_imports_another_test_through_a_tests_package(self):
        """CI runs `python3 -I -m unittest discover -s tests`: the tests folder is the top level, there is no `tests` package (and -I keeps the repository root off the path).
        Found 2026-11-11: three tests in tests/test_vtest_v0.py did `from tests.test_vtest import FakeEnv` and ERRORED (ModuleNotFoundError) under CI's own command. tests/test_storage_inventory.py
        used the same form but put the repository root on the path itself, so it worked; it is changed too, so that one rule covers every file."""
        bad = []
        for f in sorted(glob.glob(os.path.join(ROOT, "tests", "*.py"))):
            with open(f, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if re.match(r"\s*(from|import)\s+tests([.\s]|$)", line):
                        bad.append("%s:%d: %s" % (os.path.basename(f), n, line.strip()))
        self.assertEqual(bad, [])


def _guards_import_error(node):
    """True for `try: import x / except ImportError: ...`: the module is optional there, and the tests that need it say they are skipped without it."""
    for h in node.handlers:
        names = [h.type] if not isinstance(h.type, ast.Tuple) else list(h.type.elts)
        if any(isinstance(t, ast.Name) and t.id in ("ImportError", "ModuleNotFoundError", "Exception") for t in names):
            return True
    return False


def _module_level_nodes(tree):
    """Every node that runs when the module is imported and is not optional: not the inside of a function (an import there is optional by construction), not the body of a try that catches ImportError."""
    stack = list(tree.body)
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Try) and _guards_import_error(n):
            stack.extend(n.orelse + n.finalbody)
            for h in n.handlers:
                stack.extend(h.body)
            continue
        stack.extend(ast.iter_child_nodes(n))


def _repository_modules():
    """module name -> files, for every .py under src/, tools/, alerts/ and tests/ (any depth: tools/vtest, src/dflsite ...); packages map to their __init__.
    alerts/ is first-party code (the alert package, 2026-11-12) that tests import by path; a third-party import inside it is still found, because its files are walked like any other."""
    mods = {}
    for top in ("src", "tools", "alerts", "tests"):
        for dirpath, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__"))]
            for f in files:
                if f.endswith(".py"):
                    mods.setdefault(f[:-3], []).append(os.path.join(dirpath, f))
            for d in dirs:
                mods.setdefault(d, []).append(os.path.join(dirpath, d, "__init__.py"))
    return mods


def _third_party_top_level_imports(entry_files):
    """Names imported when the entry files are imported and, transitively, when every repository module they import is: neither standard library nor repository code."""
    local = _repository_modules()
    seen, todo, found = set(), list(entry_files), {}
    while todo:
        f = todo.pop()
        if f in seen or not os.path.isfile(f):
            continue
        seen.add(f)
        with open(f, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), f)
        for sub in _module_level_nodes(tree):
            names = []
            if isinstance(sub, ast.Import):
                names = [a.name.split(".")[0] for a in sub.names]
            elif isinstance(sub, ast.ImportFrom) and sub.level == 0 and sub.module:
                names = [sub.module.split(".")[0]]
            for n in names:
                if n in sys.stdlib_module_names:
                    continue
                if n in local:
                    todo.extend(local[n])
                    continue
                found.setdefault(n, set()).add(os.path.relpath(f, ROOT))
    return found


def _packages_ci_installs():
    out = set()
    for line in ci_run_lines():
        if "pip install" not in line:
            continue
        for req in re.findall(r"-r\s+(\S+)", line):
            with open(os.path.join(ROOT, req), encoding="utf-8") as fh:
                for l in fh:
                    l = l.split("#")[0].strip()
                    if l:
                        out.add(re.split(r"[<>=!~\[; ]", l, maxsplit=1)[0].lower())
    return out


class CiDependencies(unittest.TestCase):
    def test_every_third_party_module_a_test_needs_at_import_time_is_installed_by_ci(self):
        """Found 2026-11-11 on GitHub (run 43050999661): tests/test_reveal_real.py imports tools/reveal_real.py, which imports tools/measure_combined.py, which imports psutil at module level.
        This machine had psutil; CI installed only numpy, so the module could not be imported and the whole test file errored. Every module-level third-party import reachable from a test file
        must be named in a requirements file that the CI workflow installs."""
        tests = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
        needed = _third_party_top_level_imports(tests)
        installed = _packages_ci_installs()
        self.assertIn("numpy", installed, "the CI install step was not found")
        missing = {n: sorted(v)[:3] for n, v in needed.items() if n.lower() not in installed}
        self.assertEqual(missing, {}, "CI does not install these packages, but tests import them at module level: %r" % (missing,))


if __name__ == "__main__":
    unittest.main()
