# Обёртка для запуска окна скачивания двойным кликом (без консольного окна).
# Просто откройте этот файл дважды - или батник "Скачать фото.bat".
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yadisk_gui

yadisk_gui.main()
