@echo off
echo Installing PyInstaller...
py -m pip install pyinstaller
echo Building CodMod.exe ...
py -m PyInstaller --noconfirm --onefile --windowed --name CodMod --add-data "catalog.json;." codmod.py
echo.
echo Done! Your exe is in the "dist" folder.
pause
