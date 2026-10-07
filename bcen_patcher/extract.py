"""Copy an app out of LDPlayer and decrypt its data packs.

  pull_apks(package)                      -> mod/<package>/apk/*.apk
  extract_local(package, cc, version)     -> mod/<package>/packs/<Pack>/<file>
  pull_server(package)                    -> mod/<package>/server/*.pack|.list   (needs root)
  extract_server(package, cc, version)    -> mod/<package>/server_packs/<Pack>/<file>
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .common import W, adb, gv, pack_apk, su

TEXT_PACKS = ("resLocal",)
IMAGE_PACKS = ("ImageLocal", "ImageDataLocal", "MapLocal")


def pull_apks(package: str, keep_split=lambda name: True) -> list[Path]:
    dest = W / package / "apk"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    paths = [l.replace("package:", "").strip() for l in adb("shell", f"pm path {package}").splitlines() if l.strip()]
    if not paths:
        raise RuntimeError(f"{package} is not installed in LDPlayer")
    out = []
    for p in paths:
        name = p.rsplit("/", 1)[-1]
        if not keep_split(name):
            continue
        adb("pull", p, str(dest / name), timeout=600)
        out.append(dest / name)
    return out


def _decrypt(list_bytes: bytes, pack_bytes: bytes, cc: str, name: str, version: str):
    from tbcml import core
    return core.PackFile.from_pack_file(core.Data(list_bytes), core.Data(pack_bytes),
                                        core.CountryCode.from_code(cc), name, gv(version))


def _write(pack, folder: Path) -> int:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for f in pack.get_files():
        (folder / f.file_name).write_bytes(f.dec_data.to_bytes())
    return len(pack.files)


def extract_local(package: str, cc: str, version: str, packs=TEXT_PACKS + IMAGE_PACKS) -> dict[str, int]:
    """Decrypt the packs inside the app (split_InstallPack.apk, or base.apk for
    single-file installs)."""
    counts = {}
    with zipfile.ZipFile(pack_apk(package)) as z:
        names = set(z.namelist())
        for name in sorted(names):
            if not (name.startswith("assets/") and name.endswith(".pack")):
                continue
            pack_name = name[len("assets/"):-len(".pack")]
            # Base packs only (skip the English game's de/fr/es/it/th variants).
            if pack_name not in packs or name[:-5] + ".list" not in names:
                continue
            pack = _decrypt(z.read(name[:-5] + ".list"), z.read(name), cc, pack_name, version)
            if pack is None:
                raise RuntimeError(f"could not decrypt {pack_name} of {package}")
            counts[pack_name] = _write(pack, W / package / "packs" / pack_name)
    return counts


def pull_server(package: str) -> int:
    """Copy the image/map server packs the game downloaded (root)."""
    dest = W / package / "server"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    tmp = f"/sdcard/nyanko_dl_{package}"
    su(f"rm -rf {tmp}; mkdir -p {tmp}; cd /data/data/{package}/files && "
       f"for f in *ImageServer*.pack *ImageServer*.list *ImageDataServer*.pack *ImageDataServer*.list "
       f"*MapServer*.pack *MapServer*.list; do [ -f \"$f\" ] && cp \"$f\" {tmp}/; done; true")
    adb("pull", f"{tmp}/.", str(dest), timeout=1800)
    adb("shell", f"rm -rf {tmp}")
    return len(list(dest.iterdir()))


def extract_server(package: str, cc: str, version: str) -> int:
    out = W / package / "server_packs"
    if out.exists():
        shutil.rmtree(out)
    files = 0
    for pack_path in sorted((W / package / "server").glob("*.pack")):
        list_path = pack_path.with_suffix(".list")
        if not list_path.exists():
            continue
        pack = _decrypt(list_path.read_bytes(), pack_path.read_bytes(), cc, pack_path.stem, version)
        if pack is not None:
            files += _write(pack, out / pack_path.stem)
    return files
