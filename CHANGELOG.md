# Changelog

## 2.15

- force PAPRIUM backup RAM load/save through slot 0
- bypass the normal Wii menu transition on a real power request
- blank VI before shutdown to avoid the corrupted final-frame flash
- retain the stable single-thread 32,768-frame WAV streamer

## 2.13

- returned WAV streaming to the stable single-thread path
- removed experimental LWP audio prefetch

## 2.4

- completed the PowerPC byte-lane and packed-word fixes
- fixed the 1 MiB tile-cache sentinel initialization
- first stable PAPRIUM gameplay baseline on Wii

## 1.x

- modern libogc/devkitPPC compatibility
- persistent incremental build workspace
- bundled missing libchdr `dr_flac` dependency
- fast SPR compatibility generation
