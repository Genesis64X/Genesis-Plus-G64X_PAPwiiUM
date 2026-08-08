#!/usr/bin/env python3
from pathlib import Path
import subprocess
import re
import sys

PIN = "3849f3d3432df1d6320574e73695dd379ecef2b3"
MARKER = "PAPwiiUM Wii v2.15"


def die(msg):
    raise RuntimeError(msg)


def git_head(root):
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def read_preserving_eol(path):
    data = path.read_bytes()

    # Source files in this old Wii frontend are not uniformly UTF-8.
    # Latin-1 is intentionally used here as a lossless 1-byte <-> 1-codepoint
    # transport encoding: every original byte 0x00..0xFF round-trips unchanged.
    # All patch tokens are ASCII, so this does not reinterpret or
    # normalize legacy comments/strings.
    text = data.decode("latin-1")

    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and lf:
        die(f"{path}: mixed line endings before patch ({crlf} CRLF, {lf} LF)")
    eol = "\r\n" if crlf else "\n"
    return text, eol


def write_preserving_eol(path, text, eol):
    # Guard against accidentally converting the entire upstream file.
    if eol == "\r\n":
        bare_lf = text.replace("\r\n", "").count("\n")
        if bare_lf:
            die(f"{path}: patch introduced {bare_lf} bare LF line endings")

    try:
        data = text.encode("latin-1")
    except UnicodeEncodeError as exc:
        die(f"{path}: patch introduced a non-byte-preserving character: {exc}")

    path.write_bytes(data)


def replace_exact(text, old, new, label):
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def patch_file_load(path):
    text, eol = read_preserving_eol(path)

    old = "char rom_filename[256];"
    new = eol.join([
        "char rom_filename[256];",
        "",
        "/* PAPwiiUM standalone bridge for the PUNCHiUM core. */",
        "char g_rom_dir[256];",
        "uint8_t punchium_audio_track_format = 1; /* WAV only */",
        "bool punchium_tile_cache = true;",
        "bool punchium_cheat_saitama = false;",
        "",
        "const char g64x_paprium_wii_build[] __attribute__((used)) =",
        f'  "{MARKER}";',
    ])
    text = replace_exact(text, old, new, "standalone globals")

    old = eol.join([
        "    /* load game file */",
        "    size = load_rom(filename);",
    ])
    new = eol.join([
        "    /* Expose the ROM directory to PUNCHiUM before cartridge init. */",
        "    strncpy(g_rom_dir, filepath, sizeof(g_rom_dir) - 1);",
        "    g_rom_dir[sizeof(g_rom_dir) - 1] = 0;",
        "",
        "    /* PUNCHiUM appends /paprium itself. Remove Wii's trailing slash. */",
        "    {",
        "      size_t len = strlen(g_rom_dir);",
        "      while (len && g_rom_dir[len - 1] == '/')",
        "      {",
        "        g_rom_dir[--len] = 0;",
        "      }",
        "    }",
        "",
        "    /* Reload persistent standalone PAPRIUM options before cartridge init. */",
        "    g64x_paprium_cheat_load();",
        "",
        "    /* load game file */",
        "    size = load_rom(filename);",
    ])
    text = replace_exact(text, old, new, "ROM directory bridge")

    write_preserving_eol(path, text, eol)


def patch_config_header(path):
    text, eol = read_preserving_eol(path)
    if "g64x_paprium_cheat_get" in text:
        return
    anchor = "extern void config_default(void);"
    if text.count(anchor) != 1:
        die(f"config.h PAPRIUM option API: expected exactly 1 config_default prototype, found {text.count(anchor)}")
    block = eol.join([
        anchor,
        "",
        "/* PAPwiiUM standalone PAPRIUM option; persisted outside config.ini. */",
        "extern void g64x_paprium_cheat_load(void);",
        "extern int g64x_paprium_cheat_get(void);",
        "extern void g64x_paprium_cheat_toggle(void);",
    ])
    text = text.replace(anchor, block, 1)
    write_preserving_eol(path, text, eol)


def patch_config_source(path):
    text, eol = read_preserving_eol(path)
    if "paprium_g64x.cfg" in text:
        return
    anchor = "t_config config;"
    if text.count(anchor) != 1:
        die(f"config.c PAPRIUM persistence: expected exactly 1 config global, found {text.count(anchor)}")
    block = eol.join([
        anchor,
        "",
        "/* PAPwiiUM: keep this option separate from the binary config.ini so its",
        " * structure/version remains fully compatible with stock Genesis Plus GX.",
        " */",
        "extern bool punchium_cheat_saitama;",
        "",
        "void g64x_paprium_cheat_load(void)",
        "{",
        "  char fname[MAXPATHLEN];",
        "  sprintf(fname, \"%s/paprium_g64x.cfg\", DEFAULT_PATH);",
        "  FILE *fp = fopen(fname, \"rb\");",
        "  punchium_cheat_saitama = false;",
        "  if (fp)",
        "  {",
        "    punchium_cheat_saitama = (fgetc(fp) == '1');",
        "    fclose(fp);",
        "  }",
        "}",
        "",
        "int g64x_paprium_cheat_get(void)",
        "{",
        "  return punchium_cheat_saitama ? 1 : 0;",
        "}",
        "",
        "void g64x_paprium_cheat_toggle(void)",
        "{",
        "  char fname[MAXPATHLEN];",
        "  punchium_cheat_saitama = !punchium_cheat_saitama;",
        "  sprintf(fname, \"%s/paprium_g64x.cfg\", DEFAULT_PATH);",
        "  FILE *fp = fopen(fname, \"wb\");",
        "  if (fp)",
        "  {",
        "    fputc(punchium_cheat_saitama ? '1' : '0', fp);",
        "    fclose(fp);",
        "  }",
        "}",
    ])
    text = text.replace(anchor, block, 1)
    write_preserving_eol(path, text, eol)


def patch_punchium(path):
    text, eol = read_preserving_eol(path)

    # Wii build: try the external WAV path only.
    old = "for (int i = 0; i < 5 && !track_loaded; i++) {"
    new = "for (int i = 0; i < 1 && !track_loaded; i++) {"
    text = replace_exact(text, old, new, "WAV-only loop bound")

    # PUNCHiUM was developed around Genesis Plus GX's LSB_FIRST layout.
    # Logical byte accesses in the original code use ^1. Those become ^0 on
    # big-endian Wii. However, PUNCHiUM also has a smaller set of *raw* direct
    # byte accesses that intentionally observe the pair-swapped LSB_FIRST
    # storage. To preserve the exact original semantics on PowerPC, those raw
    # byte sites need the inverse selector.
    macro = eol.join([
        "/* PAPwiiUM: PUNCHiUM host-endian compatibility.",
        " * BYTE_XOR is for logical 68k bytes; RAW_BYTE_XOR preserves direct",
        " * byte-array semantics from the original LSB_FIRST implementation.",
        " */",
        "#ifdef LSB_FIRST",
        "#define PUNCHIUM_BYTE_XOR 1",
        "#define PUNCHIUM_RAW_BYTE_XOR 0",
        "#define PUNCHIUM_READ_U32_WORDPAIR(base, offset) (*(uint32 *)((base) + (offset)))",
        "#else",
        "#define PUNCHIUM_BYTE_XOR 0",
        "#define PUNCHIUM_RAW_BYTE_XOR 1",
        "#define PUNCHIUM_READ_U32_WORDPAIR(base, offset) \\",
        "  ((uint32)(*(uint16 *)((base) + (offset))) | ((uint32)(*(uint16 *)((base) + (offset) + 2)) << 16))",
        "#endif",
        "#define PUNCHIUM_RAW_U8(base, offset) ((base)[(offset) ^ PUNCHIUM_RAW_BYTE_XOR])",
    ])

    if "#define PUNCHIUM_RAW_BYTE_XOR 0" not in text:
        anchor = "#define DEBUG_SPRITE 0"
        if text.count(anchor) != 1:
            die(f"PUNCHiUM endian fix: expected exactly 1 DEBUG_SPRITE anchor, found {text.count(anchor)}")
        text = text.replace(anchor, anchor + eol + eol + macro, 1)

    # Convert logical byte-lane accesses before handling raw arrays.
    raw_xor_count = text.count("^1")
    if raw_xor_count:
        text = text.replace("^1", "^PUNCHIUM_BYTE_XOR")
    if raw_xor_count == 0 and "^PUNCHIUM_BYTE_XOR" not in text:
        die("PUNCHiUM endian fix: no byte-lane XOR sites found")

    # Preserve the original little-endian *raw* byte-array behavior on Wii.
    # These are deliberately not logical READ_BYTE-style accesses in upstream.
    raw_replacements = [
        ("punchium_s.music_ram[0x09]", "PUNCHIUM_RAW_U8(punchium_s.music_ram, 0x09)"),
        ("punchium_s.music_ram[0x0B]", "PUNCHIUM_RAW_U8(punchium_s.music_ram, 0x0B)"),
        ("punchium_s.music_ram[0x0D]", "PUNCHIUM_RAW_U8(punchium_s.music_ram, 0x0D)"),
        ("int animPtr = *(uint32*) (punchium_obj_ram + (obj+1)*4);",
         "int animPtr = PUNCHIUM_READ_U32_WORDPAIR(punchium_obj_ram, (obj+1)*4);"),
        ("framePtr = *(uint32*) (punchium_obj_ram + animPtr + anim*4);",
         "framePtr = PUNCHIUM_READ_U32_WORDPAIR(punchium_obj_ram, animPtr + anim*4);"),
        ("punchium_obj_ram[framePtr + 0]", "PUNCHIUM_RAW_U8(punchium_obj_ram, framePtr + 0)"),
        ("punchium_obj_ram[framePtr + 1]", "PUNCHIUM_RAW_U8(punchium_obj_ram, framePtr + 1)"),
        ("punchium_obj_ram[framePtr + 2]", "PUNCHIUM_RAW_U8(punchium_obj_ram, framePtr + 2)"),
        ("punchium_obj_ram[framePtr + 3]", "PUNCHIUM_RAW_U8(punchium_obj_ram, framePtr + 3)"),
        ("punchium_obj_ram[spritePtr + 3]", "PUNCHIUM_RAW_U8(punchium_obj_ram, spritePtr + 3)"),
        ("punchium_obj_ram[spritePtr + 1]", "PUNCHIUM_RAW_U8(punchium_obj_ram, spritePtr + 1)"),
        ("punchium_obj_ram[spritePtr + 0]", "PUNCHIUM_RAW_U8(punchium_obj_ram, spritePtr + 0)"),
        ("punchium_s.exps_ram[2] = 14;", "PUNCHIUM_RAW_U8(punchium_s.exps_ram, 2) = 14;"),
        ("punchium_s.ram[0xB02 + (count-1)*8] = 0;", "PUNCHIUM_RAW_U8(punchium_s.ram, 0xB02 + (count-1)*8) = 0;"),
        ("punchium_s.exps_ram[2 + (count-1)*8] = 0;", "PUNCHIUM_RAW_U8(punchium_s.exps_ram, 2 + (count-1)*8) = 0;"),
        ("punchium_s.exps_ram[2 + (count-81)*8] = 0;", "PUNCHIUM_RAW_U8(punchium_s.exps_ram, 2 + (count-81)*8) = 0;"),
        ("punchium_s.ram[0x1801] = flags & 0x01;", "PUNCHIUM_RAW_U8(punchium_s.ram, 0x1801) = flags & 0x01;"),
        ("punchium_s.ram[0x1800]  = (flags & 0x01) ? 0x80 : 0x00;", "PUNCHIUM_RAW_U8(punchium_s.ram, 0x1800)  = (flags & 0x01) ? 0x80 : 0x00;"),
        ("punchium_s.ram[0x1800] += (flags & 0x02) ? 0x40 : 0x00;", "PUNCHIUM_RAW_U8(punchium_s.ram, 0x1800) += (flags & 0x02) ? 0x40 : 0x00;"),
    ]
    for old, new in raw_replacements:
        count = text.count(old)
        if count < 1:
            die(f"PUNCHiUM raw-byte fix: expected at least 1 match for {old!r}, found {count}")
        text = text.replace(old, new)

    # SFX table has packed 24-bit size/type bytes next to native 16-bit words.
    # The single-byte fields must preserve the original direct-array lane.
    sfx_replacements = [
        ("size = (*(uint8_t *)(sfx + data*8 + 4) << 16) | (*(uint16_t *)(sfx + data*8 + 6));",
         "size = (PUNCHIUM_RAW_U8(sfx, data*8 + 4) << 16) | (*(uint16_t *)(sfx + data*8 + 6));"),
        ("type = *(uint8_t *)(sfx + data*8 + 5);",
         "type = PUNCHIUM_RAW_U8(sfx, data*8 + 5);"),
        ("voice->size = (*(uint8 *)(sfx + voice->num*8 + 4) << 16) | (*(uint16 *)(sfx + voice->num*8 + 6));",
         "voice->size = (PUNCHIUM_RAW_U8(sfx, voice->num*8 + 4) << 16) | (*(uint16 *)(sfx + voice->num*8 + 6));"),
    ]
    for old, new in sfx_replacements:
        text = replace_exact(text, old, new, "PUNCHiUM SFX raw-byte lane")

    # Upstream reset_tile_cache() uses memset(value=2048) on a uint32_t array.
    # memset repeats only the low byte, so it writes zero instead of the 2048
    # sentinel. That turns every bucket into a false index-0 candidate after a
    # music-driven cache reset. Initialize every uint32_t bucket explicitly.
    bad_hash_reset = "memset(tile_cache.hash_table, MAX_TILE_CACHE_ENTRIES, sizeof(uint32_t) * TILE_CACHE_HASH_SIZE);"
    good_hash_reset = eol.join([
        "for (int i = 0; i < TILE_CACHE_HASH_SIZE; i++) {",
        "\ttile_cache.hash_table[i] = MAX_TILE_CACHE_ENTRIES;",
        "}",
    ])
    text = replace_exact(text, bad_hash_reset, good_hash_reset, "tile cache hash sentinel reset")

    # PAPwiiUM: stream external WAV audio in bounded PCM windows instead of
    # synchronously decoding the complete track on every boss/stage transition.
    # 32768 stereo s16 frames = 128 KiB and ~683 ms at 48 kHz.
    stream_define = "#define PUNCHIUM_WAV_STREAM_FRAMES 32768"
    if stream_define not in text:
        anchor = "#define MAX_TILE_SIZE 512"
        if text.count(anchor) != 1:
            die(f"WAV streaming: expected exactly 1 MAX_TILE_SIZE anchor, found {text.count(anchor)}")
        text = text.replace(anchor, anchor + eol + stream_define, 1)

    if "uint32_t stream_start_frame;" not in text:
        lines = text.split(eol)
        matches = [i for i, line in enumerate(lines) if "int samples_read;" in line]
        if len(matches) != 1:
            die(f"WAV streaming state: expected exactly 1 samples_read field, found {len(matches)}")
        i = matches[0]
        lines[i + 1:i + 1] = [
            "\tuint32_t stream_start_frame; // absolute first frame currently buffered",
            "\tuint32_t stream_frames;      // number of valid frames in buffer",
            "\tbool wav_streaming;          // dr_wav decoder stays open while playing",
        ]
        text = eol.join(lines)

    if "punchium_track.wav_streaming = false;" not in text:
        old = eol.join([
            "\tpunchium_track.total_samples = 0;",
            "\tpunchium_track.buffer = NULL;",
            "\tmp3dec_init(&punchium_track.mp3);",
        ])
        new = eol.join([
            "\tpunchium_track.total_samples = 0;",
            "\tpunchium_track.buffer = NULL;",
            "\tpunchium_track.stream_start_frame = 0;",
            "\tpunchium_track.stream_frames = 0;",
            "\tpunchium_track.wav_streaming = false;",
            "\tmp3dec_init(&punchium_track.mp3);",
        ])
        text = replace_exact(text, old, new, "WAV streaming state init")

    # Only uninit dr_wav when a WAV decoder is actually live, then clear stream state.
    if "if (punchium_track.wav_streaming)" not in text:
        old = eol.join([
            "\tdrwav_uninit(&punchium_track.wav);",
            "\tmemset(&punchium_track.wav, 0, sizeof(drwav));",
        ])
        new = eol.join([
            "\tif (punchium_track.wav_streaming)",
            "\t\tdrwav_uninit(&punchium_track.wav);",
            "\tmemset(&punchium_track.wav, 0, sizeof(drwav));",
            "\tpunchium_track.wav_streaming = false;",
            "\tpunchium_track.stream_start_frame = 0;",
            "\tpunchium_track.stream_frames = 0;",
        ])
        text = replace_exact(text, old, new, "WAV streaming cleanup")

    if "malloc(PUNCHIUM_WAV_STREAM_FRAMES" not in text:
        # Structural conversion: do not require the complete upstream WAV block
        # to match byte-for-byte. Locate its stable allocation/decode statements.
        lines = text.split(eol)

        alloc_hits = [
            i for i, line in enumerate(lines)
            if "punchium_track.buffer" in line
            and "malloc(" in line
            and "punchium_track.total_samples" in line
            and "punchium_track.channels" in line
            and "sizeof(int16_t)" in line
        ]
        if len(alloc_hits) != 1:
            die(f"WAV streaming allocation: expected exactly 1 full-track malloc, found {len(alloc_hits)}")
        alloc_i = alloc_hits[0]

        decode_hits = [
            i for i in range(alloc_i + 1, min(len(lines), alloc_i + 40))
            if "drwav_read_pcm_frames_s16" in lines[i]
            and "totalPCMFrameCount" in lines[i]
            and "punchium_track.buffer" in lines[i]
        ]
        if len(decode_hits) != 1:
            die(f"WAV streaming decode: expected exactly 1 full-track drwav read after malloc, found {len(decode_hits)}")
        decode_i = decode_hits[0]

        loaded_hits = [
            i for i in range(decode_i + 1, min(len(lines), decode_i + 8))
            if lines[i].strip() == "track_loaded = true;"
        ]
        if len(loaded_hits) != 1:
            die(f"WAV streaming completion: expected exactly 1 track_loaded after decode, found {len(loaded_hits)}")

        indent = lines[alloc_i][:len(lines[alloc_i]) - len(lines[alloc_i].lstrip())]
        lines[alloc_i] = (
            indent
            + "punchium_track.buffer = (int16_t*)malloc("
            + "PUNCHIUM_WAV_STREAM_FRAMES * punchium_track.channels * sizeof(int16_t));"
        )

        stream_lines = [
            indent + "punchium_track.wav_streaming = true;",
            indent + "punchium_track.stream_start_frame = 0;",
            indent + "punchium_track.stream_frames = (uint32_t)drwav_read_pcm_frames_s16(",
            indent + "\t&punchium_track.wav, PUNCHIUM_WAV_STREAM_FRAMES, punchium_track.buffer);",
            indent + "if (!punchium_track.stream_frames) {",
            indent + "\tfree(punchium_track.buffer);",
            indent + "\tpunchium_track.buffer = NULL;",
            indent + "\tdrwav_uninit(&punchium_track.wav);",
            indent + "\tpunchium_track.wav_streaming = false;",
            indent + "\tcontinue;",
            indent + "}",
        ]

        lines[decode_i:decode_i + 1] = stream_lines
        text = eol.join(lines)

    player_anchor = "static void punchium_music_player(int *out_l, int *out_r) {"
    if "static int punchium_wav_stream_refill" not in text:
        if text.count(player_anchor) != 1:
            die(f"WAV streaming helper: expected exactly 1 music-player function, found {text.count(player_anchor)}")
        helper = eol.join([
            "/* PAPwiiUM: refill the current WAV PCM window. Sequential refills",
            " * continue from the open decoder; loops/reloads seek to target_frame.",
            " */",
            "static int punchium_wav_stream_refill(uint32_t target_frame)",
            "{",
            "\tif (!punchium_track.wav_streaming || !punchium_track.buffer)",
            "\t\treturn 0;",
            "",
            "\tif (target_frame >= punchium_track.total_samples)",
            "\t\ttarget_frame = 0;",
            "",
            "\tif (target_frame != (punchium_track.stream_start_frame + punchium_track.stream_frames)) {",
            "\t\tif (!drwav_seek_to_pcm_frame(&punchium_track.wav, target_frame))",
            "\t\t\treturn 0;",
            "\t}",
            "",
            "\tpunchium_track.stream_start_frame = target_frame;",
            "\tpunchium_track.stream_frames = (uint32_t)drwav_read_pcm_frames_s16(",
            "\t\t&punchium_track.wav, PUNCHIUM_WAV_STREAM_FRAMES, punchium_track.buffer);",
            "\treturn punchium_track.stream_frames > 0;",
            "}",
            "",
            player_anchor,
        ])
        text = text.replace(player_anchor, helper, 1)

    if "uint32_t available_samples = punchium_track.wav_streaming" not in text:
        # Structural conversion of the music-player sample lookup. Do not
        # require the complete function prelude to match whitespace exactly.
        lines = text.split(eol)

        fn_hits = [
            i for i, line in enumerate(lines)
            if "static void punchium_music_player(" in line
        ]
        if len(fn_hits) != 1:
            die(f"WAV streaming music player: expected exactly 1 function, found {len(fn_hits)}")
        fn_i = fn_hits[0]

        # Locate the original full-track sample position and sample fetch block.
        sample_hits = [
            i for i in range(fn_i + 1, min(len(lines), fn_i + 40))
            if "sample_pos" in lines[i]
            and "punchium_s.music_pos" in lines[i]
            and "punchium_track.channels" in lines[i]
            and "=" in lines[i]
        ]
        if len(sample_hits) != 1:
            die(f"WAV streaming music player: expected exactly 1 original sample_pos line, found {len(sample_hits)}")
        sample_i = sample_hits[0]

        if_hits = [
            i for i in range(sample_i + 1, min(len(lines), sample_i + 8))
            if "sample_pos <" in lines[i]
            and "punchium_track.total_samples" in lines[i]
            and "punchium_track.channels" in lines[i]
        ]
        if len(if_hits) != 1:
            die(f"WAV streaming music player: expected exactly 1 original sample bounds check, found {len(if_hits)}")
        if_i = if_hits[0]

        # Find the closing brace of that tiny sample-fetch if block. The
        # upstream block contains l/r assignments and closes before music_tick.
        tick_hits = [
            i for i in range(if_i + 1, min(len(lines), if_i + 12))
            if "punchium_s.music_tick +=" in lines[i]
        ]
        if len(tick_hits) != 1:
            die(f"WAV streaming music player: expected exactly 1 music_tick line after sample block, found {len(tick_hits)}")
        tick_i = tick_hits[0]

        close_candidates = [
            i for i in range(if_i + 1, tick_i)
            if lines[i].strip() == "}"
        ]
        if len(close_candidates) < 1:
            die("WAV streaming music player: sample-fetch closing brace not found")
        close_i = close_candidates[-1]

        indent = lines[sample_i][:len(lines[sample_i]) - len(lines[sample_i].lstrip())]

        new_block = [
            indent + "uint32_t frame_pos = (uint32_t)punchium_s.music_pos;",
            indent + "uint32_t local_frame = frame_pos;",
            indent + "if (punchium_track.wav_streaming) {",
            indent + "\tuint32_t stream_end = punchium_track.stream_start_frame + punchium_track.stream_frames;",
            indent + "\tif (frame_pos < punchium_track.stream_start_frame || frame_pos >= stream_end) {",
            indent + "\t\tif (!punchium_wav_stream_refill(frame_pos))",
            indent + "\t\t\treturn;",
            indent + "\t}",
            indent + "\tlocal_frame = frame_pos - punchium_track.stream_start_frame;",
            indent + "}",
            "",
            indent + "uint32_t sample_pos = local_frame * punchium_track.channels;",
            indent + "uint32_t available_samples = punchium_track.wav_streaming",
            indent + "\t? (punchium_track.stream_frames * punchium_track.channels)",
            indent + "\t: (punchium_track.total_samples * punchium_track.channels);",
            indent + "if (sample_pos < available_samples) {",
            indent + "\tl = punchium_track.buffer[sample_pos];",
            indent + "\tr = (punchium_track.channels > 1) ? punchium_track.buffer[sample_pos + 1] : l;",
            indent + "}",
        ]

        lines[sample_i:close_i + 1] = new_block
        text = eol.join(lines)

    # PAPwiiUM: the streaming build keeps dr_wav's FILE handle open while a
    # track is playing. Release PUNCHiUM frontend resources before libfat/device
    # shutdown so sd:/ is never unmounted underneath an active WAV decoder.
    cleanup_marker = "void g64x_punchium_frontend_shutdown(void)"
    if cleanup_marker not in text:
        anchor = "static void punchium_init()"
        if text.count(anchor) != 1:
            die(f"PUNCHiUM shutdown cleanup: expected exactly 1 punchium_init anchor, found {text.count(anchor)}")

        cleanup = eol.join([
            "/* PAPwiiUM: release frontend-owned PUNCHiUM resources before",
            " * FAT/device shutdown. Emulation state fields are left intact",
            " * so the stock autosave path can still serialize game state.",
            " */",
            "void g64x_punchium_frontend_shutdown(void)",
            "{",
            "\tif (punchium_track.wav_streaming) {",
            "\t\tdrwav_uninit(&punchium_track.wav);",
            "\t\tpunchium_track.wav_streaming = false;",
            "\t}",
            "\tmemset(&punchium_track.wav, 0, sizeof(drwav));",
            "\tpunchium_track.stream_start_frame = 0;",
            "\tpunchium_track.stream_frames = 0;",
            "",
            "\tif (punchium_track.buffer) {",
            "\t\tfree(punchium_track.buffer);",
            "\t\tpunchium_track.buffer = NULL;",
            "\t}",
            "",
            "\tif (punchium_track.vorbis) {",
            "\t\tmy_stb_vorbis_close(punchium_track.vorbis);",
            "\t\tpunchium_track.vorbis = NULL;",
            "\t}",
            "",
            "#if TILE_CACHE_ENABLE",
            "\tfree_tile_cache();",
            "#endif",
            "}",
            "",
            anchor,
        ])
        text = text.replace(anchor, cleanup, 1)

    write_preserving_eol(path, text, eol)


def patch_vdp_ctrl(path):
    text, eol = read_preserving_eol(path)

    # The PUNCHiUM fork left a libretro logger type in the shared VDP core.
    # Makefile.wii compiles core/vdp_ctrl.c but does not compile libretro.c,
    # therefore retro_log_printf_t and RETRO_LOG_ERROR do not exist on Wii.
    #
    # A function-like no-op macro is the least invasive standalone fix:
    # every log_cb(...) call is preprocessed away, so its libretro-only
    # arguments (including RETRO_LOG_ERROR) never need to be defined.
    old = "extern retro_log_printf_t log_cb;"
    new = eol.join([
        "/* Standalone Wii build: libretro logger is unavailable here. */",
        "#define log_cb(...) ((void)0)",
    ])
    text = replace_exact(text, old, new, "Wii VDP libretro logger shim")

    write_preserving_eol(path, text, eol)




def patch_shared(path):
    text, eol = read_preserving_eol(path)

    include_line = '#include "g64x_libogc_spr_compat.h"'
    if include_line in text:
        # Persistent shared-work baseline from an earlier v1.x build.
        return

    old = '#include "osd.h"'
    new = eol.join([
        '#include "osd.h"',
        '',
        '/* PAPwiiUM: strip libogc PowerPC SPR macros from platform-neutral core. */',
        '#ifdef HW_RVL',
        '#include "g64x_libogc_spr_compat.h"',
        '#endif',
    ])
    text = replace_exact(text, old, new, "shared core libogc SPR compatibility include")
    write_preserving_eol(path, text, eol)



def patch_cdrom_header(path):
    text, eol = read_preserving_eol(path)

    # libchdr/cdrom.h is compiled as a standalone third-party header and only
    # includes <stdint.h>, but this fork uses the project-specific INLINE macro
    # for three tiny conversion helpers. In the Wii cdrom.c compile path,
    # core/macros.h is not included, so INLINE is undefined.
    #
    # Make the header self-contained without pulling Genesis Plus GX macros into
    # libchdr: use standard C "static inline" for exactly those three helpers.
    expected = (
        "INLINE uint32_t msf_to_lba(uint32_t msf)",
        "INLINE uint32_t lba_to_msf(uint32_t lba)",
        "INLINE uint32_t lba_to_msf_alt(int lba)",
    )
    for old in expected:
        count = text.count(old)
        if count != 1:
            die(f"libchdr INLINE fix: expected exactly 1 match for {old!r}, found {count}")
        text = text.replace(old, old.replace("INLINE", "static inline", 1), 1)

    write_preserving_eol(path, text, eol)



def patch_coretypes(path):
    text, eol = read_preserving_eol(path)

    if "core_fsize(core_file *f)" in text:
        return

    anchor = "#define core_ftell                cdStreamTell"
    if text.count(anchor) != 1:
        die(f"libchdr core_fsize fix: expected exactly 1 core_ftell anchor, found {text.count(anchor)}")

    block = eol.join([
        anchor,
        "",
        "/* PAPwiiUM: libchdr expects core_fsize(), but the cdStream adapter",
        " * in this fork maps every other core_file operation and omits size.",
        " * Preserve the current stream position while determining EOF.",
        " */",
        "static inline size_t core_fsize(core_file *f)",
        "{",
        "  long current = core_ftell(f);",
        "  long end;",
        "",
        "  core_fseek(f, 0, SEEK_END);",
        "  end = core_ftell(f);",
        "  core_fseek(f, current, SEEK_SET);",
        "",
        "  return (end < 0) ? 0 : (size_t)end;",
        "}",
    ])

    text = text.replace(anchor, block, 1)
    write_preserving_eol(path, text, eol)



def patch_makefile_wii(path):
    text, eol = read_preserving_eol(path)

    # libchdr's flac.c includes <dr_libs/dr_flac.h>. The pinned fork contains
    # no deps/dr_libs directory and Makefile.wii does not expose the deps root.
    # The pinned libchdr tree expects dr_flac under its deps include root.
    if "$(CHDLIBDIR)/deps " not in text and "$(CHDLIBDIR)/deps \\" not in text:
        old = "$(CHDLIBDIR)/src $(CHDLIBDIR)/deps/libFLAC/include $(CHDLIBDIR)/deps/lzma \\"
        new = "$(CHDLIBDIR)/src $(CHDLIBDIR)/deps $(CHDLIBDIR)/deps/libFLAC/include $(CHDLIBDIR)/deps/lzma \\"
        text = replace_exact(text, old, new, "Wii libchdr deps include root")

    # Use recursive $(MAKE) with '+' so GNU make propagates jobserver/flags
    # correctly. This removes the previous "jobserver unavailable" behavior
    # and lets -k/-j reach the build_wii submake.
    old_rule = "@make --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile.wii"
    new_rule = "+@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile.wii"
    if old_rule in text:
        text = text.replace(old_rule, new_rule, 1)
    elif new_rule not in text:
        die("Wii recursive make rule not found")

    write_preserving_eol(path, text, eol)



def patch_menu_lwp(path):
    text, eol = read_preserving_eol(path)

    old = "#include <ogc/lwp_threads.h>"
    new = "#include <ogc/lwp.h>"

    if old in text:
        if text.count(old) != 1:
            die(f"Wii LWP header fix: expected exactly 1 old include, found {text.count(old)}")
        text = text.replace(old, new, 1)
    elif new not in text:
        die("Wii LWP header fix: neither old private nor new public header found")

    # Old Genesis Plus GX calls the private libogc function
    # __lwp_thread_stopmultitasking(reload) during final frontend shutdown.
    # Current libogc removed that private API.
    #
    # Do NOT depend on the exact whitespace/call formatting in this legacy
    # menu.c. Instead, add a local function-like compatibility macro beside
    # the existing exit callback declaration. The original call can then
    # remain byte-for-byte untouched and the preprocessor maps it to the
    # intended callback invocation.
    shim = "#define __lwp_thread_stopmultitasking(entry) ((entry)())"
    if "__lwp_thread_stopmultitasking" in text and shim not in text:
        anchor = "void (*reload)(void);"
        if text.count(anchor) != 1:
            die(f"Wii LWP exit shim: expected exactly 1 reload declaration, found {text.count(anchor)}")

        block = eol.join([
            "void (*reload)(void);",
            "",
            "/* PAPwiiUM: current libogc removed the old private LWP scheduler",
            " * handoff. Preserve the legacy frontend's final exit callback.",
            " */",
            "#ifndef __lwp_thread_stopmultitasking",
            shim,
            "#endif",
        ])
        text = text.replace(anchor, block, 1)

    # A source that already migrated to reload() does not need the shim.
    if "__lwp_thread_stopmultitasking" not in text and "reload();" not in text:
        die("Wii LWP exit compatibility: no private call and no direct reload callback found")

    write_preserving_eol(path, text, eol)


def patch_menu_paprium_option(path):
    text, eol = read_preserving_eol(path)
    lines = text.split(eol)

    # Idempotent complete patch.
    if (
        any("PAPRIUM One-Hit Kill: OFF" in line for line in lines)
        and any("m->max_items = 14;" in line for line in lines)
        and any("case 13: /*** PAPRIUM one-hit kill ***/" in line for line in lines)
    ):
        return

    # 1) Static Wii menu item: insert immediately after Wiimote Calibration.
    if not any("PAPRIUM One-Hit Kill: OFF" in line for line in lines):
        hits = [
            i for i, line in enumerate(lines)
            if "Wiimote Calibration: AUTO" in line
            and "Calibrate Wii remote pointer" in line
            and "{NULL,NULL" in line
        ]
        if len(hits) != 1:
            die(f"PAPRIUM one-hit menu item: expected exactly 1 Wii calibration item, found {len(hits)}")
        i = hits[0]
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines.insert(
            i + 1,
            indent + '{NULL,NULL,"PAPRIUM One-Hit Kill: OFF","Reload PAPRIUM after changing", 56,132,276,48},'
        )

    # 2) Runtime text refresh: insert after calibration comment setup.
    if not any('items[13].text' in line and "PAPRIUM One-Hit Kill" in line for line in lines):
        hits = [
            i for i, line in enumerate(lines)
            if "items[12].comment" in line
            and "Reset default Wii remote pointer calibration" in line
            and "Calibrate Wii remote pointer" in line
            and "sprintf" in line
        ]
        if len(hits) != 1:
            die(f"PAPRIUM one-hit menu state: expected exactly 1 calibration comment line, found {len(hits)}")
        i = hits[0]
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines.insert(
            i + 1,
            indent + 'sprintf (items[13].text, "PAPRIUM One-Hit Kill: %s", g64x_paprium_cheat_get() ? "ON":"OFF");'
        )

    # 3) Wii menu contains 14 entries after adding item 13.
    if not any("m->max_items = 14;" in line for line in lines):
        hits = []
        for i, line in enumerate(lines):
            compact = line.replace(" ", "").replace("\t", "")
            if compact == "m->max_items=13;":
                hits.append(i)
        if len(hits) != 1:
            die(f"PAPRIUM one-hit menu size: expected exactly 1 Wii max_items=13 line, found {len(hits)}")
        i = hits[0]
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines[i] = indent + "m->max_items = 14;"

    # 4) Toggle handler: insert inside HW_RVL after case 12 and before #endif.
    if not any("case 13: /*** PAPRIUM one-hit kill ***/" in line for line in lines):
        case12 = [
            i for i, line in enumerate(lines)
            if "case 12:" in line and "Wii remote pointer calibration" in line
        ]
        if len(case12) != 1:
            die(f"PAPRIUM one-hit menu toggle: expected exactly 1 Wii calibration case, found {len(case12)}")

        start_i = case12[0]
        case_minus1 = None
        endif_i = None
        for i in range(start_i + 1, min(len(lines), start_i + 120)):
            stripped = lines[i].strip()
            if stripped.startswith("case -1:"):
                case_minus1 = i
                break
            if stripped == "#endif":
                endif_i = i

        if case_minus1 is None or endif_i is None or endif_i >= case_minus1:
            die("PAPRIUM one-hit menu toggle: HW_RVL #endif before case -1 not found")

        indent = lines[start_i][:len(lines[start_i]) - len(lines[start_i].lstrip())]
        block = [
            indent + "case 13: /*** PAPRIUM one-hit kill ***/",
            indent + "g64x_paprium_cheat_toggle();",
            indent + 'sprintf (items[13].text, "PAPRIUM One-Hit Kill: %s", g64x_paprium_cheat_get() ? "ON":"OFF");',
            indent + "break;",
        ]
        lines[endif_i:endif_i] = block

    text = eol.join(lines)
    write_preserving_eol(path, text, eol)


def patch_file_slot_card_workarea(path):
    text, eol = read_preserving_eol(path)

    marker = "#define CARD_WORKAREA CARD_WORKAREA_SIZE"
    if marker in text:
        return

    anchor = '#include "saveicon.h"'
    if text.count(anchor) != 1:
        die(f"CARD workarea compatibility: expected exactly 1 saveicon include, found {text.count(anchor)}")

    block = eol.join([
        anchor,
        "",
        "/* PAPwiiUM: current libogc renamed CARD_WORKAREA to",
        " * CARD_WORKAREA_SIZE. Keep the old source identifier as a",
        " * compatibility alias, matching current Genesis Plus GX upstream.",
        " */",
        "#ifndef CARD_WORKAREA",
        "#define CARD_WORKAREA CARD_WORKAREA_SIZE",
        "#endif",
    ])

    text = text.replace(anchor, block, 1)
    write_preserving_eol(path, text, eol)



def patch_wii_cart_size(path):
    text, eol = read_preserving_eol(path)

    marker = "uint8 cart_size = 6;"
    if marker in text:
        return

    # PUNCHiUM added the Sega CD backup-cart size as a frontend-owned global.
    # libretro.c defines it and defaults to the core-option value "4meg",
    # which maps to numeric ID 6. The standalone Wii frontend compiles the
    # same cd_cart.c but has no corresponding definition, causing the final
    # link to fail. Mirror the fork's own default in the Wii frontend.
    anchor = "u32 ConfigRequested = 1;"
    if text.count(anchor) != 1:
        die(f"Wii cart_size definition: expected exactly 1 ConfigRequested anchor, found {text.count(anchor)}")

    block = eol.join([
        anchor,
        "",
        "/* PAPwiiUM: PUNCHiUM cd_cart.c expects a frontend-owned Sega CD",
        " * backup RAM cartridge size. Match the fork's libretro default:",
        " * 4Mbit backup cart -> ID 6.",
        " */",
        "uint8 cart_size = 6;",
    ])
    text = text.replace(anchor, block, 1)
    write_preserving_eol(path, text, eol)




def patch_wii_punchium_shutdown(path):
    text, eol = read_preserving_eol(path)

    extern_marker = "extern void g64x_punchium_frontend_shutdown(void);"
    if extern_marker not in text:
        anchor = "uint8 cart_size = 6;"
        if text.count(anchor) != 1:
            die(f"Wii PUNCHiUM shutdown extern: expected exactly 1 cart_size anchor, found {text.count(anchor)}")
        text = text.replace(
            anchor,
            anchor + eol + eol
            + "/* PAPwiiUM: PUNCHiUM streaming resource teardown. */" + eol
            + extern_marker,
            1,
        )

    # run_emulation() is above shutdown() in main.c, so direct POWER teardown
    # needs an explicit prototype on modern compilers.
    if "void shutdown(void);" not in text:
        if extern_marker not in text:
            die("Wii direct POWER shutdown: cleanup extern missing")
        text = text.replace(
            extern_marker,
            extern_marker + eol + "void shutdown(void);",
            1,
        )

    # Once POWER is requested, never submit another A/V synchronization pass.
    if "while (sync && !Shutdown)" not in text:
        count = text.count("while (sync)")
        if count != 3:
            die(f"Wii POWER sync guard: expected exactly 3 while(sync) loops, found {count}")
        text = text.replace("while (sync)", "while (sync && !Shutdown)")

    # PAPRIUM must see its real Backup RAM even if generic SRAM autoload was
    # disabled in the frontend settings. Temporarily force only the SRAM bit.
    force_load_marker = "/* PAPwiiUM: force Backup RAM autoload */"
    if force_load_marker not in text:
        anchor = eol.join([
            "  /* Auto-Load Backup RAM */",
            "  slot_autoload(0,config.s_device);",
        ])
        if text.count(anchor) != 1:
            die(f"PAPRIUM SRAM autoload: expected exactly 1 Backup RAM autoload anchor, found {text.count(anchor)}")
        block = eol.join([
            "  /* Auto-Load Backup RAM */",
            "  if (cart.special & HW_PUNCHIUM)",
            "  {",
            "    /* PAPwiiUM: force Backup RAM autoload */",
            "    int g64x_s_auto = config.s_auto;",
            "    config.s_auto |= 1;",
            "    slot_autoload(0,config.s_device);",
            "    config.s_auto = g64x_s_auto;",
            "  }",
            "  else",
            "  {",
            "    slot_autoload(0,config.s_device);",
            "  }",
        ])
        text = text.replace(anchor, block, 1)

    # A real POWER request uses PowerOff_cb(), which clears reload. Do not go
    # through gx_video_Stop()/mainmenu() at all: that path snapshots/redraws the
    # last game frame and is where PAPRIUM's red/corrupt shutdown image leaks.
    direct_marker = "/* PAPwiiUM POWER: direct hard-black shutdown */"
    if direct_marker not in text:
        anchor = eol.join([
            "    /* stop video & audio */",
            "    gx_audio_Stop();",
            "    gx_video_Stop();",
        ])
        if text.count(anchor) != 1:
            die(f"Wii direct POWER shutdown: expected exactly 1 stop-video/audio anchor, found {text.count(anchor)}")

        block = eol.join([
            "    /* stop video & audio */",
            "#ifdef HW_RVL",
            "    if (Shutdown && !reload)",
            "    {",
            "      /* PAPwiiUM POWER: direct hard-black shutdown */",
            "      VIDEO_SetBlack(TRUE);",
            "      VIDEO_Flush();",
            "      VIDEO_WaitVSync();",
            "",
            "      /* No menu transition and no final game screenshot. */",
            "      gx_audio_Stop();",
            "",
            "      /* Close PAPRIUM WAV/cache while FAT is still mounted. */",
            "      g64x_punchium_frontend_shutdown();",
            "",
            "      /* Saves SRAM, then tears down A/V and FAT devices. */",
            "      shutdown();",
            "",
            "      SYS_ResetSystem(SYS_POWEROFF, 0, 0);",
            "",
            "      /* Should never return. Keep the display path dead if it does. */",
            "      while (1) {}",
            "    }",
            "#endif",
            "",
            "    gx_audio_Stop();",
            "    gx_video_Stop();",
        ])
        text = text.replace(anchor, block, 1)

    # Backup RAM / PAPRIUM EEPROM is slot 0. Force its autosave bit just for
    # this operation so a user's generic setting cannot suppress the game save.
    save_marker = "/* PAPwiiUM: force-save Backup RAM / PAPRIUM EEPROM */"
    if save_marker not in text:
        anchor = eol.join([
            "  /* auto-save State file */",
            "  slot_autosave(config.s_default,config.s_device);",
        ])
        if text.count(anchor) != 1:
            die(f"PAPRIUM SRAM shutdown save: expected exactly 1 state-autosave anchor, found {text.count(anchor)}")

        block = eol.join([
            "  /* PAPwiiUM: force-save Backup RAM / PAPRIUM EEPROM */",
            "  if (cart.special & HW_PUNCHIUM)",
            "  {",
            "    int g64x_s_auto = config.s_auto;",
            "    config.s_auto |= 1;",
            "    slot_autosave(0,config.s_device);",
            "    config.s_auto = g64x_s_auto;",
            "  }",
            "  else",
            "  {",
            "    slot_autosave(0,config.s_device);",
            "  }",
            "",
            "  /* auto-save State file */",
            "  slot_autosave(config.s_default,config.s_device);",
        ])
        text = text.replace(anchor, block, 1)

    write_preserving_eol(path, text, eol)


def validate(root):
    f = (root / "gx/fileio/file_load.c").read_bytes().decode("latin-1")
    p = (root / "core/cart_hw/punchium.h").read_bytes().decode("latin-1")
    v = (root / "core/vdp_ctrl.c").read_bytes().decode("latin-1")
    sh = (root / "core/shared.h").read_bytes().decode("latin-1")
    compat_path = root / "core/g64x_libogc_spr_compat.h"
    compat = compat_path.read_text(encoding="latin-1") if compat_path.is_file() else ""
    cdrom = (root / "core/cd_hw/libchdr/src/cdrom.h").read_bytes().decode("latin-1")
    coretypes = (root / "core/cd_hw/libchdr/src/coretypes.h").read_bytes().decode("latin-1")
    makefile = (root / "Makefile.wii").read_bytes().decode("latin-1")
    menu = (root / "gx/gui/menu.c").read_bytes().decode("latin-1")
    file_slot = (root / "gx/fileio/file_slot.c").read_bytes().decode("latin-1")
    main_c = (root / "gx/main.c").read_bytes().decode("latin-1")
    config_c = (root / "gx/config.c").read_bytes().decode("latin-1")
    config_h = (root / "gx/config.h").read_bytes().decode("latin-1")
    md_cart = (root / "core/cart_hw/md_cart.c").read_bytes().decode("latin-1")

    checks = {
        "build marker": MARKER in f,
        "ROM directory bridge": "strncpy(g_rom_dir, filepath" in f,
        "trailing slash removal": "g_rom_dir[--len] = 0;" in f,
        "WAV fixed": "punchium_audio_track_format = 1" in f,
        "tile cache enabled": "punchium_tile_cache = true" in f,
        "cheat default disabled": "punchium_cheat_saitama = false" in f,
        "cheat persistent config": "paprium_g64x.cfg" in config_c and "g64x_paprium_cheat_toggle" in config_c,
        "cheat config API": "g64x_paprium_cheat_get" in config_h and "g64x_paprium_cheat_load" in config_h,
        "cheat refreshed before ROM init": "g64x_paprium_cheat_load();" in f,
        "PAPRIUM menu option": "PAPRIUM One-Hit Kill: OFF" in menu and "case 13: /*** PAPRIUM one-hit kill ***/" in menu and "m->max_items = 14;" in menu,
        "single audio attempt": "i < 1 && !track_loaded" in p,
        "WAV stream chunk": "#define PUNCHIUM_WAV_STREAM_FRAMES 32768" in p,
        "WAV stream bounded allocation": "malloc(PUNCHIUM_WAV_STREAM_FRAMES * punchium_track.channels" in p,
        "WAV full-track allocation removed": "malloc(punchium_track.total_samples * punchium_track.channels" not in p,
        "WAV stream refill helper": "static int punchium_wav_stream_refill" in p and "drwav_seek_to_pcm_frame" in p,
        "WAV single-window player": "uint32_t available_samples = punchium_track.wav_streaming" in p,
        "WAV no audio LWP": "LWP_CreateThread" not in p and "punchium_wav_prefetch_worker" not in p,
        "PUNCHiUM shutdown cleanup": "void g64x_punchium_frontend_shutdown(void)" in p and "free_tile_cache();" in p,
        "Wii POWER stream teardown": "g64x_punchium_frontend_shutdown();" in main_c,
        "Wii POWER sync guard": main_c.count("while (sync && !Shutdown)") == 3,
        "Wii POWER direct hard-black": "/* PAPwiiUM POWER: direct hard-black shutdown */" in main_c and "VIDEO_SetBlack(TRUE);" in main_c,
        "Wii POWER direct poweroff": "if (Shutdown && !reload)" in main_c and "SYS_ResetSystem(SYS_POWEROFF, 0, 0);" in main_c,
        "Backup RAM forced autosave": "/* PAPwiiUM: force-save Backup RAM / PAPRIUM EEPROM */" in main_c and "slot_autosave(0,config.s_device);" in main_c,
        "PAPRIUM forced Backup RAM autoload": "/* PAPwiiUM: force Backup RAM autoload */" in main_c and "config.s_auto |= 1;" in main_c,
        "Wii VDP logger shim": "#define log_cb(...) ((void)0)" in v,
        "libretro logger type removed": "extern retro_log_printf_t log_cb;" not in v,
        "shared SPR compat include": '#include "g64x_libogc_spr_compat.h"' in sh,
        "generated SPR compat header": "PAPwiiUM generated libogc SPR compatibility header" in compat,
        "DEC neutralized globally": "#undef DEC" in compat,
        "TBL neutralized globally": "#undef TBL" in compat,
        "libchdr msf_to_lba static inline": "static inline uint32_t msf_to_lba(uint32_t msf)" in cdrom,
        "libchdr lba_to_msf static inline": "static inline uint32_t lba_to_msf(uint32_t lba)" in cdrom,
        "libchdr lba_to_msf_alt static inline": "static inline uint32_t lba_to_msf_alt(int lba)" in cdrom,
        "libchdr INLINE removed": "INLINE uint32_t" not in cdrom,
        "libchdr core_fsize adapter": "static inline size_t core_fsize(core_file *f)" in coretypes,
        "libchdr core_fsize preserves position": "core_fseek(f, current, SEEK_SET);" in coretypes,
        "Wii libchdr deps include root": "$(CHDLIBDIR)/deps $(CHDLIBDIR)/deps/libFLAC/include" in makefile,
        "Wii recursive make jobserver": "+@$(MAKE) --no-print-directory -C $(BUILD)" in makefile,
        "Wii public LWP header": "#include <ogc/lwp.h>" in menu,
        "Wii private LWP header removed": "#include <ogc/lwp_threads.h>" not in menu,
        "Wii LWP exit compatibility": (
            "#define __lwp_thread_stopmultitasking(entry) ((entry)())" in menu
            or "reload();" in menu
        ),
        "Wii CARD workarea compatibility": "#define CARD_WORKAREA CARD_WORKAREA_SIZE" in file_slot,
        "Wii standalone cart_size definition": "uint8 cart_size = 6;" in main_c,
        "PUNCHiUM endian selector": "#define PUNCHIUM_BYTE_XOR 1" in p and "#define PUNCHIUM_BYTE_XOR 0" in p,
        "PUNCHiUM raw-byte selector": "#define PUNCHIUM_RAW_BYTE_XOR 0" in p and "#define PUNCHIUM_RAW_BYTE_XOR 1" in p,
        "PUNCHiUM raw-byte accessor": "#define PUNCHIUM_RAW_U8(base, offset)" in p,
        "PUNCHiUM 32-bit word-pair accessor": "#define PUNCHIUM_READ_U32_WORDPAIR" in p,
        "PUNCHiUM raw hardcoded XOR removed": "^1" not in p,
        "PUNCHiUM portable byte XOR used": "^PUNCHIUM_BYTE_XOR" in p,
        "PUNCHiUM raw byte sites converted": p.count("PUNCHIUM_RAW_U8(") >= 20,
        "PUNCHiUM sprite word-pair sites converted": p.count("PUNCHIUM_READ_U32_WORDPAIR(") >= 3,
        "PUNCHiUM cache hash reset fixed": "memset(tile_cache.hash_table, MAX_TILE_CACHE_ENTRIES" not in p and "tile_cache.hash_table[i] = MAX_TILE_CACHE_ENTRIES;" in p,
        "PUNCHiUM specific product branch retained": 'else if (strstr(rominfo.product,"T-574120-00"))' in md_cart,
        "PUNCHiUM init retained": "punchium_init();" in md_cart,
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        die("post-patch validation failed: " + ", ".join(failed))

    if "i < 5 && !track_loaded" in p:
        die("old unsafe audio loop still present")


def patch_tree(root):
    root = root.resolve()
    head = git_head(root)
    if head and head != PIN:
        die(f"wrong source revision: expected {PIN}, got {head}")

    file_load = root / "gx/fileio/file_load.c"
    config_c = root / "gx/config.c"
    config_h = root / "gx/config.h"
    punchium = root / "core/cart_hw/punchium.h"
    vdp_ctrl = root / "core/vdp_ctrl.c"
    shared = root / "core/shared.h"
    cdrom = root / "core/cd_hw/libchdr/src/cdrom.h"
    coretypes = root / "core/cd_hw/libchdr/src/coretypes.h"
    makefile = root / "Makefile.wii"
    menu = root / "gx/gui/menu.c"
    file_slot = root / "gx/fileio/file_slot.c"
    main_c = root / "gx/main.c"
    md_cart = root / "core/cart_hw/md_cart.c"
    for p in (file_load, config_c, config_h, punchium, vdp_ctrl, shared, cdrom, coretypes, makefile, menu, file_slot, main_c, md_cart):
        if not p.is_file():
            die(f"missing source file: {p}")

    patch_file_load(file_load)
    patch_config_source(config_c)
    patch_config_header(config_h)
    patch_punchium(punchium)
    patch_vdp_ctrl(vdp_ctrl)
    patch_shared(shared)
    patch_cdrom_header(cdrom)
    patch_coretypes(coretypes)
    patch_makefile_wii(makefile)
    patch_menu_lwp(menu)
    patch_menu_paprium_option(menu)
    patch_file_slot_card_workarea(file_slot)
    patch_wii_cart_size(main_c)
    patch_wii_punchium_shutdown(main_c)
    validate(root)

    print("PAPwiiUM patch: OK")
    print(f"Source revision: {PIN}")
    print("PUNCHiUM tile cache: ON")
    print("PUNCHiUM audio: WAV window streaming (32768 frames)")
    print("Wii WAV prefetch: disabled (safe single-thread path)")
    print("MP3/OGG fallback: OFF")
    print("PAPRIUM one-hit kill: standalone menu option, persistent, default OFF")
    print("Wii VDP libretro logger: neutralized")
    print("Wii libogc SPR macro namespace: neutralized across core")
    print("libchdr cdrom.h INLINE dependency: removed")
    print("libchdr core_fsize cdStream adapter: added")
    print("libchdr dr_libs include root: enabled")
    print("Wii recursive make/jobserver: fixed")
    print("Wii LWP private header: replaced by public ogc/lwp.h")
    print("Wii LWP private exit handoff: compatibility shim enabled")
    print("Wii CARD workarea API rename: compatibility shim added")
    print("Wii standalone Sega CD cart_size: defined (4Mbit default)")
    print("PUNCHiUM PowerPC logical + raw byte addressing: fixed")
    print("PUNCHiUM packed sprite/SFX word-pair reads: fixed")
    print("PUNCHiUM 1 MB tile-cache reset sentinel: fixed")
    print("Wii POWER shutdown: live PUNCHiUM WAV/cache teardown before FAT unmount")
    print("Wii POWER shutdown: final AV sync suppressed after Shutdown")
    print("Wii POWER shutdown: direct hard-black path, mainmenu bypassed")
    print("Backup RAM/PAPRIUM EEPROM: forced autosave to slot 0 (.srm)")
    print("PAPRIUM Backup RAM: forced autoload on ROM start")
    print("Legacy 8-bit source bytes: preserved")
    print("Original source line endings: preserved")


def make_mock(root, eol):
    (root / "gx/fileio").mkdir(parents=True)
    (root / "core/cart_hw").mkdir(parents=True)
    (root / "core/cd_hw/libchdr/src").mkdir(parents=True)
    (root / "gx/gui").mkdir(parents=True)
    (root / "gx/fileio").mkdir(parents=True, exist_ok=True)

    fl = eol.join([
        '#include "shared.h"',
        '#include "file_load.h"',
        '',
        'char rom_filename[256];',
        '',
        'int LoadFile(int selection)',
        '{',
        '  int size;',
        '  char *filepath = fileDir;',
        '  if (!size)',
        '  {',
        '    /* load game file */',
        '    size = load_rom(filename);',
        '  }',
        '}',
        '',
        '/* unrelated upstream whitespace stays untouched */  ',
        '',
    ])
    ph = eol.join([
        '#pragma once',
        'extern char g_rom_dir[256];',
        'extern uint8_t punchium_audio_track_format;',
        'extern bool punchium_cheat_saitama;',
        'extern bool punchium_tile_cache;',
        '',
        '#define TILE_CACHE_ENABLE 1',
        '#define TILE_CACHE_SIZE 1024',
        '#define MAX_TILE_SIZE 512',
        '#define DEBUG_SPRITE 0',
        'typedef unsigned char uint8;',
        'typedef unsigned char uint8_t;',
        'typedef unsigned short uint16;',
        'typedef unsigned short uint16_t;',
        'typedef unsigned int uint32;',
        'typedef unsigned int uint32_t;',
        '#define TILE_CACHE_HASH_SIZE 7',
        '#define MAX_TILE_CACHE_ENTRIES 8',
        'struct { uint8 music_ram[64]; uint8 ram[0x2000]; uint8 exps_ram[128]; } punchium_s;',
        'static uint8 punchium_obj_ram[256];',
        'static uint8 test_rom[8];',
        'static uint8 test_ram[8];',
        'static struct { uint32_t *hash_table; } tile_cache;',
        'static int test_endian(int src, int address) {',
        '  int type = test_rom[(src++)^1];',
        '  test_ram[address^1] = type;',
        '  return test_ram[(address++)^1];',
        '}',
        'static int raw_test(int framePtr, int spritePtr, int obj, int animPtrIn, int anim, int count, int flags) {',
        '  int sections = punchium_s.music_ram[0x09];',
        '  int bars = (punchium_s.music_ram[0x0B] ? punchium_s.music_ram[0x0B] : 0x100) + 8;',
        '  int cmds = punchium_s.music_ram[0x0D];',
        '  int animPtr = *(uint32*) (punchium_obj_ram + (obj+1)*4);',
        '  int p = animPtr;',
        '  framePtr = *(uint32*) (punchium_obj_ram + animPtr + anim*4);',
        '  p += punchium_obj_ram[framePtr + 0] + punchium_obj_ram[framePtr + 1] + punchium_obj_ram[framePtr + 2];',
        '  p += punchium_obj_ram[framePtr + 3];',
        '  p += punchium_obj_ram[spritePtr + 3] + punchium_obj_ram[spritePtr + 1] + punchium_obj_ram[spritePtr + 0];',
        '  punchium_s.exps_ram[2] = 14;',
        '  punchium_s.ram[0xB02 + (count-1)*8] = 0;',
        '  punchium_s.exps_ram[2 + (count-1)*8] = 0;',
        '  punchium_s.exps_ram[2 + (count-81)*8] = 0;',
        '  punchium_s.ram[0x1801] = flags & 0x01;',
        '  punchium_s.ram[0x1800]  = (flags & 0x01) ? 0x80 : 0x00;',
        '  punchium_s.ram[0x1800] += (flags & 0x02) ? 0x40 : 0x00;',
        '  return p + sections + bars + cmds;',
        '}',
        'static void sfx_test(uint8_t *sfx, int data, struct { int num; int size; } *voice) {',
        '  int size, type;',
        '  size = (*(uint8_t *)(sfx + data*8 + 4) << 16) | (*(uint16_t *)(sfx + data*8 + 6));',
        '  type = *(uint8_t *)(sfx + data*8 + 5);',
        '  voice->size = (*(uint8 *)(sfx + voice->num*8 + 4) << 16) | (*(uint16 *)(sfx + voice->num*8 + 6));',
        '  (void)size; (void)type;',
        '}',
        'struct punchium_track_t {',
        '	int sync_tick;				// Коэффициент синхронизации',
        '	int8_t track_last;			  // Текущий трек',
        '	int16_t *buffer;				// PCM-данные (L/R)',
        '	uint16_t sample_rate;		   // 48000, 44100 и т.д.',
        '	uint16_t channels;			  // 1 (моно) или 2 (стерео)',
        '	uint32_t total_samples;		 // Всего сэмплов (на канал)',
        '	mp3dec_t mp3;',
        '	mp3dec_file_info_t mp3_info;',
        '	drwav wav;',
        '	stb_vorbis *vorbis;',
        '	my_stb_vorbis_info vorbis_info;',
        '	int samples_read;			   // Прочитано сэмплов',
        '} punchium_track;',
        'static void music_var_init (){',
        '	punchium_s.music_track = 0;',
        '	punchium_s.music_tick = 0;',
        '	punchium_s.music_pos = 0;',
        '	punchium_track.sync_tick = 0;',
        '	punchium_track.track_last = 0;',
        '	punchium_track.channels = 2;',
        '	// punchium_track.file_type = 0;',
        '	punchium_track.sample_rate = 0;',
        '	punchium_track.total_samples = 0;',
        '	punchium_track.buffer = NULL;',
        '	mp3dec_init(&punchium_track.mp3);',
        '}',
        'static void punchium_load_music_file(int track, int reload) {',
        '	punchium_s.music_track = track;',
        '	bool track_loaded = false;',
        '	uint8_t formats_to_try[] = {0, 1, 2, 3};',
        '	formats_to_try[0] = punchium_audio_track_format;',
        '	if (punchium_track.buffer) {',
        '		free(punchium_track.buffer);',
        '		punchium_track.buffer = NULL;',
        '	}',
        '	memset(&punchium_track.mp3, 0, sizeof(mp3dec_t));',
        '	memset(&punchium_track.mp3_info, 0, sizeof(mp3dec_file_info_t));',
        '	drwav_uninit(&punchium_track.wav);',
        '	memset(&punchium_track.wav, 0, sizeof(drwav));',
        '	for (int i = 0; i < 5 && !track_loaded; i++) {',
        '		uint8_t fmt = formats_to_try[i];',
        '		if (fmt == 0) return;',
        '		switch (fmt) {',
        '			case 1:',
        '				punchium_track.channels = punchium_track.wav.channels;',
        '				punchium_track.total_samples = punchium_track.wav.totalPCMFrameCount;',
        '				punchium_track.sample_rate = punchium_track.wav.fmt.sampleRate;',
        '				punchium_track.buffer = (int16_t*)malloc(punchium_track.total_samples * punchium_track.channels * sizeof(int16_t));',
        '',
        '				if (!punchium_track.buffer) {',
        '					drwav_uninit(&punchium_track.wav);',
        '					continue;',
        '				}',
        '',
        '				drwav_read_pcm_frames_s16(&punchium_track.wav, punchium_track.wav.totalPCMFrameCount, punchium_track.buffer);',
        '				track_loaded = true;',
        '				break;',
        '		}',
        '	}',
        '}',
        '// Генерирует аудио из декодированного файла с учетом громкости/панорамирования.',
        'static void punchium_music_player(int *out_l, int *out_r) {',
        '	if (punchium_s.music_track == 0 || punchium_track.buffer == NULL)',
        '		return;',
        '',
        '	if (punchium_s.music_pos >= punchium_track.total_samples)',
        '		punchium_s.music_pos = 0;',
        '',
        '	int l = 0, r = 0;',
        '	int sample_pos = punchium_s.music_pos * punchium_track.channels;',
        '	if (sample_pos < punchium_track.total_samples * punchium_track.channels) {',
        '		l = punchium_track.buffer[sample_pos];',
        '		r = (punchium_track.channels > 1) ? punchium_track.buffer[sample_pos + 1] : l;',
        '	}',
        '	punchium_s.music_tick += punchium_track.sync_tick;',
        '}',
        'static void reset_tile_cache(void) {',
        '  memset(tile_cache.hash_table, MAX_TILE_CACHE_ENTRIES, sizeof(uint32_t) * TILE_CACHE_HASH_SIZE);',
        '}',
        '',
        "static void punchium_init()",
    ])
    (root / "gx/fileio/file_load.c").write_bytes(fl.encode())
    config_h = eol.join([
        '#ifndef _CONFIG_H_',
        '#define _CONFIG_H_',
        'typedef struct { int dummy; } t_config;',
        'extern t_config config;',
        'extern void config_save(void);',
        'extern void config_default(void);',
        '#endif',
        '',
    ])
    (root / "gx/config.h").write_bytes(config_h.encode())
    config_c = eol.join([
        '#include "shared.h"',
        '#include "gui.h"',
        '#include "file_load.h"',
        't_config config;',
        'static int config_load(void) { char fname[MAXPATHLEN]; sprintf(fname, "%s/config.ini", DEFAULT_PATH); return 0; }',
        'void config_save(void) { }',
        'void config_default(void) { }',
        '',
    ])
    (root / "gx/config.c").write_bytes(config_c.encode())
    (root / "core/cart_hw/punchium.h").write_bytes(ph.encode())
    vdp = eol.join([
        '#include "shared.h"',
        '//#define DEBUG_VDP',
        '',
        'extern retro_log_printf_t log_cb;',
        'static char error_str[512];',
        '',
        'static void test_vdp(void) {',
        '  log_cb(RETRO_LOG_ERROR, error_str);',
        '}',
        '',
    ])
    (root / "core/vdp_ctrl.c").write_bytes(vdp.encode())
    shared = eol.join([
        '#ifndef _SHARED_H_',
        '#define _SHARED_H_',
        '',
        '#include <stdio.h>',
        '#include "types.h"',
        '#include "osd.h"',
        '#include "macros.h"',
        '',
        '#endif',
        '',
    ])
    (root / "core/shared.h").write_bytes(shared.encode())
    cdrom = eol.join([
        '#pragma once',
        '#ifndef __CDROM_H__',
        '#define __CDROM_H__',
        '#include <stdint.h>',
        '',
        'INLINE uint32_t msf_to_lba(uint32_t msf)',
        '{',
        '  return msf;',
        '}',
        'INLINE uint32_t lba_to_msf(uint32_t lba)',
        '{',
        '  return lba;',
        '}',
        'INLINE uint32_t lba_to_msf_alt(int lba)',
        '{',
        '  return (uint32_t)lba;',
        '}',
        '#endif',
        '',
    ])
    (root / "core/cd_hw/libchdr/src/cdrom.h").write_bytes(cdrom.encode())
    coretypes = eol.join([
        '#ifndef __CORETYPES_H__',
        '#define __CORETYPES_H__',
        '#include <stdint.h>',
        '#include <stdio.h>',
        'typedef uint64_t UINT64;',
        '#define core_file                 cdStream',
        '#define core_fopen                cdStreamOpen',
        '#define core_fseek                cdStreamSeek',
        '#define core_fread(fc, buff, len) cdStreamRead(buff, 1, len, fc)',
        '#define core_fclose               cdStreamClose',
        '#define core_ftell                cdStreamTell',
        '#endif',
        '',
    ])
    (root / "core/cd_hw/libchdr/src/coretypes.h").write_bytes(coretypes.encode())
    makefile = eol.join([
        'CHDLIBDIR = core/cd_hw/libchdr',
        'INCLUDES := core \\\\',
        '            $(CHDLIBDIR)/src $(CHDLIBDIR)/deps/libFLAC/include $(CHDLIBDIR)/deps/lzma \\\\',
        '            gx',
        '',
        '$(BUILD):',
        '\\t@[ -d $@ ] || mkdir -p $@',
        '\\t@make --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile.wii',
        '',
    ])
    (root / "Makefile.wii").write_bytes(makefile.encode())
    menu = eol.join([
        '#include "shared.h"',
        '#include "file_load.h"',
        '#include <ogc/lwp_threads.h>',
        '#include <ogc/lwp_watchdog.h>',
        '',
        'static gui_item items_prefs[] =',
        '{',
        '{NULL,NULL,"Auto ROM Load: OFF", "x", 56,132,276,48},',
        '#ifdef HW_RVL',
        '{NULL,NULL,"Wiimote Timeout: OFF","Enable/Disable Wii remote automatic shutdown", 56,132,276,48},',
        '{NULL,NULL,"Wiimote Calibration: AUTO","Calibrate Wii remote pointer", 56,132,276,48},',
        '#endif',
        '};',
        'static void prefmenu(void)',
        '{',
        'gui_menu *m = 0; gui_item *items = 0; int ret = 0;',
        '#ifdef HW_RVL',
        'sprintf (items[11].text, "Wiimote Timeout: %s", config.autosleep ? "5 MIN":"30 MIN");',
        'sprintf (items[12].text, "Wiimote Calibration: %s", ((config.calx * config.caly) != 0) ? "MANUAL":"AUTO");',
        'sprintf (items[12].comment, "%s", ((config.calx * config.caly) != 0) ? "Reset default Wii remote pointer calibration":"Calibrate Wii remote pointer");',
        '',
        'm->max_items = 13;',
        '#else',
        'm->max_items = 11;',
        '#endif',
        'switch (ret)',
        '{',
        '#ifdef HW_RVL',
        'case 12: /*** Wii remote pointer calibration ***/',
        'if ((config.calx * config.caly) == 0)',
        '{',
        'config.calx = 1;',
        '}',
        'else',
        '{',
        'sprintf (items[12].text, "Wiimote Calibration: AUTO");',
        'sprintf (items[12].comment, "Calibrate Wii remote pointer");',
        'config.calx = config.caly = 0;',
        '}',
        '',
        'break;',
        '#endif',
        '',
        'case -1:',
        'break;',
        '}',
        '}',
        '',
        '/* Exit callback */',
        'void (*reload)(void);',
        '',
        'static void quit_menu(void)',
        '{',
        '  __lwp_thread_stopmultitasking (  reload  );',
        '}',
        '',
    ])
    # Write a real non-UTF8 legacy byte (0xF1) into menu.c to prove the
    # patcher does not require UTF-8 and preserves arbitrary 8-bit source bytes.
    menu_bytes = menu.encode("latin-1") + b"/* legacy byte: \xf1 */" + eol.encode("ascii")
    (root / "gx/gui/menu.c").write_bytes(menu_bytes)

    file_slot = eol.join([
        '#include "shared.h"',
        '#include "file_slot.h"',
        '#include "file_load.h"',
        '#include "gui.h"',
        '#include "filesel.h"',
        '#include "saveicon.h"',
        '',
        'static u8 SysArea[CARD_WORKAREA] ATTRIBUTE_ALIGN (32);',
        'void test(void) { memset(&SysArea, 0, CARD_WORKAREA); }',
        '',
    ])
    (root / "gx/fileio/file_slot.c").write_bytes(file_slot.encode())
    main_c = eol.join([
        "u32 Shutdown = 0;",
        "u32 ConfigRequested = 1;",
        "void (*reload)(void) = 0;",
        "static void run_emulation(void)",
        "{",
        "  u32 sync;",
        "  while (1)",
        "  {",
        "    while (!ConfigRequested) {",
        "      sync = 1;",
        "      while (sync) sync = 0;",
        "    }",
        "    while (!ConfigRequested) {",
        "      sync = 1;",
        "      while (sync) sync = 0;",
        "    }",
        "    while (!ConfigRequested) {",
        "      sync = 1;",
        "      while (sync) sync = 0;",
        "    }",
        "    /* stop video & audio */",
        "    gx_audio_Stop();",
        "    gx_video_Stop();",
        "    ConfigRequested = 0;",
        "    mainmenu();",
        "  }",
        "}",
        "",
        "void reloadrom(void)",
        "{",
        "  /* Auto-Load Backup RAM */",
        "  slot_autoload(0,config.s_device);",
        "  /* Auto-Load State */",
        "  slot_autoload(config.s_default,config.s_device);",
        "}",
        "",
        "void shutdown(void)",
        "{",
        "  config_save();",
        "  /* auto-save State file */",
        "  slot_autosave(config.s_default,config.s_device);",
        "  audio_shutdown();",
        "}",
        "",
    ])
    (root / "gx/main.c").write_bytes(main_c.encode("latin-1"))
    md_cart = eol.join([
        'static void md_cart_init(void)',
        '{',
        '  if (0) {}',
        '  else if (strstr(rominfo.product,"T-5740"))',
        '  {',
        '    cart.hw.bankshift = 1;',
        '    eeprom_spi_init();',
        '  }',
        '  else if (strstr(rominfo.product,"T-574120-00"))',
        '  {',
        '    cart.special |= HW_PUNCHIUM;',
        '    punchium_init();',
        '    eeprom_spi_init();',
        '  }',
        '}',
        '',
    ])
    (root / "core/cart_hw/md_cart.c").write_bytes(md_cart.encode("latin-1"))
    (root / "core/g64x_libogc_spr_compat.h").write_text(
        '#ifndef G64X_LIBOGC_SPR_COMPAT_H\n'
        '#define G64X_LIBOGC_SPR_COMPAT_H\n'
        '/* PAPwiiUM generated libogc SPR compatibility header */\n'
        '#ifdef DEC\n#undef DEC\n#endif\n'
        '#ifdef TBL\n#undef TBL\n#endif\n'
        '#endif\n',
        encoding='utf-8',
    )


def self_test():
    import tempfile
    for label, eol in (("LF", "\n"), ("CRLF", "\r\n")):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_mock(root, eol)
            before = (root / "gx/fileio/file_load.c").read_bytes()
            patch_tree(root)
            after = (root / "gx/fileio/file_load.c").read_bytes()

            config_c_after = (root / "gx/config.c").read_bytes()
            config_h_after = (root / "gx/config.h").read_bytes()
            if b"g64x_paprium_cheat_load();" not in after:
                die("self-test: persistent cheat reload before ROM init missing")
            if b"paprium_g64x.cfg" not in config_c_after or b"g64x_paprium_cheat_toggle" not in config_c_after:
                die("self-test: standalone cheat persistence missing")
            if b"g64x_paprium_cheat_get" not in config_h_after:
                die("self-test: standalone cheat config API missing")

            if eol == "\r\n":
                if b"\n" in after.replace(b"\r\n", b""):
                    die("CRLF self-test: bare LF introduced")
            if b"unrelated upstream whitespace stays untouched */  " not in after:
                die("self-test: unrelated upstream content changed")
            vdp_after = (root / "core/vdp_ctrl.c").read_bytes()
            if b"#define log_cb(...) ((void)0)" not in vdp_after:
                die("self-test: Wii VDP logger shim missing")
            if b"extern retro_log_printf_t log_cb;" in vdp_after:
                die("self-test: libretro logger declaration remained")
            shared_after = (root / "core/shared.h").read_bytes()
            if b'#include "g64x_libogc_spr_compat.h"' not in shared_after:
                die("self-test: shared SPR compatibility include missing")
            if eol == "\r\n" and b"\n" in shared_after.replace(b"\r\n", b""):
                die("CRLF self-test: shared.h bare LF introduced")
            if eol == "\r\n" and b"\n" in vdp_after.replace(b"\r\n", b""):
                die("CRLF self-test: vdp_ctrl bare LF introduced")
            cdrom_after = (root / "core/cd_hw/libchdr/src/cdrom.h").read_bytes()
            if cdrom_after.count(b"static inline uint32_t") != 3:
                die("self-test: expected three static inline libchdr helpers")
            if b"INLINE uint32_t" in cdrom_after:
                die("self-test: libchdr INLINE dependency remained")
            if eol == "\r\n" and b"\n" in cdrom_after.replace(b"\r\n", b""):
                die("CRLF self-test: cdrom.h bare LF introduced")
            coretypes_after = (root / "core/cd_hw/libchdr/src/coretypes.h").read_bytes()
            if b"static inline size_t core_fsize(core_file *f)" not in coretypes_after:
                die("self-test: core_fsize adapter missing")
            if coretypes_after.count(b"core_fsize(core_file *f)") != 1:
                die("self-test: core_fsize adapter duplicated")
            if b"core_fseek(f, current, SEEK_SET);" not in coretypes_after:
                die("self-test: core_fsize does not restore file position")
            if eol == "\r\n" and b"\n" in coretypes_after.replace(b"\r\n", b""):
                die("CRLF self-test: coretypes.h bare LF introduced")
            makefile_after = (root / "Makefile.wii").read_bytes()
            if b"$(CHDLIBDIR)/deps $(CHDLIBDIR)/deps/libFLAC/include" not in makefile_after:
                die("self-test: libchdr deps include root missing")
            if b"+@$(MAKE) --no-print-directory -C $(BUILD)" not in makefile_after:
                die("self-test: recursive make jobserver fix missing")
            if eol == "\r\n" and b"\n" in makefile_after.replace(b"\r\n", b""):
                die("CRLF self-test: Makefile.wii bare LF introduced")
            menu_after = (root / "gx/gui/menu.c").read_bytes()
            if b"PAPRIUM One-Hit Kill: OFF" not in menu_after or b"case 13: /*** PAPRIUM one-hit kill ***/" not in menu_after:
                die("self-test: PAPRIUM one-hit menu option missing")
            if b"#include <ogc/lwp.h>" not in menu_after:
                die("self-test: public LWP include missing")
            if b"#include <ogc/lwp_threads.h>" in menu_after:
                die("self-test: private LWP include remained")
            if b"#define __lwp_thread_stopmultitasking(entry) ((entry)())" not in menu_after:
                die("self-test: LWP exit compatibility shim missing")
            if b"__lwp_thread_stopmultitasking (  reload  );" not in menu_after:
                die("self-test: legacy differently-formatted LWP call was not preserved")
            if b"\xf1" not in menu_after:
                die("self-test: legacy 0xF1 byte was not preserved")
            if eol == "\r\n" and b"\n" in menu_after.replace(b"\r\n", b""):
                die("CRLF self-test: menu.c bare LF introduced")

            file_slot_after = (root / "gx/fileio/file_slot.c").read_bytes()
            if b"#define CARD_WORKAREA CARD_WORKAREA_SIZE" not in file_slot_after:
                die("self-test: CARD workarea compatibility alias missing")
            if file_slot_after.count(b"#define CARD_WORKAREA CARD_WORKAREA_SIZE") != 1:
                die("self-test: CARD workarea compatibility alias duplicated")
            if eol == "\r\n" and b"\n" in file_slot_after.replace(b"\r\n", b""):
                die("CRLF self-test: file_slot.c bare LF introduced")
            main_after = (root / "gx/main.c").read_bytes()
            if b"uint8 cart_size = 6;" not in main_after:
                die("self-test: standalone cart_size definition missing")
            if main_after.count(b"uint8 cart_size = 6;") != 1:
                die("self-test: standalone cart_size definition duplicated")
            if b"extern void g64x_punchium_frontend_shutdown(void);" not in main_after:
                die("self-test: PUNCHiUM shutdown extern missing")
            if main_after.count(b"while (sync && !Shutdown)") != 3:
                die("self-test: POWER sync guards missing")
            if b"g64x_punchium_frontend_shutdown();" not in main_after:
                die("self-test: POWER PUNCHiUM cleanup call missing")
            if b"/* PAPwiiUM POWER: direct hard-black shutdown */" not in main_after:
                die("self-test: direct POWER shutdown branch missing")
            if b"VIDEO_SetBlack(TRUE);" not in main_after:
                die("self-test: POWER VIDEO_SetBlack missing")
            if b"SYS_ResetSystem(SYS_POWEROFF, 0, 0);" not in main_after:
                die("self-test: direct Wii poweroff missing")
            if b"/* PAPwiiUM: force Backup RAM autoload */" not in main_after:
                die("self-test: forced PAPRIUM Backup RAM autoload missing")
            if b"/* PAPwiiUM: force-save Backup RAM / PAPRIUM EEPROM */" not in main_after:
                die("self-test: forced PAPRIUM Backup RAM autosave missing")
            if b"slot_autosave(0,config.s_device);" not in main_after:
                die("self-test: Backup RAM/PAPRIUM EEPROM autosave missing")
            punchium_after = (root / "core/cart_hw/punchium.h").read_bytes()
            if b"void g64x_punchium_frontend_shutdown(void)" not in punchium_after:
                die("self-test: PUNCHiUM frontend shutdown function missing")
            if eol == "\r\n" and b"\n" in main_after.replace(b"\r\n", b""):
                die("CRLF self-test: main.c bare LF introduced")
            punchium_after = (root / "core/cart_hw/punchium.h").read_bytes()
            if b"#define PUNCHIUM_WAV_STREAM_FRAMES 32768" not in punchium_after:
                die("self-test: WAV stream chunk define missing")
            if b"malloc(PUNCHIUM_WAV_STREAM_FRAMES" not in punchium_after:
                die("self-test: bounded WAV stream allocation missing")
            if b"malloc(punchium_track.total_samples * punchium_track.channels" in punchium_after:
                die("self-test: full-track WAV allocation remains")
            if b"punchium_wav_stream_refill" not in punchium_after:
                die("self-test: WAV refill helper missing")
            if b"#define PUNCHIUM_BYTE_XOR 1" not in punchium_after:
                die("self-test: little-endian PUNCHIUM_BYTE_XOR definition missing")
            if b"#define PUNCHIUM_BYTE_XOR 0" not in punchium_after:
                die("self-test: big-endian PUNCHIUM_BYTE_XOR definition missing")
            if b"#define PUNCHIUM_RAW_BYTE_XOR 0" not in punchium_after or b"#define PUNCHIUM_RAW_BYTE_XOR 1" not in punchium_after:
                die("self-test: inverse raw-byte selectors missing")
            if b"#define PUNCHIUM_RAW_U8(base, offset)" not in punchium_after:
                die("self-test: raw-byte lvalue accessor missing")
            if b"#define PUNCHIUM_READ_U32_WORDPAIR" not in punchium_after:
                die("self-test: 32-bit word-pair accessor missing")
            if b"^1" in punchium_after:
                die("self-test: raw hardcoded ^1 byte-lane access remained")
            if punchium_after.count(b"^PUNCHIUM_BYTE_XOR") < 3:
                die("self-test: representative portable logical-byte conversions missing")
            if punchium_after.count(b"PUNCHIUM_RAW_U8(") < 20:
                die("self-test: representative raw byte-lane conversions missing")
            if punchium_after.count(b"PUNCHIUM_READ_U32_WORDPAIR(") < 3:
                die("self-test: representative 32-bit word-pair conversions missing")
            if b"memset(tile_cache.hash_table, MAX_TILE_CACHE_ENTRIES" in punchium_after:
                die("self-test: broken uint32 hash-table memset remained")
            if b"tile_cache.hash_table[i] = MAX_TILE_CACHE_ENTRIES;" not in punchium_after:
                die("self-test: explicit cache hash sentinel reset missing")
            if eol == "\r\n" and b"\n" in punchium_after.replace(b"\r\n", b""):
                die("CRLF self-test: punchium.h bare LF introduced")

            md_cart_after = (root / "core/cart_hw/md_cart.c").read_bytes()
            if b'else if (strstr(rominfo.product,"T-574120-00"))' not in md_cart_after:
                die("self-test: specific PAPRIUM branch missing")
            if b"punchium_init();" not in md_cart_after:
                die("self-test: punchium_init call missing")
            if b'&& !strstr(rominfo.product,"T-574120-00")' in md_cart_after:
                die("self-test: obsolete PAPRIUM mapper exclusion still present")
            print(f"Self-test {label}: OK")


if __name__ == "__main__":
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
            self_test()
        elif len(sys.argv) == 2:
            patch_tree(Path(sys.argv[1]))
        else:
            print("Usage: patch_g64x_wii.py <source-dir>")
            print("       patch_g64x_wii.py --self-test")
            raise SystemExit(2)
    except Exception as exc:
        print(f"PATCH FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
