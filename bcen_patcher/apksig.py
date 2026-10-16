"""Check that an app file is genuinely signed by its developer.

Verifies the APK Signature Scheme v2/v3 block (digests of the file contents and
the signature over them) and returns the signer certificate's SHA-256
fingerprint. Comparing that fingerprint with a known-good copy proves a file is
the developer's own, untouched, wherever it was downloaded.

  python -m bcen_patcher.apksig <file.apk|file.xapk|folder> [--expect <known-good.apk>]
"""
from __future__ import annotations

import hashlib
import io
import struct
import sys
import zipfile
from pathlib import Path

V2_ID, V3_ID = 0x7109871A, 0xF05368C0
# algorithm id -> (content digest, signature scheme)
ALGS = {0x0101: ("sha256", "rsa-pss"), 0x0102: ("sha512", "rsa-pss"),
        0x0103: ("sha256", "rsa-pkcs1"), 0x0104: ("sha512", "rsa-pkcs1"),
        0x0201: ("sha256", "ecdsa"), 0x0202: ("sha512", "ecdsa"), 0x0301: ("sha256", "dsa")}


class SignatureError(Exception):
    pass


def _rd(b: bytes, o: int) -> tuple[bytes, int]:
    n = struct.unpack_from("<I", b, o)[0]
    if o + 4 + n > len(b):
        raise SignatureError("malformed signature block")
    return b[o + 4:o + 4 + n], o + 4 + n


def _seq(b: bytes) -> list[bytes]:
    out, o = [], 0
    while o < len(b):
        item, o = _rd(b, o)
        out.append(item)
    return out


def _chunked(sections: list[bytes], alg: str) -> bytes:
    h = getattr(hashlib, alg)
    digests = []
    for s in sections:
        for i in range(0, len(s), 1 << 20):
            c = s[i:i + (1 << 20)]
            digests.append(h(b"\xa5" + struct.pack("<I", len(c)) + c).digest())
    return h(b"\x5a" + struct.pack("<I", len(digests)) + b"".join(digests)).digest()


def _verify_sig(pub_der: bytes, alg_id: int, sig: bytes, data: bytes) -> None:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import dsa, ec, padding
    digest, scheme = ALGS[alg_id]
    hash_alg = hashes.SHA256() if digest == "sha256" else hashes.SHA512()
    key = serialization.load_der_public_key(pub_der)
    if scheme == "rsa-pkcs1":
        key.verify(sig, data, padding.PKCS1v15(), hash_alg)
    elif scheme == "rsa-pss":
        key.verify(sig, data, padding.PSS(padding.MGF1(hash_alg), hash_alg.digest_size), hash_alg)
    elif scheme == "ecdsa":
        key.verify(sig, data, ec.ECDSA(hash_alg))
    else:
        key.verify(sig, data, hash_alg)


def verify(data: bytes) -> str:
    """Verify the v3 (else v2) signature of an APK; return the signer cert SHA-256."""
    eocd = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if eocd < 0:
        raise SignatureError("not an app file")
    cd_size, cd_off = struct.unpack_from("<II", data, eocd + 12)
    if data[cd_off - 16:cd_off] != b"APK Sig Block 42":
        raise SignatureError("no v2/v3 signature block (unsigned or old-style signature only)")
    size = struct.unpack_from("<Q", data, cd_off - 24)[0]
    start = cd_off - size - 8
    blocks, p = {}, start + 8
    while p < cd_off - 24:
        n, bid = struct.unpack_from("<QI", data, p)
        blocks[bid] = data[p + 12:p + 8 + n]
        p += 8 + n
    scheme = V3_ID if V3_ID in blocks else V2_ID
    if scheme not in blocks:
        raise SignatureError("no v2/v3 signature found")
    eocd_rec = bytearray(data[eocd:])
    struct.pack_into("<I", eocd_rec, 16, start)  # digest is computed as if the block weren't there
    sections = [data[:start], data[cd_off:cd_off + cd_size], bytes(eocd_rec)]
    fingerprints = set()
    for signer in _seq(_rd(blocks[scheme], 0)[0]):
        signed_data, o = _rd(signer, 0)
        if scheme == V3_ID:
            o += 8  # min/max SDK
        sigs, o = _rd(signer, o)
        pub, _ = _rd(signer, o)
        best = None
        for s in _seq(sigs):
            alg = struct.unpack_from("<I", s, 0)[0]
            if alg in ALGS:
                best = (alg, _rd(s, 4)[0])
        if best is None:
            raise SignatureError("unsupported signature algorithm")
        _verify_sig(pub, best[0], best[1], signed_data)  # raises if forged
        digests_b, o2 = _rd(signed_data, 0)
        certs_b, _ = _rd(signed_data, o2)
        digests = {struct.unpack_from("<I", d, 0)[0]: _rd(d, 4)[0] for d in _seq(digests_b)}
        if best[0] not in digests:
            raise SignatureError("signature/digest mismatch")
        if _chunked(sections, ALGS[best[0]][0]) != digests[best[0]]:
            raise SignatureError("file contents were changed after signing")
        cert = _seq(certs_b)[0]
        fingerprints.add(hashlib.sha256(cert).hexdigest())
    if len(fingerprints) != 1:
        raise SignatureError("multiple signers")
    return fingerprints.pop()


def apks_in(path: Path) -> dict[str, bytes]:
    """name -> bytes for an .apk, a folder of .apk files, or an .xapk/.apkm/.apks bundle."""
    if path.is_dir():
        return {p.name: p.read_bytes() for p in sorted(path.glob("*.apk"))}
    if path.suffix.lower() == ".apk":
        return {path.name: path.read_bytes()}
    with zipfile.ZipFile(path) as z:
        return {Path(n).name: z.read(n) for n in z.namelist() if n.lower().endswith(".apk")}


def check(path: Path, expect: Path | None = None) -> dict:
    files = apks_in(path)
    if not files:
        raise SignatureError("no app files found")
    want = verify(next(iter(apks_in(expect).values()))) if expect else None
    report = {}
    for name, data in files.items():
        try:
            fp = verify(data)
            report[name] = "ok" if want is None or fp == want else "WRONG SIGNER"
            report.setdefault("_fingerprint", fp)
        except Exception as e:  # noqa: BLE001
            report[name] = f"FAILED: {e}"
    report["_all_ok"] = all(v == "ok" for k, v in report.items() if not k.startswith("_"))
    return report


if __name__ == "__main__":
    args = sys.argv[1:]
    exp = Path(args[args.index("--expect") + 1]) if "--expect" in args else None
    target = Path(args[0])
    for k, v in check(target, exp).items():
        print(f"{k}: {v}")
