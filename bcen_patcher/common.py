"""Settings, paths and emulator helpers for the Battle Cats English patcher."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]           # the patcher's folder
CONFIG_FILE = ROOT / "config.json"

JP_PKG = "jp.co.ponos.battlecats"     # Japanese game (source; the user keeps it updated)
EN_PKG = "jp.co.ponos.battlecatsen"   # English game (source of the official English files)
MOD_PKG = "jp.co.ponos.battlecatz"    # the patched copy (same length as JP_PKG)

DEFAULTS = {
    "work_dir": "",          # "" = %USERPROFILE%\.bcen-patcher\work
    "adb": "",               # "" = auto-detect (LDPlayer's adb, then PATH)
    "adb_serial": "",        # "" = the only connected emulator/device
    "java_bin": "",          # "" = auto-detect a JDK (needs keytool + jarsigner)
    "app_name": "Battle Cats",
    "check_minutes": 30,
    "signature_v2": "auto",  # "auto" = only when the device runs Android 11+
}


@lru_cache(maxsize=1)
def config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")))
    return cfg


def home_dir() -> Path:
    return Path.home() / ".bcen-patcher"


def work_dir() -> Path:
    w = Path(os.path.expandvars(config()["work_dir"])) if config()["work_dir"] else home_dir() / "work"
    w.mkdir(parents=True, exist_ok=True)
    return w


W = work_dir()
STATE = W / "state.json"


# -- tool discovery ------------------------------------------------------------------

def _ldplayer_dirs() -> list[Path]:
    dirs = []
    try:  # running emulator
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-Process dnplayer -ErrorAction SilentlyContinue).Path"],
                             capture_output=True, text=True, timeout=15).stdout
        dirs += [Path(l.strip()).parent for l in out.splitlines() if l.strip()]
    except Exception:  # noqa: BLE001
        pass
    for drive in "CDEFG":
        dirs += [Path(p) for p in glob.glob(f"{drive}:\\LDPlayer\\LDPlayer*")]
        dirs += [Path(p) for p in glob.glob(f"{drive}:\\Program Files\\LDPlayer*\\LDPlayer*")]
    return dirs


@lru_cache(maxsize=1)
def adb_path() -> Path:
    if config()["adb"]:
        return Path(os.path.expandvars(config()["adb"]))
    for d in _ldplayer_dirs():
        if (d / "adb.exe").exists():
            return d / "adb.exe"
    found = shutil.which("adb")
    if found:
        return Path(found)
    raise RuntimeError("adb not found. Install LDPlayer 9, or set \"adb\" in config.json "
                       "to your emulator's adb.exe")


@lru_cache(maxsize=1)
def java_bin() -> Path:
    def ok(d: Path) -> bool:
        return all((d / f"{t}.exe").exists() for t in ("java", "keytool", "jarsigner"))
    cands = []
    if config()["java_bin"]:
        cands.append(Path(os.path.expandvars(config()["java_bin"])))
    if os.environ.get("JAVA_HOME"):
        cands.append(Path(os.environ["JAVA_HOME"]) / "bin")
    k = shutil.which("keytool")
    if k:
        cands.append(Path(k).parent)
    for pattern in (r"C:\Program Files\Java\*\bin", r"C:\Program Files\Eclipse Adoptium\*\bin",
                    r"C:\Program Files\Microsoft\jdk-*\bin", r"C:\Program Files\Zulu\*\bin"):
        cands += sorted((Path(p) for p in glob.glob(pattern)), reverse=True)
    for c in cands:
        if ok(c):
            return c
    raise RuntimeError("A Java JDK (with keytool and jarsigner) was not found. Run setup.bat, "
                       "or set \"java_bin\" in config.json to the JDK's bin folder")


# -- emulator ------------------------------------------------------------------------

def adb(*args: str, timeout: int = 120, check: bool = True) -> str:
    serial = config()["adb_serial"]
    cmd = [str(adb_path()), *(["-s", serial] if serial else []), *args]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout.decode("utf-8", "replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {out}{r.stderr.decode('utf-8', 'replace')}")
    return out


def su(cmd: str, timeout: int = 300) -> str:
    return adb("shell", f"su -c '{cmd}'", timeout=timeout)


def emulator_ready() -> tuple[bool, str]:
    try:
        devices = [l.split("\t")[0] for l in adb("devices", timeout=20).splitlines()[1:]
                   if l.endswith("\tdevice")]
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    if not devices:
        return False, "no emulator found: start LDPlayer and enable ADB debugging (local connection)"
    if len(devices) > 1 and not config()["adb_serial"]:
        return False, f"several devices connected ({', '.join(devices)}): set \"adb_serial\" in config.json"
    return True, devices[0]


@lru_cache(maxsize=1)
def has_root() -> bool:
    try:
        return "uid=0" in su("id", timeout=20)
    except Exception:  # noqa: BLE001
        return False


def android_sdk() -> int:
    try:
        return int(adb("shell", "getprop ro.build.version.sdk", timeout=20).strip())
    except Exception:  # noqa: BLE001
        return 0


@dataclass
class AppInfo:
    package: str
    version: str | None
    version_code: int | None


def app_info(package: str) -> AppInfo:
    out = adb("shell", f"dumpsys package {package}", check=False)
    vn = re.search(r"versionName=(\S+)", out)
    vc = re.search(r"versionCode=(\d+)", out)
    return AppInfo(package, vn.group(1) if vn else None, int(vc.group(1)) if vc else None)


def server_fingerprint(package: str) -> str | None:
    """Hash of the downloaded server packs (changes when events add data). Needs root."""
    if not has_root():
        return None
    try:
        out = su(f"ls -l /data/data/{package}/files/ 2>/dev/null | grep -E \"Server.*\\.(pack|list)$\"")
    except RuntimeError:
        return None
    lines = sorted(" ".join(l.split()[4:5] + l.split()[-1:]) for l in out.splitlines() if l.strip())
    return hashlib.sha1("\n".join(lines).encode()).hexdigest() if lines else None


# -- state / files ---------------------------------------------------------------------

def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def find_apk_with(apk_dir: Path, pattern: str) -> Path | None:
    """The APK in apk_dir containing an entry matching `pattern`. Works for split
    installs (split_InstallPack.apk / split_config.<abi>.apk) and single-file
    installs (everything in base.apk), e.g. from QooApp."""
    rx = re.compile(pattern)
    for apk in sorted(apk_dir.glob("*.apk"), key=lambda p: p.name != "split_InstallPack.apk"):
        with zipfile.ZipFile(apk) as z:
            if any(rx.match(n) for n in z.namelist()):
                return apk
    return None


def pack_apk(package: str) -> Path:
    apk = find_apk_with(W / package / "apk", r"assets/.*\.pack$")
    if apk is None:
        raise RuntimeError(f"no game data packs found in the copied {package} app")
    return apk


def gv(version: str):
    from tbcml import core
    return core.GameVersion.from_string(version)
