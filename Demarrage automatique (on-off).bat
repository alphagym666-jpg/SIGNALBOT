@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SignalBot.lnk"

if exist "%LNK%" goto remove

powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath='%~dp0Lancer SignalBot.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 goto fail
echo.
echo  [ON] SignalBot va maintenant demarrer tout seul quand tu ouvres ta session Windows.
echo       La fenetre s'ouvrira reduite dans la barre des taches.
echo       Relance ce fichier pour desactiver.
pause
exit /b 0

:remove
del "%LNK%"
echo.
echo  [OFF] SignalBot ne demarrera plus automatiquement avec Windows.
echo        Relance ce fichier pour reactiver.
pause
exit /b 0

:fail
echo  [X] Impossible de creer le raccourci de demarrage.
pause
exit /b 1
