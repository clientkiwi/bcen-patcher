"""Build and update the English copy of にゃんこ大戦争 ("Battle Cats" in the emulator).

  python -m bcen_patcher.pipeline check                what changed since the last build (JSON)
  python -m bcen_patcher.pipeline update [--no-install] [--force]
  python -m bcen_patcher.pipeline rollback             reinstall the previous build

Progress lines look like   PROGRESS|<percent>|<message>
and the last line is       RESULT|<json>          (read by the window/tray app)

Steps are skipped when their inputs haven't changed (app versions, downloaded
event data). Before installing, the build must pass: pack round-trips, checksum
patching and the anchor check. With root, the patched app's save is backed up
before every install; the last builds are kept for rollback.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import traceback

from . import build, extract, merge_text, swap_images, verify
from .common import (EN_PKG, JP_PKG, MOD_PKG, ROOT, W, adb, android_sdk, app_info, config,
                     emulator_ready, has_root, load_state, save_state, server_fingerprint, su)

BUILDS = W / "builds"
BACKUPS = W / "backups"
KEEP_BUILDS = 3


def progress(pct: int, msg: str) -> None:
    print(f"PROGRESS|{pct}|{msg}", flush=True)


def server_source(jp, mod) -> str:
    """Whose downloaded event data to use: the patched app if it's on the same
    version (that's what's being played), otherwise the original."""
    return MOD_PKG if mod.version and mod.version == jp.version else JP_PKG


def use_v2() -> bool:
    setting = str(config()["signature_v2"]).lower()
    if setting in ("true", "yes", "1"):
        return True
    if setting in ("false", "no", "0"):
        return False
    return android_sdk() >= 30  # Android 11+


# -- check ----------------------------------------------------------------------

def check() -> dict:
    ready, info = emulator_ready()
    if not ready:
        return {"ready": False, "update_needed": False, "message": info}
    jp, en, mod = app_info(JP_PKG), app_info(EN_PKG), app_info(MOD_PKG)
    if not jp.version:
        return {"ready": False, "update_needed": False,
                "message": "the Japanese game (にゃんこ大戦争) is not installed in the emulator"}
    if not en.version:
        return {"ready": False, "update_needed": False,
                "message": "the English game (The Battle Cats) is not installed in the emulator"}
    built = load_state().get("built", {})
    changes, notes = [], []
    fp_jp, fp_en = server_fingerprint(server_source(jp, mod)), server_fingerprint(EN_PKG)
    if not built:
        changes.append("no English build made yet")
    else:
        if jp.version_code != built.get("jp_code"):
            changes.append(f"Japanese game updated: {built.get('jp_version')} → {jp.version}")
        if en.version_code != built.get("en_code"):
            changes.append(f"English game updated: {built.get('en_version')} → {en.version} (new English text)")
        if fp_jp and fp_jp != built.get("server_fp_jp"):
            changes.append("new event data downloaded (images/banners)")
        if fp_en and fp_en != built.get("server_fp_en"):
            changes.append("English game downloaded new data")
    if mod.version and mod.version != jp.version:
        notes.append(f"the English copy is on {mod.version}; the Japanese game is on {jp.version}")
    if not mod.version:
        notes.append("the English copy is not installed yet")
    if not has_root():
        notes.append("no root access: event images can't be swapped and saves aren't backed up")
    return {"ready": True, "update_needed": bool(changes), "changes": changes, "notes": notes,
            "jp": jp.version, "en": en.version, "mod": mod.version}


# -- update ---------------------------------------------------------------------

def backup_save() -> str | None:
    if not app_info(MOD_PKG).version or not has_root():
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    remote = f"/sdcard/bcen_save_{stamp}.tgz"
    su(f"cd /data/data && tar -czf {remote} --exclude='*.pack' --exclude='*.list' "
       f"--exclude='*.ogg' --exclude='*.caf' --exclude='cache' --exclude='code_cache' {MOD_PKG}")
    local = BACKUPS / f"save_{stamp}.tgz"
    adb("pull", remote, str(local), timeout=600)
    adb("shell", f"rm -f {remote}")
    for old in sorted(BACKUPS.glob("save_*.tgz"))[:-10]:  # keep the last 10
        old.unlink()
    return str(local)


def install(build_dir) -> None:
    apks = [str(p) for p in sorted(build_dir.glob("*.apk"))]
    out = adb("install-multiple", "-r", *apks, timeout=900, check=False)
    if "Success" not in out:
        raise RuntimeError(f"install failed: {out.strip()[-500:]}")


def backup_keystore() -> None:
    dest = ROOT / "signing_key_backup"
    dest.mkdir(exist_ok=True)
    for name in ("nyanko.keystore", "nyanko.keystore.password"):
        if (W / name).exists():
            shutil.copy2(W / name, dest / name)


def update(do_install: bool = True, force: bool = False) -> dict:
    progress(1, "checking the emulator…")
    ready, info = emulator_ready()
    if not ready:
        raise RuntimeError(info)
    jp, en, mod = app_info(JP_PKG), app_info(EN_PKG), app_info(MOD_PKG)
    if not jp.version or not en.version:
        raise RuntimeError("both にゃんこ大戦争 (Japanese) and The Battle Cats (English) must be "
                           "installed in the emulator")
    root = has_root()
    state = load_state()
    cache = {} if force else state.get("cache", {})
    notes = [] if root else ["no root access: only images inside the app were swapped (event images "
                             "stay Japanese) and the save wasn't backed up"]

    # 1. Apps (only when their version changed).
    if cache.get("jp_code") != jp.version_code or not (W / JP_PKG / "packs" / "resLocal").exists():
        progress(5, f"copying にゃんこ大戦争 {jp.version}…")
        extract.pull_apks(JP_PKG)
        progress(12, "decrypting Japanese data…")
        extract.extract_local(JP_PKG, "jp", jp.version)
        cache["jp_code"] = jp.version_code
    if cache.get("en_code") != en.version_code or not (W / EN_PKG / "packs" / "resLocal").exists():
        progress(18, f"copying The Battle Cats {en.version}…")
        extract.pull_apks(EN_PKG, keep_split=lambda n: n in ("base.apk", "split_InstallPack.apk"))
        progress(24, "decrypting English data…")
        extract.extract_local(EN_PKG, "en", en.version)
        cache["en_code"] = en.version_code

    # 2. Downloaded event data (root only; only when it changed).
    src = server_source(jp, mod)
    fp_jp, fp_en = server_fingerprint(src), server_fingerprint(EN_PKG)
    if root:
        if fp_jp and (cache.get("server_fp_jp") != fp_jp or cache.get("server_src") != src):
            progress(30, "copying Japanese event data…")
            extract.pull_server(src)
            extract.extract_server(src, "jp", jp.version)
            cache["server_fp_jp"], cache["server_src"] = fp_jp, src
        if fp_en and cache.get("server_fp_en") != fp_en:
            progress(38, "copying English event data…")
            extract.pull_server(EN_PKG)
            extract.extract_server(EN_PKG, "en", en.version)
            cache["server_fp_en"] = fp_en
        if not fp_jp:
            notes.append("no downloaded event data yet: open the Japanese game once so it downloads its data")
    state["cache"] = cache
    save_state(state)

    # 3. Text and images.
    progress(45, "putting in the English text…")
    text = merge_text.main()
    progress(60, "swapping in the English images…")
    server_dir = W / src / "server_packs"
    images = swap_images.main(server_dir if root and server_dir.exists() else W / "_none")

    # 4. Quality gate.
    progress(68, "checking the result…")
    quality = verify.check()
    if not quality["passed"]:
        raise RuntimeError(f"quality check failed, nothing was installed: {quality}")

    # 5. Build.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    build_dir = BUILDS / f"{jp.version}_{stamp}"
    v2 = use_v2()
    progress(72, "building the English app…")
    build.main(jp.version, build_dir, progress=lambda m: progress(80, m), v2=v2)
    backup_keystore()
    result = {"jp": jp.version, "en": en.version, "build": build_dir.name, "text": text,
              "images": {k: images[k] for k in ("swapped", "groups")}, "quality": quality,
              "signature_v2": v2, "notes": notes}

    # 6. Install (save backed up first, when possible).
    if do_install:
        progress(90, "backing up the English app's save…")
        result["save_backup"] = backup_save()
        progress(94, "installing the English app…")
        install(build_dir)
        state["installed"] = build_dir.name
    state["built"] = {"jp_version": jp.version, "jp_code": jp.version_code, "en_version": en.version,
                      "en_code": en.version_code, "server_fp_jp": fp_jp, "server_fp_en": fp_en,
                      "build": build_dir.name, "time": stamp}
    builds = [build_dir.name] + [b for b in state.get("builds", []) if b != build_dir.name]
    for old in builds[KEEP_BUILDS:]:
        if old != state.get("installed"):
            shutil.rmtree(BUILDS / old, ignore_errors=True)
    state["builds"] = [b for b in builds[:KEEP_BUILDS] if (BUILDS / b).exists()]
    save_state(state)
    progress(100, "done" + ("" if do_install else " (built, not installed)"))
    return result


def rollback() -> dict:
    state = load_state()
    builds = [b for b in state.get("builds", []) if (BUILDS / b).exists()]
    older = [b for b in builds if b != state.get("installed")]
    if not older:
        raise RuntimeError("no earlier build to roll back to")
    target = older[0]
    progress(30, "backing up the English app's save…")
    backup_save()
    progress(60, f"installing {target}…")
    install(BUILDS / target)
    state["installed"] = target
    save_state(state)
    progress(100, "rolled back")
    return {"installed": target}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "check"
    try:
        if cmd == "check":
            result = check()
        elif cmd == "update":
            result = update(do_install="--no-install" not in argv, force="--force" in argv)
        elif cmd == "rollback":
            result = rollback()
        else:
            print(__doc__)
            return 2
        print("RESULT|" + json.dumps({"ok": True, **result}, ensure_ascii=False), flush=True)
        return 0
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        print("RESULT|" + json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
