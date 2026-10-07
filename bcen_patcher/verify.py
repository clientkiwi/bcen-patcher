"""Quality gate: do the merged files still line up?

anchors.json holds ~275 known-correct Japanese→English pairs (unit, enemy and
stage names) spread across the text files. For each one, find the row where the
Japanese game has that Japanese text and check the merged file put the expected
English there. If PONOS reorders rows so the two games no longer line up,
agreement drops and the patcher refuses to install the build.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .common import JP_PKG, W

ANCHORS = Path(__file__).with_name("anchors.json")
JP = W / JP_PKG / "packs" / "resLocal"
MERGED = W / "merged" / "resLocal"


def key(t: str) -> str:
    return re.sub(r"[\s!！?？~～〜・、。,.★☆「」『』:：()（）‚]", "",
                  unicodedata.normalize("NFKC", t)).lower()


def check(min_agree: float = 0.9) -> dict:
    anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))
    cache: dict[str, tuple[list, list, str]] = {}
    found = agree = 0
    misses = []
    for a in anchors:
        name = a["file"]
        if name not in cache:
            if not (JP / name).exists() or not (MERGED / name).exists():
                cache[name] = ([], [], ",")
            else:
                d = "\t" if name.endswith(".tsv") else ","
                cache[name] = ((JP / name).read_bytes().decode("utf-8").split("\n"),
                               (MERGED / name).read_bytes().decode("utf-8").split("\n"), d)
        jl, ml, d = cache[name]
        for jrow, mrow in zip(jl, ml):
            jf, mf = jrow.rstrip("\r").split(d), mrow.rstrip("\r").split(d)
            cols = [c for c, f in enumerate(jf) if f.strip() == a["ja"] and c < len(mf)]
            if cols:
                found += 1
                if any(key(mf[c]) == key(a["en"]) for c in cols):
                    agree += 1
                elif len(misses) < 5:
                    misses.append(f"{a['ja']} → expected {a['en']!r}, got {mf[cols[0]].strip()!r}")
                break
    rate = agree / found if found else 0.0
    # Names can legitimately disappear or be renamed between versions, so only
    # judge when enough anchors are still present.
    passed = found >= 50 and rate >= min_agree
    report = {"anchors_found": found, "agree": agree, "rate": round(rate, 3), "passed": passed,
              "examples_of_mismatch": misses}
    print(f"quality check: {report}")
    return report
