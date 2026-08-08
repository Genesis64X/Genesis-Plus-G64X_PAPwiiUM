#!/usr/bin/env python3
from pathlib import Path
import re
import sys
import tempfile

MARKER = "PAPwiiUM generated libogc SPR compatibility header"

def die(msg):
    raise RuntimeError(msg)

def parse_object_macros(text):
    names = []
    for raw in text.splitlines():
        m = re.match(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)(.*)$", raw)
        if not m:
            continue
        name, rest = m.group(1), m.group(2)

        if rest.startswith("("):
            continue

        if name.startswith("_"):
            continue

        names.append(name)

    return sorted(set(names))

def render(names, source_name):
    lines = [
        "#ifndef G64X_LIBOGC_SPR_COMPAT_H",
        "#define G64X_LIBOGC_SPR_COMPAT_H",
        "",
        f"/* {MARKER}",
        f" * Source: {source_name}",
        " *",
        " * Genesis Plus GX core code is platform-neutral and must not inherit",
        " * libogc's generic PowerPC SPR macro names (DEC, TBL, etc.).",
        " * osd.h has already included gccore.h before this file is included.",
        " */",
        "",
    ]
    for name in names:
        lines += [f"#ifdef {name}", f"#undef {name}", "#endif"]
    lines += ["", "#endif /* G64X_LIBOGC_SPR_COMPAT_H */", ""]
    return "\n".join(lines)

def write_if_changed(path, content):
    data = content.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        print(f"SPR compatibility header unchanged: {path}")
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    print(f"SPR compatibility header updated: {path}")
    return True

def generate(spr_path, out_path):
    spr_path = spr_path.resolve()
    out_path = out_path.resolve()

    if not spr_path.is_file():
        die(f"libogc spr.h not found: {spr_path}")

    text = spr_path.read_text(encoding="utf-8", errors="strict")
    names = parse_object_macros(text)
    if not names:
        die("no object-like SPR macros found")

    # Known collisions in this codebase.
    for required in ("DEC", "TBL"):
        if required not in names:
            die(f"expected current libogc SPR macro missing: {required}")

    content = render(names, str(spr_path))
    write_if_changed(out_path, content)

    print(f"libogc SPR object-like macros neutralized for core: {len(names)}")
    print("Confirmed: DEC")
    print("Confirmed: TBL")

def self_test():
    sample = """\
#ifndef TEST_SPR_H
#define TEST_SPR_H
#define DEC 22
#define TBL 284
#define TBU 285
#define SPR_FIELD 0x10
#define FUNC(x) ((x) + 1)
#define _PRIVATE 123
#endif
"""
    names = parse_object_macros(sample)
    assert "DEC" in names
    assert "TBL" in names
    assert "TBU" in names
    assert "SPR_FIELD" in names
    assert "FUNC" not in names
    assert "_PRIVATE" not in names
    hdr = render(names, "self-test")
    assert "#undef DEC" in hdr
    assert "#undef TBL" in hdr
    assert "#undef FUNC" not in hdr
    print("SPR generator self-test: OK")

if __name__ == "__main__":
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
            self_test()
        elif len(sys.argv) == 3:
            generate(Path(sys.argv[1]), Path(sys.argv[2]))
        else:
            print("Usage: generate_spr_compat.py <libogc spr.h> <output header>")
            print("       generate_spr_compat.py --self-test")
            raise SystemExit(2)
    except Exception as exc:
        print(f"SPR GENERATOR FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
