#!/usr/bin/env python3
"""Move ONE verified evidence archive from a disposable machine to a private, evidence-only bucket, so the owner can download it with the provider's own web page.

  export_evidence.py upload --archive F --bucket dfl-vtest-evidence-<tag> --region nyc3

Why this exists: copying archives through the machine's web console by hand consumed whole sessions and could not be done twice. This replaces the transcription, not the safeguards.

What it does: reads the archive (it must be a .tar.gz inside the harness's export folder, at most 512 MB), computes its SHA-256, uploads it with ONE signed PUT to evidence/<name> in the named bucket, uploads a
<name>.sha256 file beside it, reads the object back with HEAD (size and the stored hash must match), and checks with an UNSIGNED request that the object is NOT publicly readable (an answer of 200 is a failure).
It prints the SHA-256: the receiver compares the downloaded archive's hash with it.

What it never does: write, print or keep a credential (the access key id and secret are asked for with getpass, held in memory for this one run, and never read from the environment or a file); upload to any bucket
whose name does not start with dfl-vtest-evidence- (production and its backups can never be a target); set an ACL; list, delete or overwrite anything it did not just create; use anything but the standard library.

The key must be one the OWNER created for that bucket alone (read/write on dfl-vtest-evidence-*, nothing else) and revokes afterwards; the owner types it at the prompt in the machine's console. An assumption not yet checked:
that the provider offers keys limited to one bucket. If it does not, do not use this tool with an account-wide key.
"""
import argparse
import getpass
import hashlib
import hmac
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                                                    # noqa: E402

MAX_BYTES = 512 * 1024 * 1024
BUCKET_RE = re.compile(r"^dfl-vtest-evidence-[a-z0-9][a-z0-9-]*$")
REGION_RE = re.compile(r"^[a-z]{2,6}[0-9]{1,2}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz$")


class Refused(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------------------------------------- guards
def check_bucket(name):
    if not BUCKET_RE.match(name or "") or len(name) > 63:
        raise Refused("refusing bucket %r: only an evidence bucket named dfl-vtest-evidence-<letters, digits, hyphens> is accepted (production and its backups can never be a target)" % name)


def check_region(region):
    if not REGION_RE.match(region or ""):
        raise Refused("refusing region %r: expected a region name such as nyc3" % region)


def check_archive(path, export_dir=None):
    export_dir = os.path.realpath(export_dir or C.EXPORT)
    real = os.path.realpath(path)
    if os.path.dirname(real) != export_dir:
        raise Refused("refusing %s: only archives made by `vtest.py archive` in %s are uploaded" % (path, export_dir))
    if not NAME_RE.match(os.path.basename(real)):
        raise Refused("refusing %s: the file must be a .tar.gz archive with a plain name" % path)
    if not os.path.isfile(real):
        raise Refused("%s does not exist" % path)
    if os.path.getsize(real) > MAX_BYTES:
        raise Refused("refusing %s: larger than %d MB" % (path, MAX_BYTES >> 20))
    return real


def object_key(name):
    if "/" in name or ".." in name or not name:
        raise Refused("refusing object name %r" % name)
    return "evidence/" + name


# ---------------------------------------------------------------------------------------------------------------------------------------- SigV4
def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(method, host, path, query, headers, payload_hash, access, secret, region, service, amz_date, include_content_sha=True):
    """AWS Signature Version 4 (verified against AWS's published get-vanilla vector). -> the headers to send, including Authorization."""
    hdrs = {"host": host, "x-amz-date": amz_date}
    if include_content_sha:
        hdrs["x-amz-content-sha256"] = payload_hash
    hdrs.update({k.lower(): str(v).strip() for k, v in headers.items()})
    names = sorted(hdrs)
    canon_headers = "".join("%s:%s\n" % (n, hdrs[n]) for n in names)
    signed = ";".join(names)
    canonical = "\n".join([method, path or "/", query, canon_headers, signed, payload_hash])
    date = amz_date[:8]
    scope = "%s/%s/%s/aws4_request" % (date, region, service)
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret).encode(), date)
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out = dict(hdrs)
    out["Authorization"] = "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (access, scope, signed, signature)
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------ transfer
def prompt_credentials():
    """Asked for at the console, not echoed, never stored. The owner types them; they are not read from the environment or from any file."""
    access = getpass.getpass("access key id for the evidence bucket (typing is hidden): ").strip()
    secret = getpass.getpass("secret key (typing is hidden): ").strip()
    return access, secret


def _url(bucket, region, key, endpoint):
    if endpoint:                                   # tests: a local server, path-style
        host = endpoint.split("://", 1)[1]
        return endpoint, host, "/%s/%s" % (bucket, key)
    host = "%s.%s.digitaloceanspaces.com" % (bucket, region)
    return "https://" + host, host, "/" + key


def _request(method, bucket, region, key, body, extra, access, secret, endpoint, timeout=60):
    base, host, path = _url(bucket, region, key, endpoint)
    payload = hashlib.sha256(body or b"").hexdigest()
    hdrs = sign(method, host, path, "", extra, payload, access, secret, region, "s3", time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    hdrs.pop("host")
    req = urllib.request.Request(base + path, data=body if method == "PUT" else None, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return None, {"error": str(e.reason if hasattr(e, "reason") else e)}


def _anonymous_get(bucket, region, key, endpoint, timeout=30):
    base, host, path = _url(bucket, region, key, endpoint)
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, method="GET"), timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return None


def upload(archive, bucket, region, export_dir=None, endpoint=None, creds=None, echo=print):
    """-> exit code. 0 only when the object is there, matches, and is not publicly readable."""
    return upload_many([archive], bucket, region, export_dir, endpoint, creds, echo)


def upload_many(archives, bucket, region, export_dir=None, endpoint=None, creds=None, echo=print):
    """Several archives, one credential prompt. Every file and the bucket are checked BEFORE the credentials are asked for and before anything is sent. -> 0 only if every upload verified."""
    try:
        check_bucket(bucket)
        check_region(region)
        paths = [check_archive(a, export_dir) for a in archives]
    except Refused as e:
        echo("REFUSED: %s" % e)
        return 2
    access, secret = (creds or prompt_credentials)()
    if not access or not secret:
        echo("REFUSED: no credentials were given; nothing was sent. The owner creates a key limited to the evidence bucket and types it at the prompt (it is never read from a file or the environment).")
        return 2
    worst = 0
    for path in paths:
        worst = max(worst, _upload_one(path, bucket, region, endpoint, access, secret, echo))
    return worst


def _upload_one(path, bucket, region, endpoint, access, secret, echo):
    with open(path, "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()
    name = os.path.basename(path)
    key = object_key(name)
    echo("SHA256  %s  %s  (%d bytes)" % (sha, name, len(data)))
    st, hdr = _request("PUT", bucket, region, key, data, {"x-amz-meta-sha256": sha, "content-type": "application/gzip"}, access, secret, endpoint)
    if st != 200:
        echo("FAILED: the upload was not accepted (HTTP %s %s). Nothing else was done." % (st, hdr.get("error", "")))
        return 1
    note = ("%s  %s\n" % (sha, name)).encode()
    st2, _ = _request("PUT", bucket, region, key + ".sha256", note, {"content-type": "text/plain"}, access, secret, endpoint)
    st3, h3 = _request("HEAD", bucket, region, key, None, {}, access, secret, endpoint)
    ok = st3 == 200 and h3.get("content-length") == str(len(data)) and h3.get("x-amz-meta-sha256") == sha and st2 == 200
    if not ok:
        echo("FAILED: the object was uploaded but reading it back did not match (HTTP %s, size %s, stored hash %s, hash file HTTP %s)." % (st3, h3.get("content-length"), h3.get("x-amz-meta-sha256"), st2))
        return 1
    anon = _anonymous_get(bucket, region, key, endpoint)
    if anon == 200:
        echo("FAILED: the object is publicly readable (an unsigned request got HTTP 200). Make the bucket private in the provider's control panel, delete the object there, and do not use this bucket.")
        return 1
    echo("uploaded %s/%s: size and hash read back match; an unsigned request got HTTP %s (not readable by the public: %s)" % (bucket, key, anon, "yes" if anon in (401, 403) else "NOT CONFIRMED"))
    echo("Download %s (and %s.sha256) with the provider's web page, attach them in chat, and compare the downloaded file's SHA-256 with the line above." % (name, name))
    return 0 if anon in (401, 403) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="export_evidence", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("upload")
    u.add_argument("--archive", required=True, action="append", help="repeat for several archives (one credential prompt)")
    u.add_argument("--bucket", required=True)
    u.add_argument("--region", required=True)
    a = ap.parse_args(argv)
    return upload_many(a.archive, a.bucket, a.region)


if __name__ == "__main__":
    sys.exit(main())
