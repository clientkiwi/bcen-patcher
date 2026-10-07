"""Put English text into the Japanese game's text files.

For every text file in the JP resLocal pack that has an English counterpart in
the EN game, replace Japanese text with the official English wherever the EN
file has the same row/ID. Text the EN game doesn't have yet (JP is ahead)
stays Japanese, unless custom_names.csv gives an English name for it.

Unit descriptions are handled as paragraphs: a custom English description is
wrapped back onto the 3 description lines.

The JP file layout is kept exactly (comma delimiter, padding, column count);
commas inside English text become a look-alike (U+201A) so the JP parser
doesn't split them.
"""
from __future__ import annotations

import csv
import re
import shutil
import textwrap
import unicodedata
from collections import Counter

from .common import EN_PKG, JP_PKG, ROOT, W

JP_DIR = W / JP_PKG / "packs" / "resLocal"
EN_DIR = W / EN_PKG / "packs" / "resLocal"
OUT = W / "merged" / "resLocal"
CUSTOM = ROOT / "custom_names.csv"
JPCH = re.compile(r"[぀-ヿ㐀-鿿]")
COMMA = "‚"  # "‚" looks like a comma, isn't one
PLACEHOLDER = "　"  # JP files fill unused description lines with an ideographic space
DESC_WIDTH = 44  # characters per description line (official English lines are <= ~45)

POSITIONAL = [
    r"Unit_Explanation\d+_ja\.csv", r"StageName.*_ja\.csv", r"Enemyname\.tsv",
    r"EnemyPictureBook.*_ja\.csv", r"Nyancombo\d*_ja\.csv", r"Category_Explanation_ja\.csv",
    r"God.*_ja\.csv", r"GamatotoExpedition_.*_ja\.csv", r"medalname\.tsv",
    r"GatyaitemName\.csv", r"PageName_ja\.csv", r"Mission_Name\.csv",
    r"SkillDescriptions\.csv", r"matatabi_Popup\.csv", r"omikuji_.*\.(csv|tsv)",
]
KEYED = [r"localizable\.tsv", r"Map_Name\.csv"]
UNIT_DESC = re.compile(r"Unit_Explanation\d+_ja\.csv")


def norm_key(t: str) -> str:
    return re.sub(r"[\s!！?？~～〜・、。,.★☆「」『』:：()（）‚　]", "",
                  unicodedata.normalize("NFKC", t))


def en_name(jp_name: str) -> str:
    return jp_name.replace("_ja.", "_en.")


def delimiter(text: str, name: str) -> str:
    if name.endswith(".tsv"):
        return "\t"
    first = next((l for l in text.splitlines() if l.strip()), "")
    return "|" if first.count("|") > first.count(",") else ","


def clean_en(s: str, jp_delim: str) -> str:
    s = re.sub(r"\s+", " ", s.strip())
    if jp_delim == ",":
        s = s.replace(",", COMMA)
    elif jp_delim == "\t":
        s = s.replace("\t", " ")
    return s


def usable_en(s: str | None) -> bool:
    """EN placeholders for JP-only content are Japanese ("ダミー"): not usable."""
    return bool(s and s.strip() and not JPCH.search(s))


class Fallback:
    """English for JP-only text, from the user's custom_names.csv."""

    def __init__(self) -> None:
        self.map: dict[str, str] = {}
        self.source: dict[str, str] = {}
        if CUSTOM.exists():
            for r in csv.DictReader(CUSTOM.open(encoding="utf-8-sig")):
                ja, en = (r.get("japanese") or "").strip(), (r.get("english") or "").strip()
                if ja and en and not ja.startswith("#"):
                    self._add(ja, en, "custom")

    def _add(self, ja: str, en: str, src: str) -> None:
        k = norm_key(ja)
        if k:
            self.map[k] = en  # later sources override earlier ones
            self.source[k] = src

    def get(self, ja: str) -> tuple[str, str] | None:
        if not JPCH.search(ja):
            return None
        k = norm_key(ja)
        return (self.map[k], self.source[k]) if k in self.map else None


class Merger:
    def __init__(self) -> None:
        self.fb = Fallback()
        self.stats: Counter = Counter()
        self.pending: dict[str, dict] = {}

    def field(self, jp: str, en: str | None, jd: str) -> str:
        """English for one JP field (keeps the field's trailing padding)."""
        if not JPCH.search(jp) or jp.lstrip().startswith("//"):  # "//" = data-file comment
            return jp
        pad = jp[len(jp.rstrip()):]
        if usable_en(en):
            self.stats["official"] += 1
            return clean_en(en, jd) + pad
        hit = self.fb.get(jp)
        if hit:
            self.stats[hit[1]] += 1
            return clean_en(hit[0], jd) + pad
        self.stats["still_japanese"] += 1
        self.pending.setdefault(norm_key(jp), {"text": jp.strip(), "kind": "field"})
        return jp

    def paragraph(self, jp_lines: list[str], en_lines: list[str] | None, jd: str) -> list[str]:
        """Unit description: translate the lines together, re-wrap onto the same fields."""
        n = len(jp_lines)
        if en_lines and any(usable_en(l) for l in en_lines):
            self.stats["official"] += sum(1 for l in jp_lines if JPCH.search(l))
            out = [clean_en(l, jd) if usable_en(l) else PLACEHOLDER for l in en_lines[:n]]
            return out + [PLACEHOLDER] * (n - len(out))
        text = "".join(l.strip() for l in jp_lines if l.strip() and l.strip() != PLACEHOLDER)
        if not JPCH.search(text):
            return jp_lines
        hit = self.fb.get(text)
        if not hit:
            self.stats["still_japanese"] += sum(1 for l in jp_lines if JPCH.search(l))
            self.pending.setdefault(norm_key(text), {"text": text, "kind": "paragraph", "lines": n})
            return jp_lines
        self.stats[hit[1]] += sum(1 for l in jp_lines if JPCH.search(l))
        wrapped = textwrap.wrap(clean_en(hit[0], jd), DESC_WIDTH) or [PLACEHOLDER]
        if len(wrapped) > n:  # too long: let the last line carry the rest
            wrapped = wrapped[:n - 1] + [" ".join(wrapped[n - 1:])]
        return wrapped + [PLACEHOLDER] * (n - len(wrapped))

    # -- file modes --------------------------------------------------------
    def positional(self, name: str, jp: str, en: str, jd: str, ed: str) -> str:
        jl, el = jp.split("\n"), en.split("\n")
        out = []
        for i, line in enumerate(jl):
            cr = "\r" if line.endswith("\r") else ""
            jf = line.rstrip("\r").split(jd)
            ef = el[i].rstrip("\r").split(ed) if i < len(el) else None
            if UNIT_DESC.fullmatch(name) and len(jf) >= 4:
                # name, 3 description lines, trailing empty field
                jf[0] = self.field(jf[0], ef[0] if ef else None, jd)
                desc = jf[1:4]
                jf[1:4] = self.paragraph(desc, ef[1:4] if ef and len(ef) >= 4 else None, jd)
            else:
                for c, f in enumerate(jf):
                    jf[c] = self.field(f, ef[c] if ef is not None and c < len(ef) else None, jd)
            out.append(jd.join(jf) + cr)
        return "\n".join(out)

    def keyed(self, name: str, jp: str, en: str, jd: str, ed: str) -> str:
        en_map = {}
        for line in en.splitlines():
            f = line.split(ed, 1)
            if len(f) == 2:
                en_map[f[0].strip()] = f[1]
        out = []
        for line in jp.split("\n"):
            cr = "\r" if line.endswith("\r") else ""
            f = line.rstrip("\r").split(jd, 1)
            if len(f) == 2:
                f[1] = self.field(f[1], en_map.get(f[0].strip()), jd)
            out.append(jd.join(f) + cr)
        return "\n".join(out)

    def id_rows(self, name: str, jp: str, en: str, jd: str, ed: str) -> str:
        """Rows that start with a numeric ID (MainMenu_ja.csv): match rows by ID."""
        en_rows = {}
        for line in en.splitlines():
            f = line.split(ed)
            if f and f[0].strip().isdigit():
                en_rows[f[0].strip()] = line
        by_pos = []
        for line in jp.split("\n"):
            f = line.rstrip("\r").split(jd)
            by_pos.append(en_rows.get(f[0].strip(), "") if f and f[0].strip().isdigit() else "")
        return self.positional(name, jp, "\n".join(by_pos), jd, ed)


def has_id_column(text: str, delim: str) -> bool:
    lines = [l for l in text.splitlines() if l.strip()]
    return len(lines) >= 3 and sum(l.split(delim)[0].strip().isdigit() for l in lines) >= 0.9 * len(lines)


def pick_mode(name: str, jp: str, en: str, jd: str, ed: str) -> str | None:
    if any(re.fullmatch(p, name) for p in KEYED):
        return "keyed"
    if has_id_column(jp, jd) and has_id_column(en, ed):
        return "id_rows"
    if any(re.fullmatch(p, name) for p in POSITIONAL):
        return "positional"
    nj, ne = len(jp.splitlines()), len(en.splitlines())
    return "positional" if nj and abs(nj - ne) <= max(2, nj // 20) else None


def main() -> dict:
    m = Merger()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    files = 0
    for jp_path in sorted(JP_DIR.iterdir()):
        name = jp_path.name
        if not name.endswith((".csv", ".tsv")):
            continue
        try:
            jp = jp_path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not JPCH.search(jp):
            continue
        en_path = EN_DIR / en_name(name)
        en = en_path.read_bytes().decode("utf-8", "replace") if en_path.exists() else ""
        jd, ed = delimiter(jp, name), (delimiter(en, name) if en else ",")
        mode = pick_mode(name, jp, en, jd, ed) if en else "positional"  # JP-only file: fallback only
        if mode is None:
            continue
        merged = getattr(m, mode)(name, jp, en, jd, ed)
        if merged != jp:
            (OUT / name).write_bytes(merged.encode("utf-8"))
            files += 1
    result = {"files": files, **m.stats, "japanese_only_unique": len(m.pending)}
    print(f"text merge: {result}")
    return result


if __name__ == "__main__":
    main()
