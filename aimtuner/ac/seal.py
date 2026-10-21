"""Seals: the run log, the build, and share codes.

  run log     every record carries a keyed checksum of its content (HMAC-SHA256) and a link to the
              record before it, so a score edited by hand, or a bad run deleted, shows as such
  build       the published AimTuner.zip carries a list of its files' hashes, signed with a key
              that never leaves the publisher's computer (Ed25519). At start-up the files are
              hashed again: "official", "modified", or "source" (not the published build)
  share code  a run's result and verdict as a line of text a friend pastes into Check a code

The checksum key is inside AimTuner, so someone who takes the program apart can forge a seal.
What it stops is the casual edit, and passing off a doctored result as a clean one.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import zlib

# ---------------------------------------------------------------------------- keys
_K = (b"AimTuner", b"\x9a\x1f\x52\xe3\x07\xbd\x44\x6c", b"seal-v1", b"\x5c\xe1\x88\x2d\x3a\x90\xf7\x11")


def _key(purpose: bytes) -> bytes:
    return hashlib.sha256(b"|".join(_K) + b"|" + purpose).digest()


# --------------------------------------------------------------------------- run log
def _canon(rec: dict) -> bytes:
    body = {k: v for k, v in rec.items() if k != "seal" and not k.startswith("_")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _link(prev_chain: str, run_id: str) -> str:
    return hashlib.sha256((prev_chain + "|" + str(run_id)).encode("utf-8")).hexdigest()[:24]


def seal(rec: dict, prev: dict | None) -> None:
    """Seal a record that follows `prev` (None: the first in its log)."""
    chain = _link(prev["seal"]["chain"] if prev and isinstance(prev.get("seal"), dict) else "", rec.get("run_id"))
    mac = hmac.new(_key(b"log"), _canon(rec) + chain.encode(), hashlib.sha256).hexdigest()[:32]
    rec["seal"] = {"v": 1, "chain": chain, "mac": mac}


def reseal(rec: dict) -> None:
    """The record's own content changed (its verdict came in, a profile was renamed): same place in
    the chain, new checksum."""
    s = rec.get("seal")
    if not isinstance(s, dict):
        return
    s["mac"] = hmac.new(_key(b"log"), _canon(rec) + s["chain"].encode(), hashlib.sha256).hexdigest()[:32]


SEALED_FROM = (0, 8, 0)     # records written by this version and later must carry a seal


def _ver(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except ValueError:
        return (0, 0, 0)


def verify(records: list) -> None:
    """Mark every record in memory: rec["_seal"] = "ok", "edited", "gap" (a record before it is
    missing or was moved), "unsealed" (from before seals) or "stripped" (should have one, has not)."""
    prev_chain = ""
    for rec in records:
        s = rec.get("seal")
        if not isinstance(s, dict):
            rec["_seal"] = "unsealed" if _ver(rec.get("app")) < SEALED_FROM else "stripped"
            continue
        mac = hmac.new(_key(b"log"), _canon(rec) + str(s.get("chain", "")).encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(mac, str(s.get("mac", ""))):
            rec["_seal"] = "edited"
        elif prev_chain and s.get("chain") != _link(prev_chain, rec.get("run_id")):
            rec["_seal"] = "gap"
        else:
            rec["_seal"] = "ok"
        prev_chain = str(s.get("chain", ""))


def trusted(rec: dict) -> bool:
    """May this record count? (A gap is reported but does not make the record itself untrue.)"""
    return rec.get("_seal", "ok") in ("ok", "unsealed", "gap")


# --------------------------------------------------------------------------- share codes
PREFIX = "AT1-"


def share_code(rec: dict) -> str:
    """A run's result and verdict as one line of text."""
    i = rec.get("integrity") or {}
    st = rec.get("stats") or {}
    body = {
        "a": rec.get("app"), "r": rec.get("run_id"), "t": rec.get("time"), "c": rec.get("controller"),
        "k": (rec.get("input") or {}).get("kind"), "s": rec.get("scenario"), "n": rec.get("scenario_name"),
        "f": rec.get("fingerprint"), "d": rec.get("duration_s"),
        "m": {k: v for k, v in st.items() if isinstance(v, (int, float)) and not isinstance(v, bool)},
        "v": i.get("status", "none"), "b": i.get("build"), "o": bool(i.get("override")),
        "w": (i.get("flags") or []) + (i.get("doubts") or []), "e": bool(rec.get("endless")),
        "l": rec.get("_seal", "ok"),
    }
    raw = zlib.compress(json.dumps(body, separators=(",", ":"), default=str).encode("utf-8"), 9)
    mac = hmac.new(_key(b"share"), raw, hashlib.sha256).digest()[:12]
    return PREFIX + base64.urlsafe_b64encode(raw + mac).decode("ascii").rstrip("=")


def read_code(text: str):
    """(body, None) for a genuine code, or (None, why it is not)."""
    t = "".join(text.split())
    if not t.startswith(PREFIX):
        return None, "not an AimTuner share code"
    t = t[len(PREFIX):]
    try:
        blob = base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
    except (ValueError, TypeError):
        return None, "the code is damaged (not all of it was copied?)"
    if len(blob) < 14:
        return None, "the code is too short (not all of it was copied?)"
    raw, mac = blob[:-12], blob[-12:]
    if not hmac.compare_digest(mac, hmac.new(_key(b"share"), raw, hashlib.sha256).digest()[:12]):
        return None, "the code has been changed since AimTuner made it"
    try:
        return json.loads(zlib.decompress(raw).decode("utf-8")), None
    except (ValueError, zlib.error):
        return None, "the code is damaged"


# ------------------------------------------------------------------------ Ed25519 (RFC 8032)
# The plain reference algorithm: only the build check uses it (one verification at start-up).
_p = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _p - 2, _p) % _p
_I = pow(2, (_p - 1) // 4, _p)


def _xrecover(y):
    xx = (y * y - 1) * pow(_d * y * y + 1, _p - 2, _p)
    x = pow(xx, (_p + 3) // 8, _p)
    if (x * x - xx) % _p:
        x = x * _I % _p
    if x % 2:
        x = _p - x
    return x


_By = 4 * pow(5, _p - 2, _p) % _p
_B = (_xrecover(_By), _By, 1, _xrecover(_By) * _By % _p)


def _add(P, Q):
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % _p
    b = (y1 + x1) * (y2 + x2) % _p
    c = t1 * 2 * _d * t2 % _p
    dd = z1 * 2 * z2 % _p
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % _p, g * h % _p, f * g % _p, e * h % _p)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _enc(P):
    x, y, z, _ = P
    zi = pow(z, _p - 2, _p)
    x, y = x * zi % _p, y * zi % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _dec(s):
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _p - x
    P = (x, y, 1, x * y % _p)
    if (-x * x + y * y - 1 - _d * x * x * y * y) % _p:
        raise ValueError("not a point")
    return P


def _h(m):
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def ed_public(seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return _enc(_mul(a, _B))


def ed_sign(seed: bytes, msg: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    A = _enc(_mul(a, _B))
    r = _h(h[32:] + msg) % _L
    R = _enc(_mul(r, _B))
    S = (r + _h(R + A + msg) * a) % _L
    return R + int.to_bytes(S, 32, "little")


def ed_verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    try:
        if len(sig) != 64 or len(pub) != 32:
            return False
        R, A = _dec(sig[:32]), _dec(pub)
        S = int.from_bytes(sig[32:], "little")
        if S >= _L:
            return False
        h = _h(sig[:32] + pub + msg) % _L
        return _enc(_mul(S, _B)) == _enc(_add(R, _mul(h, A)))
    except (ValueError, ZeroDivisionError):
        return False


# --------------------------------------------------------------------------- the build
# The publisher's public key. Its private half lives only on the publisher's computer
# (see build_exe.py); without it a build is "source", never "official".
PUBLIC_KEY = "383f03bc0b6c22da376e48eba0eaa3274d566537d68da8d49658fa707067dde5"
MANIFEST = "aimtuner_build.json"


def manifest_files(root: str) -> list:
    """Every file of a built AimTuner, relative to the folder holding AimTuner.exe."""
    out = []
    for base, _dirs, files in os.walk(root):
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), root).replace("\\", "/")
            if rel.startswith(("_internal/", "AimTuner.exe")) and not rel.endswith(MANIFEST) \
                    and "__pycache__" not in rel:
                out.append(rel)
    return sorted(out)


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_state(root: str | None = None) -> str:
    """"official", "modified", or "source" - for the program that is running (see the notes)."""
    if not getattr(sys, "frozen", False):
        return "source"
    root = root or os.path.dirname(os.path.abspath(sys.executable))
    path = os.path.join(root, "_internal", MANIFEST)
    pub = PUBLIC_KEY
    if pub is None:
        return "source"
    try:
        with open(path, "r", encoding="utf-8") as f:
            man = json.load(f)
        body = json.dumps(man["files"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        if not ed_verify(bytes.fromhex(pub), body, bytes.fromhex(man["sig"])):
            return "modified"
        listed = man["files"]
        present = set(manifest_files(root))
        if set(listed) - present or (present - set(listed)) & {p for p in present if p.endswith((".pyd", ".dll", ".pyz", ".exe", ".zip", ".pyc"))}:
            return "modified"
        for rel, digest in listed.items():
            if file_hash(os.path.join(root, rel)) != digest:
                return "modified"
        return "official"
    except (OSError, ValueError, KeyError, TypeError):
        return "modified"
