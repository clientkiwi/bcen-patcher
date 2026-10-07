"""Build the English-text copy of にゃんこ大戦争 as a separate app.

Steps (run by pipeline.py after the packs are merged):
  1. Re-encrypt every local pack that has merged files (text + images).
  2. Update those packs' checksums (MD5) in the native library.
  3. Rename the app package to a same-length name inside every split's binary
     manifest (and the matching authorities/permission, the package_name
     string and the launcher name), so it installs next to the original.
  4. Drop the old signatures, sign every split with the user's own key:
     v1 (jarsigner), zip-align (4 bytes; native libs 4096), and for Android
     11+ also an APK Signature Scheme v2 block.
Any failed check raises, so a broken build is never installed.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from tbcml import core

from .common import JP_PKG, MOD_PKG, W, config, find_apk_with, java_bin

SRC = W / JP_PKG / "apk"
MERGED = W / "merged"
KEYSTORE_DIR = W
OLD_PKG, NEW_PKG = JP_PKG, MOD_PKG  # same length: binary strings patched in place
OLD_LABEL = "にゃんこ大戦争"            # launcher name; replaced by config "app_name"
CC = core.CountryCode.from_code("jp")
assert len(OLD_PKG) == len(NEW_PKG)
NEW_LABEL = config()["app_name"]
if len(NEW_LABEL.encode("utf-8")) > len(OLD_LABEL.encode("utf-8")) or len(NEW_LABEL) >= 128:
    raise SystemExit(f"app_name {NEW_LABEL!r} is too long (max {len(OLD_LABEL.encode('utf-8'))} bytes)")


class BuildError(RuntimeError):
    pass


# -- Android binary string pools (AndroidManifest.xml / resources.arsc) --------

def patch_string_pool(buf: bytearray, pool_off: int, fn) -> int:
    """Replace strings in a ResStringPool in place when fn(s) returns a new
    string that fits in the old one's space (same or shorter; a shorter one is
    NUL-padded, so no other string moves). Returns number of strings changed."""
    _type, hsize, size, count, _styles, flags, s_start = struct.unpack_from("<HHIIIII", buf, pool_off)
    assert _type == 0x0001, "not a string pool"
    utf8 = bool(flags & 0x100)
    offsets = struct.unpack_from(f"<{count}I", buf, pool_off + hsize)
    base = pool_off + s_start
    changed = 0
    for off in offsets:
        start = p = base + off
        if utf8:
            n = buf[p]; p += 1
            if n & 0x80:
                n = ((n & 0x7F) << 8) | buf[p]; p += 1
            blen = buf[p]; p += 1
            if blen & 0x80:
                blen = ((blen & 0x7F) << 8) | buf[p]; p += 1
            s = bytes(buf[p:p + blen]).decode("utf-8")
            new = fn(s)
            if new is None or new == s:
                continue
            nb = new.encode("utf-8")
            # Keep the length prefix the same size (1 byte each below 128).
            if len(nb) <= blen and (len(new) < 128) == (n < 128) and (len(nb) < 128) == (blen < 128):
                q = start
                for v in (len(new), len(nb)):
                    if v < 128:
                        buf[q] = v; q += 1
                    else:
                        buf[q] = 0x80 | (v >> 8); buf[q + 1] = v & 0xFF; q += 2
                buf[p:p + blen + 1] = nb + b"\0" * (blen - len(nb) + 1)
                changed += 1
        else:
            n = struct.unpack_from("<H", buf, p)[0]; p += 2
            if n & 0x8000:
                continue  # very long strings: never ours
            s = bytes(buf[p:p + 2 * n]).decode("utf-16-le")
            new = fn(s)
            if new is not None and new != s and len(new) <= n:
                struct.pack_into("<H", buf, start, len(new))
                buf[p:p + 2 * n + 2] = new.encode("utf-16-le") + b"\0\0" * (n - len(new) + 1)
                changed += 1
    return changed


def rename_fn(rename_prefixed: set[str]):
    def fn(s: str):
        if s == OLD_PKG:
            return NEW_PKG
        if s in rename_prefixed:
            return NEW_PKG + s[len(OLD_PKG):]
        if s == OLD_LABEL:
            return NEW_LABEL
        return None
    return fn


def manifest_renames(base_apk: Path) -> set[str]:
    """Authorities and permission names that start with the package (not class names).
    Read from this version's own manifest, decoded with apktool."""
    apktool = next(Path(core.__file__).resolve().parents[1].rglob("apktool.jar"))
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([str(java_bin() / "java"), "-jar", str(apktool), "d", "-f", "-s",
                            str(base_apk), "-o", tmp + "/base"], capture_output=True, text=True)
        if r.returncode != 0:
            raise BuildError(f"apktool could not read the manifest: {r.stdout}{r.stderr}")
        text = (Path(tmp) / "base" / "AndroidManifest.xml").read_text(encoding="utf-8")
    names = set(re.findall(r'android:authorities="([^"]+)"', text))
    names |= set(re.findall(r'<(?:uses-)?permission android:name="([^"]+)"', text))
    return {n for n in names if n.startswith(OLD_PKG + ".")}


# -- steps -----------------------------------------------------------------------

def build_pack(install_pack: Path, name: str, files_dir: Path, gv) -> tuple[bytes, bytes, bytes, bytes]:
    """Rebuild local pack `name` with the files in files_dir replaced/added."""
    with zipfile.ZipFile(install_pack) as z:
        old_list, old_pack = z.read(f"assets/{name}.list"), z.read(f"assets/{name}.pack")
    pack = core.PackFile.from_pack_file(core.Data(old_list), core.Data(old_pack), CC, name, gv)
    files = list(files_dir.iterdir())
    for f in files:
        pack.set_file(f.name, core.Data(f.read_bytes()))
    _, pack_data, list_data = pack.to_pack_list_file()
    new_pack, new_list = pack_data.to_bytes(), list_data.to_bytes()
    # Round-trip check: decrypt what we built and compare with the input files.
    check = core.PackFile.from_pack_file(core.Data(new_list), core.Data(new_pack), CC, name, gv)
    if check is None:
        raise BuildError(f"{name}: rebuilt pack can't be decrypted again")
    for f in files:
        if check.files[f.name].dec_data.to_bytes() != f.read_bytes():
            raise BuildError(f"{name}: round-trip check failed for {f.name}")
    print(f"{name} rebuilt with {len(files)} English files (round-trip verified)")
    return old_pack, old_list, new_pack, new_list


def md5hex(b: bytes) -> bytes:
    return hashlib.md5(b).hexdigest().encode()


def rewrite_apk(src: Path, dst: Path, replace: dict[str, bytes], rename: set[str]) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            name = info.filename
            if name.startswith("META-INF/") and re.search(r"\.(SF|RSA|DSA|EC|MF)$|stamp-cert", name):
                continue  # old signature
            data = replace.get(name, zin.read(name))
            if name == "AndroidManifest.xml":
                buf = bytearray(data)
                n = patch_string_pool(buf, 8, rename_fn(rename))
                data = bytes(buf)
                need = 1 + len(rename) if src.name == "base.apk" else 1
                if n < need:
                    raise BuildError(f"{src.name}: only {n} of {need} package names could be renamed")
            elif name == "resources.arsc" and src.name == "base.apk":
                buf = bytearray(data)
                patch_string_pool(buf, 12, rename_fn(rename))
                data = bytes(buf)
            zi = zipfile.ZipInfo(name, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zout.writestr(zi, data)


def keystore() -> tuple[Path, str]:
    ks, pw_file = KEYSTORE_DIR / "nyanko.keystore", KEYSTORE_DIR / "nyanko.keystore.password"
    if not ks.exists():
        pw = secrets.token_hex(16)
        pw_file.write_text(pw)
        subprocess.run([str(java_bin() / "keytool"), "-genkeypair", "-keystore", str(ks), "-alias", "nyanko",
                        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                        "-storepass", pw, "-keypass", pw, "-dname", "CN=Battle Cats EN Patcher"],
                       check=True, capture_output=True)
    return ks, pw_file.read_text().strip()


def sign(apk: Path, ks: Path, pw: str) -> None:
    r = subprocess.run([str(java_bin() / "jarsigner"), "-keystore", str(ks), "-storepass", pw,
                        "-sigalg", "SHA256withRSA", "-digestalg", "SHA-256", str(apk), "nyanko"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise BuildError(f"jarsigner failed for {apk.name}: {r.stdout}{r.stderr}")


def zipalign(src: Path, dst: Path) -> None:
    """Align stored entries: 4 bytes, native libraries to 4096 (like zipalign -p)."""
    with zipfile.ZipFile(src) as zin, open(dst, "wb") as fout:
        zout = zipfile.ZipFile(fout, "w")
        for info in zin.infolist():
            data = zin.read(info.filename)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            if info.compress_type == zipfile.ZIP_STORED:
                align = 4096 if info.filename.endswith(".so") else 4
                header_end = fout.tell() + 30 + len(info.filename.encode("utf-8"))
                pad = (-(header_end + 4)) % align  # 4 = extra field header
                zi.extra = struct.pack("<HH", 0xD935, pad) + b"\0" * pad
            zout.writestr(zi, data)
        zout.close()


# -- APK Signature Scheme v2 (required on Android 11+ for this app) --------------------
# https://source.android.com/docs/security/features/apksigning/v2

V2_BLOCK_ID = 0x7109871A
RSA_PKCS1_SHA256 = 0x0103


def _lp(b: bytes) -> bytes:
    return struct.pack("<I", len(b)) + b


def _zip_sections(data: bytes) -> tuple[int, int, int]:
    """(central directory offset, size, EOCD offset)."""
    eocd = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if eocd < 0:
        raise BuildError("not a zip file (no end-of-central-directory record)")
    cd_size, cd_off = struct.unpack_from("<II", data, eocd + 12)
    return cd_off, cd_size, eocd


def _chunked_sha256(sections: list[bytes]) -> bytes:
    digests = []
    for s in sections:
        for i in range(0, len(s), 1 << 20):
            chunk = s[i:i + (1 << 20)]
            digests.append(hashlib.sha256(b"\xa5" + struct.pack("<I", len(chunk)) + chunk).digest())
    return hashlib.sha256(b"\x5a" + struct.pack("<I", len(digests)) + b"".join(digests)).digest()


def _load_key(ks: Path, pw: str):
    from cryptography.hazmat.primitives.serialization import pkcs12
    key, cert, _ = pkcs12.load_key_and_certificates(ks.read_bytes(), pw.encode())
    if key is None or cert is None:
        raise BuildError("signing key could not be read from the keystore")
    return key, cert


def sign_v2(apk: Path, ks: Path, pw: str) -> None:
    """Add an APK Signature Scheme v2 block (after v1 signing and zip-align)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    data = apk.read_bytes()
    cd_off, cd_size, eocd = _zip_sections(data)
    if data[cd_off - 16:cd_off] == b"APK Sig Block 42":
        raise BuildError(f"{apk.name} already has a signing block")
    contents, cd, eocd_rec = data[:cd_off], data[cd_off:cd_off + cd_size], data[eocd:]
    # The digest covers the EOCD with its CD offset pointing at the signing block,
    # which is where the central directory starts now.
    digest = _chunked_sha256([contents, cd, eocd_rec])
    key, cert = _load_key(ks, pw)
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    signed_data = (_lp(_lp(struct.pack("<I", RSA_PKCS1_SHA256) + _lp(digest)))
                   + _lp(_lp(cert_der))
                   + _lp(b""))
    signature = key.sign(signed_data, padding.PKCS1v15(), hashes.SHA256())
    public_key = key.public_key().public_bytes(serialization.Encoding.DER,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    signer = _lp(signed_data) + _lp(_lp(struct.pack("<I", RSA_PKCS1_SHA256) + _lp(signature))) + _lp(public_key)
    value = _lp(_lp(signer))
    pair = struct.pack("<QI", 4 + len(value), V2_BLOCK_ID) + value
    size = len(pair) + 8 + 16
    block = struct.pack("<Q", size) + pair + struct.pack("<Q", size) + b"APK Sig Block 42"
    new_eocd = bytearray(eocd_rec)
    struct.pack_into("<I", new_eocd, 16, cd_off + len(block))
    apk.write_bytes(contents + block + cd + bytes(new_eocd))


def verify_v2(apk: Path) -> bool:
    """Check our own v2 block the way Android does (digest + signature)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    data = apk.read_bytes()
    cd_off, cd_size, eocd = _zip_sections(data)
    if data[cd_off - 16:cd_off] != b"APK Sig Block 42":
        return False
    size = struct.unpack_from("<Q", data, cd_off - 24)[0]
    start = cd_off - size - 8
    p, end = start + 8, cd_off - 24
    value = None
    while p < end:
        n, bid = struct.unpack_from("<QI", data, p)
        if bid == V2_BLOCK_ID:
            value = data[p + 12:p + 8 + n]
        p += 8 + n
    if value is None:
        return False

    def rd(b: bytes, o: int) -> tuple[bytes, int]:
        n = struct.unpack_from("<I", b, o)[0]
        return b[o + 4:o + 4 + n], o + 4 + n

    signers, _ = rd(value, 0)
    signer, _ = rd(signers, 0)
    signed_data, o = rd(signer, 0)
    sigs, o = rd(signer, o)
    pub, _ = rd(signer, o)
    sig_entry, _ = rd(sigs, 0)
    sig, _ = rd(sig_entry, 4)
    serialization.load_der_public_key(pub).verify(sig, signed_data, padding.PKCS1v15(), hashes.SHA256())
    digests, _ = rd(signed_data, 0)
    d_entry, _ = rd(digests, 0)
    digest, _ = rd(d_entry, 4)
    eocd_rec = bytearray(data[eocd:])
    struct.pack_into("<I", eocd_rec, 16, start)  # as if the block weren't there
    return digest == _chunked_sha256([data[:start], data[cd_off:cd_off + cd_size], bytes(eocd_rec)])


def main(version: str, out: Path, progress=print, v2: bool = False) -> list[Path]:
    """Build the English app from SRC (+ MERGED) for game `version` into `out`."""
    gv = core.GameVersion.from_string(version)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # Split installs keep data in split_InstallPack.apk and native code in
    # split_config.<abi>.apk; single-file installs (e.g. some QooApp versions)
    # keep both in base.apk. Find whichever holds them.
    data_apk = find_apk_with(SRC, r"assets/.*\.pack$")
    lib_apk = find_apk_with(SRC, r"lib/[^/]+/libnative-lib\.so$")
    if data_apk is None or lib_apk is None:
        raise BuildError("couldn't find the game data or native library in the copied app")
    install = {}
    swaps: list[tuple[bytes, bytes]] = []  # (old md5 hex, new md5 hex)
    for folder in sorted(MERGED.iterdir()):
        if not folder.is_dir() or not any(folder.iterdir()):
            continue
        progress(f"rebuilding {folder.name}…")
        old_pack, old_list, new_pack, new_list = build_pack(data_apk, folder.name, folder, gv)
        install[f"assets/{folder.name}.pack"] = new_pack
        install[f"assets/{folder.name}.list"] = new_list
        swaps += [(md5hex(old_pack), md5hex(new_pack)), (md5hex(old_list), md5hex(new_list))]
    replace_by_apk: dict[str, dict[str, bytes]] = {data_apk.name: install}

    # Native library: swap the checksums of every rebuilt pack/list (all ABIs present).
    patched = False
    with zipfile.ZipFile(lib_apk) as z:
        for name in z.namelist():
            if name.endswith(".so"):
                so = z.read(name)
                hits = [so.count(old) for old, _ in swaps]
                if any(hits):
                    if hits.count(1) != len(hits):
                        raise BuildError(f"pack checksums in {name} not as expected: {hits}")
                    for old, new in swaps:
                        so = so.replace(old, new)
                    replace_by_apk.setdefault(lib_apk.name, {})[name] = so
                    patched = True
                    progress(f"checksums updated in {name}: {len(swaps)} of {len(swaps)}")
    if not patched:
        raise BuildError("pack checksums not found in the native library "
                         "(the game may have changed how it checks its files)")

    rename = manifest_renames(SRC / "base.apk")
    ks, pw = keystore()
    for src in sorted(SRC.glob("*.apk")):
        progress(f"signing {src.name}…")
        tmp = out / (src.stem + ".unaligned.apk")
        rewrite_apk(src, tmp, replace_by_apk.get(src.name, {}), rename)
        sign(tmp, ks, pw)
        zipalign(tmp, out / src.name)
        tmp.unlink()
        if v2:
            sign_v2(out / src.name, ks, pw)
            if not verify_v2(out / src.name):
                raise BuildError(f"v2 signature check failed for {src.name}")
    built = sorted(out.glob("*.apk"))
    progress(f"built {len(built)} app parts for {version}")
    return built
