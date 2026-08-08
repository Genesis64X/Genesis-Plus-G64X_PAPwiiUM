#!/usr/bin/env python3
from pathlib import Path
import hashlib
import struct
import sys

# These are functional PUNCHiUM signatures that must survive into the linked DOL.
# Unlike a build-version string, they are actually referenced by runtime code and
# therefore cannot legitimately disappear just because --gc-sections runs.
REQUIRED_MARKERS = (
    b"T-574120-00",
    b"%s/paprium/%s",
    b"01 Theme of Paprium",
    b"31 Bad Dudes vs Paprium",
    b"PAPRIUM One-Hit Kill",
)
OPTIONAL_BUILD_MARKER = b"PAPwiiUM Wii v2.15"


def u32be(data, off):
    return struct.unpack_from(">I", data, off)[0]


def verify(path):
    p = Path(path)
    data = p.read_bytes()
    if len(data) < 0x100:
        raise RuntimeError("file too small to be a DOL")

    text_off = [u32be(data, 0x00 + 4*i) for i in range(7)]
    data_off = [u32be(data, 0x1C + 4*i) for i in range(11)]
    text_addr = [u32be(data, 0x48 + 4*i) for i in range(7)]
    data_addr = [u32be(data, 0x64 + 4*i) for i in range(11)]
    text_size = [u32be(data, 0x90 + 4*i) for i in range(7)]
    data_size = [u32be(data, 0xAC + 4*i) for i in range(11)]
    bss_addr = u32be(data, 0xD8)
    bss_size = u32be(data, 0xDC)
    entry = u32be(data, 0xE0)

    sections = []
    for kind, offs, addrs, sizes in (
        ("text", text_off, text_addr, text_size),
        ("data", data_off, data_addr, data_size),
    ):
        for i, (off, addr, size) in enumerate(zip(offs, addrs, sizes)):
            if not size:
                continue
            if off < 0x100 or off + size > len(data):
                raise RuntimeError(f"invalid {kind} section {i}")
            sections.append((kind, i, off, addr, size))

    if not sections:
        raise RuntimeError("DOL contains no loadable sections")
    if not any(addr <= entry < addr + size for _, _, _, addr, size in sections):
        raise RuntimeError(f"entry point 0x{entry:08X} is outside loadable sections")

    missing = [m.decode("latin-1") for m in REQUIRED_MARKERS if m not in data]
    if missing:
        raise RuntimeError("missing functional PUNCHiUM markers: " + ", ".join(missing))

    print("DOL validation: OK")
    print(f"File: {p}")
    print(f"Size: {len(data)} bytes")
    print(f"Entry: 0x{entry:08X}")
    print(f"Loadable sections: {len(sections)}")
    print(f"BSS: 0x{bss_addr:08X} + 0x{bss_size:X}")
    print(f"SHA256: {hashlib.sha256(data).hexdigest()}")
    print("PUNCHiUM signatures: OK")
    for marker in REQUIRED_MARKERS:
        print("  " + marker.decode("latin-1"))
    if OPTIONAL_BUILD_MARKER in data:
        print("Optional build-version marker: present")
    else:
        print("Optional build-version marker: optimized out (OK)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: verify_dol.py <boot.dol>")
        raise SystemExit(2)
    try:
        verify(sys.argv[1])
    except Exception as exc:
        print(f"DOL VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
