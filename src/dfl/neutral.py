"""A neutral, readable form of the league's state, and the digest that proves two states are the same.

Why it exists. A league file (persist.py) holds pickles, which are valid only for the code that wrote them. To carry a league across a code
change, the state is written here in a form that names every type explicitly and can be checked without running anything, read by the
new code, and compared with the old by value. This module is deliberately self-contained (standard library and numpy only, no import of
the rest of `dfl` except by class name when decoding) so that a tool can load it from a file path next to *older* code and export that
code's league.

What it preserves, exactly: every int and float (floats by `repr`, which round-trips), every exact `Fraction` (the ledger's money), strings,
bytes, tuples, lists, dicts **in insertion order**, sets, `random.Random` generator state, numpy arrays and scalars, dates, and any object
of a `dfl` class with its attributes. Shared references and cycles are kept (two names for one list stay two names for one list).
Derived caches listed in `IGNORE` are written as None, as in `deep_digest`.

What it does not do: it does not translate between versions of the code. If a class gained, lost or renamed an attribute, `RENAMES` and
`ADAPTERS` are the places to say how; both are empty today. Export alone is not a migration: see persist.upgrade, which exports under
the code that wrote the league, imports under the new code, checks the state by value and records the change.
"""
import base64
import datetime
import gzip
import hashlib
import importlib
import json
import random
import sys
import types
from collections import OrderedDict, defaultdict, deque
from fractions import Fraction

import numpy as np

DOC_FORMAT = "dfl-state"
DOC_VERSION = 1
IGNORE = ("_units",)            # derived caches: written as None, left out of digests
DIGEST_IGNORE = ("_units", "dir", "archive_buffer", "archive")   # `dir`: where this machine keeps the tables. archive*: the per-event hand-off to the archive, empty whenever a digest is taken; logging never changes league state
RENAMES = {}        # {"old.module.Class": "new.module.Class"} for classes that moved between versions
ADAPTERS = {}       # {"new.module.Class": callable(obj, attrs_dict)} to fill attributes a newer class expects


class NeutralError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------- digest
def deep_digest(obj, ignore=DIGEST_IGNORE):
    """A digest of an entire object graph that does not depend on dict or set order, memory addresses or hash randomisation, so two
    leagues that hold the same values have the same digest whichever process built them. Random generators contribute their full
    state. `ignore` names derived caches that may or may not be filled without changing any outcome."""
    memo = {}
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

    def prim(tag, body):
        b = body if isinstance(body, bytes) else str(body).encode()
        return tag + str(len(b)).encode() + b":" + b

    def comb(tag, parts):
        hh = hashlib.sha256(tag)
        for p in parts:
            hh.update(len(p).to_bytes(4, "big"))
            hh.update(p)
        return b"h" + hh.digest()

    def h(o):
        t = type(o)
        if o is None:
            return b"N"
        if t is bool or t is int:
            return prim(b"i", repr(o))
        if t is float:
            return prim(b"f", repr(o))
        if t is str:
            return prim(b"s", o.encode())
        if t is bytes:
            return prim(b"b", o)
        i = id(o)
        if i in memo:
            return memo[i]
        memo[i] = b"cycle"
        name = f"{t.__module__}.{t.__qualname__}".encode()
        if isinstance(o, random.Random):
            r = comb(b"random" + name, [h(o.getstate())])
        elif isinstance(o, np.ndarray):
            r = comb(b"nd", [str(o.dtype).encode(), repr(o.shape).encode(), o.tobytes()])
        elif isinstance(o, np.generic):
            r = prim(b"g", repr(o))
        elif isinstance(o, dict):
            r = comb(b"dict" + name, sorted(h(k) + b"=" + h(v) for k, v in o.items()))
        elif isinstance(o, (set, frozenset)):
            r = comb(b"set" + name, sorted(h(x) for x in o))
        elif isinstance(o, (list, tuple)) or t.__name__ == "deque":
            r = comb(b"seq" + name, [h(x) for x in o])
        elif isinstance(o, (int, float, str, bytes)):
            r = comb(b"sub" + name, [repr(o).encode()])
        elif isinstance(o, (type, types.FunctionType, types.MethodType, types.BuiltinFunctionType)):
            r = prim(b"c", f"{getattr(o, '__module__', '')}.{getattr(o, '__qualname__', repr(o))}")
        elif hasattr(o, "__dict__") or hasattr(t, "__slots__"):
            r = comb(b"obj" + name, sorted(h(k) + b"=" + h(v) for k, v in _attrs(o).items() if k not in ignore))
        else:
            r = prim(b"o", f"{name!r}:{o!r}")
        memo[i] = r
        return r

    return hashlib.sha256(h(obj)).hexdigest()


def _attrs(o):
    state = dict(getattr(o, "__dict__", {}))
    for klass in type(o).__mro__:
        for sl in getattr(klass, "__slots__", ()) or ():
            if hasattr(o, sl):
                state[sl] = getattr(o, sl)
    return state


# ---------------------------------------------------------------------------------------------------------------- encode
_PRIM = (type(None), bool, int, str)


def _is_shareable(o):
    return not isinstance(o, (type(None), bool, int, float, str, bytes, tuple, frozenset, Fraction, datetime.date, datetime.timedelta,
                              np.generic, type, types.FunctionType, types.MethodType, types.BuiltinFunctionType))


def encode(obj):
    """-> a JSON-serialisable structure that `decode` turns back into an equal object graph."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    count = {}
    stack = []

    def walk(o):                                  # first pass: which shareable objects are reached more than once
        if not _is_shareable(o):
            if isinstance(o, tuple):
                for x in o:
                    walk(x)
            elif isinstance(o, frozenset):
                for x in o:
                    walk(x)
            return
        i = id(o)
        count[i] = count.get(i, 0) + 1
        if count[i] > 1:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                walk(k)
                walk(v)
            if type(o) is defaultdict:
                walk(o.default_factory)
        elif isinstance(o, (list, set, deque)):
            for x in o:
                walk(x)
        elif isinstance(o, random.Random):
            walk(o.getstate())
        elif isinstance(o, np.ndarray):
            return
        elif _is_dfl(o):
            for v in _attrs(o).values():
                walk(v)

    walk(obj)
    ids, next_id, done = {}, [0], {}

    def sort_key(x):
        if isinstance(x, (int, str, float, bool, type(None))):
            return (0, type(x).__name__, repr(x))
        return (1, deep_digest(x), "")

    def enc(o, path):
        t = type(o)
        if o is None or t is bool or t is int or t is str:
            return o
        if t is float:
            if o != o or o in (float("inf"), float("-inf")):
                return {"$f": repr(o)}
            return o
        if t is bytes:
            return {"$b": base64.b64encode(o).decode("ascii")}
        if t is Fraction:
            return {"$q": [o.numerator, o.denominator]}
        if isinstance(o, np.generic):
            return {"$ng": [str(o.dtype), repr(o.item())]}
        if t is tuple:
            return {"$t": [enc(x, path) for x in o]}
        if t is frozenset:
            return {"$fs": [enc(x, path) for x in sorted(o, key=sort_key)]}
        if t is datetime.date or t is datetime.datetime or t is datetime.timedelta:
            return {"$dt": [t.__name__, o.isoformat() if t is not datetime.timedelta else [o.days, o.seconds, o.microseconds]]}
        if o in (list, dict, set, int, float, str, tuple) and isinstance(o, type):
            return {"$ty": o.__name__}
        if isinstance(o, (type, types.FunctionType, types.MethodType, types.BuiltinFunctionType)):
            raise NeutralError(f"cannot write {o!r}")
        i = id(o)
        if count.get(i, 0) > 1:
            if i in ids:
                return {"$ref": ids[i]}
            ids[i] = next_id[0]
            next_id[0] += 1
        tag = {"id": ids[i]} if i in ids else {}
        if t is list:
            body = {"$l": [enc(x, path) for x in o]}
        elif isinstance(o, random.Random):
            body = {"$r": enc(o.getstate(), path)}
        elif isinstance(o, np.ndarray):
            body = {"$nd": {"dtype": str(o.dtype), "shape": list(o.shape), "b": base64.b64encode(np.ascontiguousarray(o).tobytes()).decode("ascii")}}
        elif t is set:
            body = {"$s": [enc(x, path) for x in sorted(o, key=sort_key)]}
        elif t is deque:
            body = {"$dq": [enc(x, path) for x in o], "maxlen": o.maxlen}
        elif t in (dict, OrderedDict, defaultdict):
            body = {"$d": [[enc(k, path), enc(v, path)] for k, v in o.items()]}
            if t is not dict:
                body["cls"] = t.__name__
                if t is defaultdict:
                    body["factory"] = enc(o.default_factory, path)
        elif _is_dfl(o):
            cname = f"{t.__module__}.{t.__qualname__}"
            if "<locals>" in cname:
                raise NeutralError(f"cannot write local class {cname}")
            pairs = []
            for k, v in _attrs(o).items():
                try:
                    pairs.append([k, None if k in IGNORE else enc(v, path)])
                except NeutralError as e:
                    raise NeutralError(f"{cname}.{k}: {e}") from None
            body = {"$o": cname, "s": pairs}
        else:
            raise NeutralError(f"cannot write a {t.__module__}.{t.__qualname__}")
        body.update(tag)
        return body

    return enc(obj, "$")


def _is_dfl(o):
    return type(o).__module__.startswith("dfl.")


# ---------------------------------------------------------------------------------------------------------------- decode
def _cls(name):
    name = RENAMES.get(name, name)
    if not name.startswith("dfl."):
        raise NeutralError(f"a state document may only name dfl classes, not {name}")
    parts = name.split(".")
    for k in range(len(parts) - 1, 0, -1):
        try:
            mod = importlib.import_module(".".join(parts[:k]))
        except ImportError:
            continue
        obj = mod
        for p in parts[k:]:
            obj = getattr(obj, p)
        return obj
    raise NeutralError(f"class {name} does not exist in this version of the code")


def decode(doc):
    """Rebuild the object graph. Raises NeutralError on anything it does not recognise."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    memo = {}

    def reg(d, obj):
        if "id" in d:
            memo[d["id"]] = obj
        return obj

    def dec(d):
        if d is None or isinstance(d, (bool, int, float, str)):
            return d
        if isinstance(d, list):
            raise NeutralError("a bare list in a state document")
        if "$ref" in d:
            return memo[d["$ref"]]
        if "$f" in d:
            return float(d["$f"])
        if "$ty" in d:
            return {"list": list, "dict": dict, "set": set, "int": int, "float": float, "str": str, "tuple": tuple}[d["$ty"]]
        if "$b" in d:
            return base64.b64decode(d["$b"])
        if "$q" in d:
            return Fraction(*d["$q"])
        if "$ng" in d:
            dt, rep = d["$ng"]
            return np.dtype(dt).type(eval_literal(rep))
        if "$t" in d:
            return tuple(dec(x) for x in d["$t"])
        if "$fs" in d:
            return frozenset(dec(x) for x in d["$fs"])
        if "$dt" in d:
            kind, val = d["$dt"]
            if kind == "timedelta":
                return datetime.timedelta(days=val[0], seconds=val[1], microseconds=val[2])
            return getattr(datetime, kind).fromisoformat(val)
        if "$l" in d:
            out = reg(d, [])
            out.extend(dec(x) for x in d["$l"])
            return out
        if "$s" in d:
            out = reg(d, set())
            for x in d["$s"]:
                out.add(dec(x))
            return out
        if "$dq" in d:
            out = reg(d, deque(maxlen=d.get("maxlen")))
            out.extend(dec(x) for x in d["$dq"])
            return out
        if "$d" in d:
            cls = {"OrderedDict": OrderedDict, "defaultdict": defaultdict}.get(d.get("cls"), dict)
            out = reg(d, cls() if cls is not defaultdict else defaultdict(None))
            if cls is defaultdict:
                out.default_factory = dec(d["factory"])
            for k, v in d["$d"]:
                out[dec(k)] = dec(v)
            return out
        if "$r" in d:
            out = reg(d, random.Random())
            out.setstate(dec(d["$r"]))
            return out
        if "$nd" in d:
            n = d["$nd"]
            arr = np.frombuffer(base64.b64decode(n["b"]), dtype=np.dtype(n["dtype"])).reshape(n["shape"]).copy()
            return reg(d, arr)
        if "$o" in d:
            cls = _cls(d["$o"])
            out = reg(d, cls.__new__(cls))
            attrs = {}
            for k, v in d["s"]:
                attrs[k] = dec(v)
            fill = ADAPTERS.get(RENAMES.get(d["$o"], d["$o"]))
            if fill:
                fill(out, attrs)
            for k, v in attrs.items():
                object.__setattr__(out, k, v)
            return out
        raise NeutralError(f"unrecognised element {list(d)[:3]}")

    return dec(doc)


def eval_literal(rep):
    import ast
    return ast.literal_eval(rep)


# ---------------------------------------------------------------------------------------------------------------- documents
def dumps_doc(obj, manifest):
    """-> gzip bytes of a state document: {format, version, manifest, state}, with canonical (sorted-key, compact) JSON outside the
    order-preserving pair lists inside `state`."""
    doc = {"format": DOC_FORMAT, "version": DOC_VERSION, "manifest": manifest, "state": encode(obj)}
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return gzip.compress(text.encode("utf-8"), 6, mtime=0)


def loads_doc(raw):
    try:
        doc = json.loads(gzip.decompress(raw).decode("utf-8"))
    except Exception as e:
        raise NeutralError(f"not a state document: {e}")
    if doc.get("format") != DOC_FORMAT:
        raise NeutralError("not a dfl state document")
    if doc.get("version") != DOC_VERSION:
        raise NeutralError(f"state document version {doc.get('version')} is not supported (this code reads version {DOC_VERSION})")
    return doc


def state_digest(raw):
    """SHA-256 of the *uncompressed* document text: stable across gzip implementations."""
    return hashlib.sha256(gzip.decompress(raw)).hexdigest()


def runner_manifest(r):
    """What a state document says about the league it came from. `r` is any object with `.u` (the Universe), `.meta`, `.seq`, `.chain`,
    which is what persist.Runner has in every version of the league file."""
    try:
        rule = importlib.import_module("dfl.engine.params").RULE_VERSION
    except Exception:
        rule = None
    m = r.meta
    return {"league_format": m.get("format"), "fingerprint": m.get("fingerprint"), "seq": r.seq, "chain": r.chain, "seed": m.get("seed"),
            "snapshot_every": m.get("snapshot_every"), "keep": m.get("keep"), "universe_args": m.get("universe_args"),
            "tables": m.get("tables"), "lineage": m.get("lineage", []), "created_with": m.get("created_with"), "rule_version": rule,
            "archive": m.get("archive"), "deep_digest": deep_digest(r.u)}
