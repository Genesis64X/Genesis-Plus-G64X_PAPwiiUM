# PAPwiiUM

PAPwiiUM is a Wii-focused build patch for the standalone Genesis Plus GX frontend. It is built on top of pav1388's Genesis-Plus-GX-PUNCHiUM fork and targets PAPRIUM without requiring RetroArch.

The repository contains the build and patch tooling only. It does not include PAPRIUM, its ROM, or its soundtrack.

## What it changes

- PowerPC-safe PUNCHiUM byte and packed-word access
- 1 MiB PUNCHiUM tile cache fix
- external WAV soundtrack support with stereo preserved
- bounded 32,768-frame WAV streaming instead of decoding a whole track at once
- WAV-only soundtrack lookup; MP3/OGG fallback is disabled
- PAPRIUM SRAM/EEPROM is forced to load from and save to slot 0 (`.srm`)
- direct Wii power-off path that blanks video before teardown and avoids the normal menu screenshot transition
- optional PAPRIUM One-Hit Kill toggle in the standalone Wii menu, disabled by default
- compatibility fixes for current libogc/devkitPPC
- bundled `dr_flac` header required by the pinned libchdr tree

The audio path is intentionally single-threaded. Earlier background-prefetch experiments were less stable on Wii and are not part of this version.

## Upstream

PAPwiiUM patches this fork at build time:

- Repository: `https://github.com/pav1388/Genesis-Plus-GX-PUNCHiUM.git`
- Pinned revision: `3849f3d3432df1d6320574e73695dd379ecef2b3`

Keeping the upstream revision pinned makes the patch deterministic and prevents an unrelated upstream change from silently changing the build.

## Build requirements

The easiest route is Docker with the devkitPro image used by the build script:

- Docker
- WSL/Linux shell
- enough free space for the shared source tree and Wii build objects

The native script also works in an environment where devkitPPC and libogc are already configured.

## Build

From WSL:

```bash
cd "/mnt/c/Users/<you>/path/to/PAPwiiUM"
chmod +x BUILD_DOCKER.sh BUILD_NATIVE_LINUX.sh
./BUILD_DOCKER.sh
```

The final DOL is written to:

```text
output/boot.dol
```

The build also writes a compiler log, a patch against the pinned source, build information, and DOL validation results to `output/`.

### Incremental builds

Source and object files are kept outside the package directory in:

```text
G64X_SHARED_WORK/Genesis-Plus-GX-PUNCHiUM
```

`build_wii` is preserved between builds. The scripts do not run `git clean -fdx` or delete the build directory automatically.

To deliberately force a clean Wii rebuild:

```bash
FORCE_REBUILD=1 ./BUILD_DOCKER.sh
```

## PAPRIUM files

Example layout:

```text
sd:/roms/megadrive/Paprium.bin
sd:/roms/megadrive/paprium/<track name>.wav
```

The soundtrack files are read from the `paprium` folder next to the ROM. Stereo WAV files remain stereo.

Genesis Plus GX stores PAPRIUM's persistent data as normal backup RAM. The exact filename follows the ROM filename and uses the `.srm` extension in the frontend save directory.

## Wii shutdown behavior

PAPRIUM can leave the last rendered frame in a bad state while the console is powering down. The stock Wii frontend normally captures that frame for its menu transition, which can show a red/corrupted flash.

On a real Wii power request PAPwiiUM instead:

1. stops further A/V synchronization
2. forces VI output black
3. stops audio
4. closes PUNCHiUM-owned WAV/cache resources while FAT is still mounted
5. saves backup RAM
6. runs the normal frontend shutdown
7. requests `SYS_POWEROFF`

Normal menu transitions are left alone.

## Notes

`generate_spr_compat.py` generates a small compatibility header from the libogc installed in the build environment. This avoids namespace collisions between generic PowerPC SPR macros such as `DEC`/`TBL` and identifiers used by the Genesis Plus GX core.

`patch_g64x_wii.py` preserves the original line endings and legacy 8-bit source bytes in the old Wii frontend. Its self-test covers both LF and CRLF input.

## Credits

PAPwiiUM build/port work: Genesis64X

Based on Genesis Plus GX and the Genesis-Plus-GX-PUNCHiUM fork by their respective authors and contributors. `dr_flac` is by David Reid (mackron) and retains its upstream license text in the bundled header.

PAPRIUM is property of its respective rights holders. This project does not distribute game data.
