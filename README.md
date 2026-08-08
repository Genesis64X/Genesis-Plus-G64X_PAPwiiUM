# G64X_PAPwiiUM

G64X_PAPwiiUM is a Wii-focused build of the standalone Genesis Plus GX frontend by ekeeke, based on pav1388's Genesis-Plus-GX-PUNCHiUM fork.

To install it, replace the `boot.dol` inside your `apps/genplus-gx` folder with the one included here. Make a backup of your original `boot.dol` first.

Place `paprium.bin` in your `genplus/roms` folder on the Wii SD card.

Then place the `paprium` music folder in that same `genplus/roms` folder.

## Notes

This is still a beta build, so a few bugs and glitches remain.

For now, I am using WAV files only. MP3 support has been disabled because it introduced additional glitches, longer loading times, and higher RAM usage.

I also added a one-hit kill cheat to the settings menu, just for fun. Enable it, reset the game, and it should take effect.

## Known issues

Some audio may briefly skip during transitions, such as when selecting a character or entering a boss room. It does not affect gameplay.

Saving appears to be working properly so far, but I am still testing it.

There is also a graphical glitch that can occasionally cause the Wii to freeze on a black screen with red flashing. This should be fixed in a future build. The workaround is to quit using the HOME button and exit through the Genesis Plus GX GUI. If you always do that, it shouldn't freeze. It does not seem to affect normal gameplay, but it is definitely annoying.

## Credits

PAPwiiUM Wii build and port work: Genesis64X

Based on Genesis Plus GX by ekeeke  
https://github.com/ekeeke/genesis-plus-gx

Based on the Genesis-Plus-GX-PUNCHiUM fork by pav1388  
https://github.com/pav1388/Genesis-Plus-GX-PUNCHiUM

PAPRIUM is the property of its respective rights holders. This project does not include or distribute any game data.
