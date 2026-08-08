#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PARENT="$(cd "$ROOT/.." && pwd)"
OUT="$ROOT/output"

REPO="https://github.com/pav1388/Genesis-Plus-GX-PUNCHiUM.git"
PIN="3849f3d3432df1d6320574e73695dd379ecef2b3"

SHARED_WORK="${G64X_WORK:-$PARENT/G64X_SHARED_WORK}"
SRC="${G64X_SOURCE:-$SHARED_WORK/Genesis-Plus-GX-PUNCHiUM}"

DRFLAC_VENDOR="$ROOT/vendor/dr_libs/dr_flac.h"
DRFLAC_DEST="$SRC/core/cd_hw/libchdr/deps/dr_libs/dr_flac.h"
DRFLAC_SHA256="3c579b159fb3d3dc639f7f5404fa373e515bcd2b6c7616cf0b297debd809ef76"

die() {
  echo
  echo "ERROR: $*" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not found"
}

need git
need python3
need make
need powerpc-eabi-gcc
need sha256sum

: "${DEVKITPRO:?DEVKITPRO is not set}"
: "${DEVKITPPC:?DEVKITPPC is not set}"
[ -f "$DEVKITPPC/wii_rules" ] || die "Missing $DEVKITPPC/wii_rules"

LIBOGC_SPR="${LIBOGC_SPR:-$DEVKITPRO/libogc/include/tuxedo/ppc/spr.h}"
[ -f "$LIBOGC_SPR" ] || die "Current libogc spr.h not found: $LIBOGC_SPR"

LIBOGC_LWP="$DEVKITPRO/libogc/include/ogc/lwp.h"
LIBOGC_CARD="$DEVKITPRO/libogc/include/ogc/card.h"
[ -f "$LIBOGC_LWP" ] || die "Current public libogc lwp.h not found: $LIBOGC_LWP"
[ -f "$LIBOGC_CARD" ] || die "Current libogc card.h not found: $LIBOGC_CARD"
grep -Fq 'CARD_WORKAREA_SIZE' "$LIBOGC_CARD" \
  || die "Current libogc card.h does not define CARD_WORKAREA_SIZE"

export GIT_PAGER=cat
export PAGER=cat
git config --global --add safe.directory '*' >/dev/null 2>&1 || true

mkdir -p "$SHARED_WORK" "$OUT"

if [ ! -d "$SRC/.git" ]; then
  echo "Shared source not found; cloning once..."
  git clone --no-tags "$REPO" "$SRC"
else
  echo "Reusing shared source:"
  echo "  $SRC"
fi

if ! git -C "$SRC" cat-file -e "$PIN^{commit}" 2>/dev/null; then
  echo "Pinned revision missing locally; fetching it..."
  git -C "$SRC" fetch --no-tags origin punchium
fi

HEAD_NOW="$(git -C "$SRC" rev-parse HEAD)"
if [ "$HEAD_NOW" != "$PIN" ]; then
  echo "Switching repository to pinned revision..."
  git -C "$SRC" checkout --force --detach "$PIN"
fi

# Restore files patched by the previous run while keeping the generated
# shared compatibility header in place when possible. This preserves useful
# incremental-build timestamps without carrying stale source edits forward.
echo "Restoring transient patched files to upstream..."
git -C "$SRC" checkout "$PIN" -- \
  gx/fileio/file_load.c \
  gx/config.c \
  gx/config.h \
  core/cart_hw/punchium.h \
  core/vdp_ctrl.c \
  core/z80/z80.c \
  core/sound/ym2612.c \
  core/cd_hw/libchdr/src/cdrom.h \
  core/cd_hw/libchdr/src/coretypes.h \
  Makefile.wii \
  gx/gui/menu.c \
  gx/fileio/file_slot.c \
  gx/main.c \
  core/cart_hw/md_cart.c

# Product detection stays exactly as it is in the pinned upstream revision.
if ! git -C "$SRC" --no-pager diff --quiet -- core/cart_hw/md_cart.c; then
  die "md_cart.c restore from pinned revision failed"
fi

if ! grep -Fq '#include "g64x_libogc_spr_compat.h"' "$SRC/core/shared.h" 2>/dev/null; then
  git -C "$SRC" checkout "$PIN" -- core/shared.h
fi

ACTUAL="$(git -C "$SRC" rev-parse HEAD)"
[ "$ACTUAL" = "$PIN" ] || die "Source revision mismatch: $ACTUAL"

echo
echo "Testing generators/patchers..."
python3 "$ROOT/generate_spr_compat.py" --self-test
python3 "$ROOT/patch_g64x_wii.py" --self-test

echo
echo "Generating compatibility layer from CURRENT libogc..."
python3 "$ROOT/generate_spr_compat.py" \
  "$LIBOGC_SPR" \
  "$SRC/core/g64x_libogc_spr_compat.h"

# Sanity-check the two collisions already seen with libogc.
grep -Fq '#undef DEC' "$SRC/core/g64x_libogc_spr_compat.h" \
  || die "Generated compatibility header does not neutralize DEC"
grep -Fq '#undef TBL' "$SRC/core/g64x_libogc_spr_compat.h" \
  || die "Generated compatibility header does not neutralize TBL"

echo
echo "Installing pinned libchdr dr_flac dependency..."
[ -f "$DRFLAC_VENDOR" ] || die "Bundled dr_flac.h missing from package"

ACTUAL_DRFLAC_SHA="$(sha256sum "$DRFLAC_VENDOR" | awk '{print $1}')"
[ "$ACTUAL_DRFLAC_SHA" = "$DRFLAC_SHA256" ] \
  || die "Bundled dr_flac.h checksum mismatch: $ACTUAL_DRFLAC_SHA"

mkdir -p "$(dirname "$DRFLAC_DEST")"
if [ ! -f "$DRFLAC_DEST" ] || ! cmp -s "$DRFLAC_VENDOR" "$DRFLAC_DEST"; then
  cp "$DRFLAC_VENDOR" "$DRFLAC_DEST"
  echo "dr_flac.h installed: v0.12.42"
else
  echo "dr_flac.h already present and identical: v0.12.42"
fi

grep -Fq '#define DRFLAC_VERSION_MINOR     12' "$DRFLAC_DEST" \
  || die "Unexpected dr_flac major/minor version"
grep -Fq '#define DRFLAC_VERSION_REVISION  42' "$DRFLAC_DEST" \
  || die "Unexpected dr_flac revision"
grep -Fq 'DRFLAC_API drflac* drflac_open_with_metadata(drflac_read_proc onRead, drflac_seek_proc onSeek, drflac_meta_proc onMeta, void* pUserData, const drflac_allocation_callbacks* pAllocationCallbacks);' "$DRFLAC_DEST" \
  || die "dr_flac API does not match this libchdr flac.c"

echo
echo "Applying PAPwiiUM patch..."
python3 "$ROOT/patch_g64x_wii.py" "$SRC"

echo
echo "Validating source changes..."

mapfile -t CHANGED < <(git -C "$SRC" --no-pager diff --name-only)
printf 'Modified tracked source files:\n'
printf '  %s\n' "${CHANGED[@]}"

# Files expected to differ from the pinned revision.
for required in \
  core/shared.h \
  core/cart_hw/punchium.h \
  core/vdp_ctrl.c \
  gx/fileio/file_load.c \
  gx/config.c \
  gx/config.h \
  core/cd_hw/libchdr/src/cdrom.h \
  core/cd_hw/libchdr/src/coretypes.h \
  Makefile.wii \
  gx/gui/menu.c \
  gx/fileio/file_slot.c \
  gx/main.c
do
  [[ " ${CHANGED[*]} " == *" $required "* ]] || die "$required not modified"
done

# The generated SPR compatibility header replaces the old one-off fixes.
if git -C "$SRC" --no-pager diff --quiet -- core/z80/z80.c; then
  :
else
  die "z80.c still has an old one-off patch; expected clean upstream file"
fi

if git -C "$SRC" --no-pager diff --quiet -- core/sound/ym2612.c; then
  :
else
  die "ym2612.c unexpectedly modified"
fi

if git -C "$SRC" --no-pager diff --quiet -- core/cart_hw/md_cart.c; then
  :
else
  die "md_cart.c unexpectedly modified; upstream product detection must stay untouched"
fi

git --no-pager -C "$SRC" -c core.whitespace=cr-at-eol diff --check -- \
  core/shared.h \
  core/cart_hw/punchium.h \
  core/vdp_ctrl.c \
  gx/fileio/file_load.c \
  gx/config.c \
  gx/config.h \
  core/cd_hw/libchdr/src/cdrom.h \
  core/cd_hw/libchdr/src/coretypes.h \
  Makefile.wii \
  gx/gui/menu.c \
  gx/fileio/file_slot.c \
  gx/main.c

grep -Fq 'PAPwiiUM Wii v2.15' "$SRC/gx/fileio/file_load.c" \
  || die "Build marker missing"

grep -Fq 'uint8_t punchium_audio_track_format = 1;' "$SRC/gx/fileio/file_load.c" \
  || die "WAV format variable is not fixed to 1"

grep -Fq 'g64x_paprium_cheat_load();' "$SRC/gx/fileio/file_load.c" \
  || die "PAPRIUM one-hit state is not refreshed before ROM init"
grep -Fq 'paprium_g64x.cfg' "$SRC/gx/config.c" \
  || die "PAPRIUM one-hit persistence file support missing"
grep -Fq 'g64x_paprium_cheat_toggle' "$SRC/gx/config.c" \
  || die "PAPRIUM one-hit toggle implementation missing"
grep -Fq 'g64x_paprium_cheat_get' "$SRC/gx/config.h" \
  || die "PAPRIUM one-hit config API missing"

grep -Fq 'formats_to_try[0] = punchium_audio_track_format;' "$SRC/core/cart_hw/punchium.h" \
  || die "WAV selector assignment missing"

grep -Fq 'i < 1 && !track_loaded' "$SRC/core/cart_hw/punchium.h" \
  || die "Single-attempt WAV loop missing"

if grep -Fq 'i < 5 && !track_loaded' "$SRC/core/cart_hw/punchium.h"; then
  die "Unsafe upstream audio loop is still present"
fi

grep -Fq '#define PUNCHIUM_WAV_STREAM_FRAMES 32768' "$SRC/core/cart_hw/punchium.h" \
  || die "WAV stream chunk size missing"
grep -Fq 'malloc(PUNCHIUM_WAV_STREAM_FRAMES * punchium_track.channels' "$SRC/core/cart_hw/punchium.h" \
  || die "WAV bounded stream allocation missing"
if grep -Fq 'malloc(punchium_track.total_samples * punchium_track.channels' "$SRC/core/cart_hw/punchium.h"; then
  die "Full-track WAV allocation still present"
fi
grep -Fq 'static int punchium_wav_stream_refill' "$SRC/core/cart_hw/punchium.h" \
  || die "WAV stream refill helper missing"
if grep -Fq 'LWP_CreateThread' "$SRC/core/cart_hw/punchium.h"; then
  die "Experimental audio LWP thread unexpectedly present"
fi
grep -Fq 'drwav_seek_to_pcm_frame(&punchium_track.wav, target_frame)' "$SRC/core/cart_hw/punchium.h" \
  || die "WAV loop/reload seek support missing"
grep -Fq 'void g64x_punchium_frontend_shutdown(void)' "$SRC/core/cart_hw/punchium.h" \
  || die "PUNCHiUM frontend shutdown cleanup missing"
grep -Fq 'g64x_punchium_frontend_shutdown();' "$SRC/gx/main.c" \
  || die "Wii POWER PUNCHiUM cleanup call missing"
SYNC_GUARD_COUNT="$(grep -Fc 'while (sync && !Shutdown)' "$SRC/gx/main.c" || true)"
[ "$SYNC_GUARD_COUNT" = "3" ] \
  || die "Wii POWER sync guard count mismatch: expected 3, got $SYNC_GUARD_COUNT"
grep -Fq '/* PAPwiiUM POWER: direct hard-black shutdown */' "$SRC/gx/main.c" \
  || die "Wii direct POWER shutdown branch missing"
grep -Fq 'SYS_ResetSystem(SYS_POWEROFF, 0, 0);' "$SRC/gx/main.c" \
  || die "Wii direct SYS_POWEROFF missing"
grep -Fq '/* PAPwiiUM: force Backup RAM autoload */' "$SRC/gx/main.c" \
  || die "PAPRIUM forced Backup RAM autoload missing"
grep -Fq '/* PAPwiiUM: force-save Backup RAM / PAPRIUM EEPROM */' "$SRC/gx/main.c" \
  || die "PAPRIUM forced Backup RAM autosave missing"


grep -Fq '#define PUNCHIUM_BYTE_XOR 1' "$SRC/core/cart_hw/punchium.h" \
  || die "PUNCHiUM little-endian byte selector missing"
grep -Fq '#define PUNCHIUM_BYTE_XOR 0' "$SRC/core/cart_hw/punchium.h" \
  || die "PUNCHiUM big-endian byte selector missing"
grep -Fq '#define PUNCHIUM_RAW_BYTE_XOR 0' "$SRC/core/cart_hw/punchium.h" \
  || die "PUNCHiUM little-endian raw-byte selector missing"
grep -Fq '#define PUNCHIUM_RAW_BYTE_XOR 1' "$SRC/core/cart_hw/punchium.h" \
  || die "PUNCHiUM PowerPC raw-byte selector missing"
grep -Fq '#define PUNCHIUM_RAW_U8(base, offset)' "$SRC/core/cart_hw/punchium.h" \
  || die "PUNCHiUM raw-byte accessor missing"
grep -Fq '#define PUNCHIUM_READ_U32_WORDPAIR' "$SRC/core/cart_hw/punchium.h" \
  || die "PUNCHiUM 32-bit word-pair accessor missing"
grep -Fq '^PUNCHIUM_BYTE_XOR' "$SRC/core/cart_hw/punchium.h" \
  || die "PUNCHiUM portable logical byte addressing not used"
if grep -Fq '^1' "$SRC/core/cart_hw/punchium.h"; then
  die "PUNCHiUM still contains hardcoded little-endian ^1 byte addressing"
fi
RAW_U8_COUNT="$(grep -Fo 'PUNCHIUM_RAW_U8(' "$SRC/core/cart_hw/punchium.h" | wc -l | tr -d ' ')"
[ "$RAW_U8_COUNT" -ge 20 ] || die "PUNCHiUM raw-byte conversion count too low: $RAW_U8_COUNT"
WORDPAIR_COUNT="$(grep -Fo 'PUNCHIUM_READ_U32_WORDPAIR(' "$SRC/core/cart_hw/punchium.h" | wc -l | tr -d ' ')"
[ "$WORDPAIR_COUNT" -ge 3 ] || die "PUNCHiUM word-pair conversion count too low: $WORDPAIR_COUNT"
if grep -Fq 'memset(tile_cache.hash_table, MAX_TILE_CACHE_ENTRIES' "$SRC/core/cart_hw/punchium.h"; then
  die "Broken tile-cache uint32 sentinel memset still present"
fi
grep -Fq 'tile_cache.hash_table[i] = MAX_TILE_CACHE_ENTRIES;' "$SRC/core/cart_hw/punchium.h" \
  || die "Tile-cache hash sentinel reset fix missing"

grep -Fq '#define log_cb(...) ((void)0)' "$SRC/core/vdp_ctrl.c" \
  || die "Wii VDP logger shim missing"

grep -Fq '#include "g64x_libogc_spr_compat.h"' "$SRC/core/shared.h" \
  || die "Core-wide libogc compatibility include missing"

grep -Fq 'static inline uint32_t msf_to_lba(uint32_t msf)' "$SRC/core/cd_hw/libchdr/src/cdrom.h" \
  || die "libchdr msf_to_lba INLINE fix missing"

grep -Fq 'static inline uint32_t lba_to_msf(uint32_t lba)' "$SRC/core/cd_hw/libchdr/src/cdrom.h" \
  || die "libchdr lba_to_msf INLINE fix missing"

grep -Fq 'static inline uint32_t lba_to_msf_alt(int lba)' "$SRC/core/cd_hw/libchdr/src/cdrom.h" \
  || die "libchdr lba_to_msf_alt INLINE fix missing"

if grep -Fq 'INLINE uint32_t' "$SRC/core/cd_hw/libchdr/src/cdrom.h"; then
  die "Undefined libchdr INLINE usage remains in cdrom.h"
fi

grep -Fq 'static inline size_t core_fsize(core_file *f)' "$SRC/core/cd_hw/libchdr/src/coretypes.h" \
  || die "libchdr core_fsize cdStream adapter missing"

grep -Fq 'core_fseek(f, current, SEEK_SET);' "$SRC/core/cd_hw/libchdr/src/coretypes.h" \
  || die "libchdr core_fsize does not restore stream position"

grep -Fq '$(CHDLIBDIR)/deps $(CHDLIBDIR)/deps/libFLAC/include' "$SRC/Makefile.wii" \
  || die "Makefile.wii does not expose libchdr deps include root"

grep -Fq '+@$(MAKE) --no-print-directory -C $(BUILD)' "$SRC/Makefile.wii" \
  || die "Makefile.wii recursive make/jobserver fix missing"

grep -Fq '#include <ogc/lwp.h>' "$SRC/gx/gui/menu.c" \
  || die "menu.c does not use current public ogc/lwp.h"
if grep -Fq '#include <ogc/lwp_threads.h>' "$SRC/gx/gui/menu.c"; then
  die "menu.c still includes removed/private ogc/lwp_threads.h"
fi
if grep -Fq '__lwp_thread_stopmultitasking' "$SRC/gx/gui/menu.c"; then
  grep -Fq '#define __lwp_thread_stopmultitasking(entry) ((entry)())' "$SRC/gx/gui/menu.c" \
    || die "menu.c private LWP call remains without compatibility shim"
else
  grep -Fq 'reload();' "$SRC/gx/gui/menu.c" \
    || die "menu.c has neither LWP compatibility shim nor direct reload callback"
fi

grep -Fq 'PAPRIUM One-Hit Kill: OFF' "$SRC/gx/gui/menu.c" \
  || die "PAPRIUM one-hit menu item missing"
grep -Fq 'case 13: /*** PAPRIUM one-hit kill ***/' "$SRC/gx/gui/menu.c" \
  || die "PAPRIUM one-hit menu toggle case missing"
grep -Fq 'm->max_items = 14;' "$SRC/gx/gui/menu.c" \
  || die "PAPRIUM one-hit menu item not included in Wii menu count"

grep -Fq '#define CARD_WORKAREA CARD_WORKAREA_SIZE' "$SRC/gx/fileio/file_slot.c" \
  || die "file_slot.c CARD_WORKAREA compatibility alias missing"

grep -Fq 'uint8 cart_size = 6;' "$SRC/gx/main.c" \
  || die "Wii standalone cart_size definition missing"

grep -Fq 'else if (strstr(rominfo.product,"T-574120-00"))' "$SRC/core/cart_hw/md_cart.c" \
  || die "PUNCHiUM specific product branch missing"
grep -Fq 'cart.special |= HW_PUNCHIUM;' "$SRC/core/cart_hw/md_cart.c" \
  || die "PUNCHiUM HW flag assignment missing"
grep -Fq 'punchium_init();' "$SRC/core/cart_hw/md_cart.c" \
  || die "PUNCHiUM initialization call missing"
if grep -Fq '&& !strstr(rominfo.product,"T-574120-00")' "$SRC/core/cart_hw/md_cart.c"; then
  die "Obsolete PAPRIUM mapper exclusion remained in md_cart.c"
fi

[ -s "$DRFLAC_DEST" ] || die "libchdr dr_flac.h missing after install"
[ "$(sha256sum "$DRFLAC_DEST" | awk '{print $1}')" = "$DRFLAC_SHA256" ] \
  || die "installed dr_flac.h checksum mismatch"

# Verify the fork's flac.c still expects the exact API/header shape we vendor.
grep -Fq '#include <dr_libs/dr_flac.h>' "$SRC/core/cd_hw/libchdr/src/flac.c" \
  || die "Unexpected libchdr flac.c include changed"
grep -Fq 'flac_decoder_metadata_callback, decoder, NULL);' "$SRC/core/cd_hw/libchdr/src/flac.c" \
  || die "Unexpected libchdr dr_flac API call changed"
grep -Fq 'DRFLAC_CACHE_L2_LINES_REMAINING(&flac->bs)' "$SRC/core/cd_hw/libchdr/src/flac.c" \
  || die "Unexpected libchdr dr_flac internals changed"

echo "libchdr dependency preflight: OK (dr_flac v0.12.42)"
echo "modern libogc Wii API preflight: OK (lwp.h + LWP exit shim + CARD_WORKAREA_SIZE)"
echo "PUNCHiUM product detection preflight: OK (upstream T-574120-00 branch)"
echo "PUNCHiUM endian preflight: OK (logical bytes + inverse raw-byte lanes + 32-bit word pairs)"
echo "PUNCHiUM tile-cache reset preflight: OK (uint32 sentinel loop)"
echo "PUNCHiUM WAV streaming preflight: OK (32768-frame safe single-thread window)"
echo "PAPRIUM one-hit menu preflight: OK (persistent, default OFF)"

# The generated header already covers every public object-like SPR macro.
# Avoid a full source-token scan here; it is unnecessarily slow on /mnt/c.

echo "Fast SPR validation: OK (DEC/TBL + generated full macro header)"

echo "Source validation: OK"

# Keep build_wii. Make dependency files decide what needs rebuilding.
echo
echo "Incremental build enabled."
echo "Existing build_wii objects are kept; make decides what actually changed."

if [ "${FORCE_REBUILD:-0}" = "1" ]; then
  echo
  echo "FORCE_REBUILD=1: cleaning Wii build..."
  make -C "$SRC" -f Makefile.wii clean
fi

echo
echo "Building Wii DOL..."
set +e
make -k -C "$SRC" -f Makefile.wii -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)" \
  2>&1 | tee "$OUT/COMPILE.log"
MAKE_STATUS=${PIPESTATUS[0]}
set -e

if [ "$MAKE_STATUS" -ne 0 ]; then
  echo
  echo "Compilation failed, but make -k continued through independent files."
  echo "Collecting every compiler error from this run..."
  grep -nE 'fatal error:|(^|[[:space:]])error:|undefined reference|cannot find|multiple definition|ld:' "$OUT/COMPILE.log" \
    > "$OUT/COMPILER_ERRORS.txt" || true

  if [ -s "$OUT/COMPILER_ERRORS.txt" ]; then
    echo
    echo "All detected compiler errors:"
    cat "$OUT/COMPILER_ERRORS.txt"
  fi

  echo
  echo "Shared source/build cache was KEPT:"
  echo "  $SRC"
  echo "Full compiler log:"
  echo "  $OUT/COMPILE.log"
  echo "Error summary:"
  echo "  $OUT/COMPILER_ERRORS.txt"
  exit "$MAKE_STATUS"
fi


DOL="$SRC/genplus_wii.dol"
[ -s "$DOL" ] || die "make finished without producing genplus_wii.dol"

cp "$DOL" "$OUT/boot.dol"
git -C "$SRC" --no-pager diff -- \
  core/shared.h \
  core/cart_hw/punchium.h \
  core/vdp_ctrl.c \
  gx/fileio/file_load.c \
  gx/config.c \
  gx/config.h \
  core/cd_hw/libchdr/src/cdrom.h \
  core/cd_hw/libchdr/src/coretypes.h \
  Makefile.wii \
  gx/gui/menu.c \
  gx/fileio/file_slot.c \
  gx/main.c \
  > "$OUT/PAPwiiUM.patch"
cp "$SRC/core/g64x_libogc_spr_compat.h" "$OUT/g64x_libogc_spr_compat.h"

python3 "$ROOT/verify_dol.py" "$OUT/boot.dol" | tee "$OUT/VERIFY.txt"

{
  echo "PAPwiiUM Wii v2.15"
  echo "Source: $REPO"
  echo "Revision: $PIN"
  echo "Shared source: $SRC"
  echo "Current libogc SPR header: $LIBOGC_SPR"
  echo "SPR validation mode: fast (no full core token scan)"
  echo "General SPR compatibility: enabled"
  echo "libchdr dr_flac: bundled v0.12.42"
  echo "make keep-going diagnostics: enabled"
  echo "modern libogc compatibility: lwp.h + LWP exit shim + CARD_WORKAREA_SIZE"
  echo "standalone Sega CD backup cart default: 4Mbit (ID 6)"
  echo "PUNCHiUM product detection: upstream T-574120-00 branch unchanged"
  echo "PUNCHiUM byte addressing: logical + raw lanes endian-portable"
  echo "PUNCHiUM sprite/SFX packed reads: PowerPC-compatible"
  echo "PUNCHiUM tile-cache reset: uint32 sentinel fixed"
  echo "PUNCHiUM WAV audio: window-streamed (32768 frames, no full-track decode)"
  echo "PAPRIUM one-hit kill: standalone menu option, persistent, default OFF"
  echo "Incremental cache: enabled; no automatic clean"
  echo "Compiler: $(powerpc-eabi-gcc --version | head -n1)"
  echo
  cat "$OUT/VERIFY.txt"
} > "$OUT/BUILD_INFO.txt"

echo
echo "=============================================="
echo "SUCCESS"
echo "boot.dol: $OUT/boot.dol"
echo "Shared work kept at: $SRC"
echo "=============================================="
