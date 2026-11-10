# Yandex-disk-folder-download-fix
After some troubles service don`t work correctly, so lets fix it!
"Download the фото.bat" (rewrote):

saved in the "native" encoding of the console (cp866) — Cyrillic does not break the startup.;
searches for pythonw/pyw in PATH;
If it doesn't find it, it launches it. 
yadisk_gui.py
through file association (just like your work double—click) - this closes your case.;
if something goes wrong, the window does not blink, but writes the reason and waits for the keystroke.
"Download the photo.pyw" (new) — you don't have to bother with the batnik at all: double-clicking on it opens the window immediately, without the black console. It works through the same mechanism as your click on 
yadisk_gui.py
.

Keep both files in the same folder with 
yadisk_gui.py
. You can continue to run it directly through 
yadisk_gui.py
— it's the same.
