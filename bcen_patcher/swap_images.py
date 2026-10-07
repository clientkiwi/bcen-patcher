"""Swap Japanese text images for the English game's versions.

For every image group the JP game has with a "_ja" name (e.g. img002_ja.png +
img002_ja.imgcut, mapsn000_00_a_ja.png), take the EN game's "_en" group and
store it under the JP name in the matching *local* pack. The game prefers a
file in a local pack over the same file in a downloaded server pack, so this
also replaces images that normally come from the server.

A group is only swapped when it is safe:
  * EN has the picture, and (if JP has a cut file) a cut file with the same
    number of pieces, so every piece the game asks for exists.
The cut/model files name their picture inside; that reference is renamed.

  python tools/mod/swap_images.py   -> %USERPROFILE%/.nyanko-overlay/mod/merged/<LocalPack>/
"""
from __future__ import annotations

import shutil
from collections import Counter, defaultdict
from pathlib import Path

from .common import EN_PKG, JP_PKG, W

JP_LOCAL = W / JP_PKG / "packs"
EN_LOCAL = W / EN_PKG / "packs"
EN_SERVER = W / EN_PKG / "server_packs"
OUT = W / "merged"
LOCAL_PACKS = ("ImageLocal", "ImageDataLocal", "MapLocal")
EXTS = (".png", ".imgcut", ".mamodel")
OTHER_LANGS = ("_de", "_es", "_fr", "_it", "_th", "_kr", "_tw", "_ko")


def to_local(pack: str) -> str:
    for server, local in (("ImageDataServer", "ImageDataLocal"), ("ImageServer", "ImageLocal"),
                          ("MapServer", "MapLocal")):
        if server in pack:
            return local
    return pack


def index(local_dir: Path, server_dir: Path, skip_langs: bool) -> dict[str, tuple[str, Path]]:
    """file name -> (local pack it belongs in, path). Local copies win."""
    files: dict[str, tuple[str, Path]] = {}
    if server_dir.exists():
        for d in sorted(server_dir.iterdir()):  # later versions sort last and win
            if skip_langs and any(d.name.endswith(l) or f"{l}_" in d.name for l in OTHER_LANGS):
                continue
            if not any(s in d.name for s in ("ImageServer", "ImageDataServer", "MapServer")):
                continue
            for p in d.iterdir():
                files[p.name] = (to_local(d.name), p)
    for name in LOCAL_PACKS:
        d = local_dir / name
        if d.exists():
            for p in d.iterdir():
                files[p.name] = (name, p)
    return files


def png_size(p: Path) -> tuple[int, int]:
    b = p.read_bytes()[:24]
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")


def cut_count(data: bytes) -> int | None:
    lines = data.decode("utf-8", "replace").splitlines()
    try:
        return int(lines[3].strip())
    except (IndexError, ValueError):
        return None


def main(jp_server: Path | None = None) -> dict:
    """jp_server: decrypted server packs of the JP game (original or our build)."""
    jp_server = jp_server or (W / JP_PKG / "server_packs")
    for pack in LOCAL_PACKS:
        if (OUT / pack).exists():
            shutil.rmtree(OUT / pack)
    jp = index(JP_LOCAL, jp_server, skip_langs=False)
    en = index(EN_LOCAL, EN_SERVER, skip_langs=True)
    groups: dict[str, list[str]] = defaultdict(list)
    for name in jp:
        stem, ext = name.rsplit(".", 1) if "." in name else (name, "")
        if "_ja" in stem and "." + ext in EXTS:
            groups[stem].append(name)

    stats, reasons = Counter(), Counter()
    written: dict[str, int] = Counter()
    for stem, names in sorted(groups.items()):
        en_stem = stem.replace("_ja", "_en")
        jp_png, en_png = stem + ".png", en_stem + ".png"
        if jp_png not in jp:
            reasons["JP group has no picture"] += 1
            continue
        if en_png not in en:
            reasons["no English picture"] += 1
            continue
        ew, eh = png_size(en[en_png][1])
        jw, jh = png_size(jp[jp_png][1])
        if ew * eh <= 16 < jw * jh:  # EN placeholder (1x1) for content it doesn't have
            reasons["English picture is a placeholder"] += 1
            continue
        if stem + ".imgcut" in jp:
            if en_stem + ".imgcut" not in en:
                reasons["no English cut file"] += 1
                continue
            jc = cut_count(jp[stem + ".imgcut"][1].read_bytes())
            ec = cut_count(en[en_stem + ".imgcut"][1].read_bytes())
            if jc is None or jc != ec:
                reasons["different number of pieces"] += 1
                continue
        for name in names:
            ext = name[len(stem):]
            src = en.get(en_stem + ext)
            if src is None:
                continue  # e.g. JP has a model file EN doesn't: keep JP's
            data = src[1].read_bytes()
            if ext != ".png":  # text files that name their picture
                data = data.replace(en_stem.encode(), stem.encode())
            pack = jp[name][0]
            (OUT / pack).mkdir(parents=True, exist_ok=True)
            (OUT / pack / name).write_bytes(data)
            written[pack] += 1
        stats["swapped"] += 1
    result = {"groups": len(groups), "swapped": stats["swapped"], "files": dict(written),
              "kept_japanese": dict(reasons)}
    print(f"image swap: {result}")
    return result


if __name__ == "__main__":
    main()
