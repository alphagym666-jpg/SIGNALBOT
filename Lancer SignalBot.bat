@echo off
chcp 65001 >nul
title SignalBot
rem On se relance depuis une copie temporaire : la mise a jour peut alors remplacer ce fichier.
if /i "%~1"=="--depuis-temp" goto start
copy /y "%~f0" "%TEMP%\signalbot_lanceur.bat" >nul
"%TEMP%\signalbot_lanceur.bat" --depuis-temp "%~dp0."
exit /b
:start
cd /d "%~2"

echo.
echo  ==========================================
echo     SignalBot - Nasdaq, Or et Actions
echo  ==========================================
echo.

rem ---- 0. Mise a jour automatique (garde .env, data et .venv)
if exist "pas_de_mise_a_jour.txt" goto maj_ok
if not exist "outils\mise_a_jour.ps1" goto maj_ok
echo  [..] Recherche d'une mise a jour...
powershell -NoProfile -ExecutionPolicy Bypass -File "outils\mise_a_jour.ps1" -Dest "%CD%"
:maj_ok
echo.

rem ---- 1. Trouver Python
set "PY=python"
python --version >nul 2>nul
if not errorlevel 1 goto python_ok
set "PY=py"
py --version >nul 2>nul
if not errorlevel 1 goto python_ok
echo  [X] Python n'est pas installe sur cet ordinateur.
echo      La page de telechargement va s'ouvrir.
echo      IMPORTANT : coche "Add Python to PATH" pendant l'installation,
echo      puis relance ce fichier.
start "" https://www.python.org/downloads/
pause
exit /b 1
:python_ok

rem ---- 2. Fichier .env avec le token Telegram
if exist ".env" goto env_ok
copy ".env.example" ".env" >nul
echo  [!] Premiere utilisation : le fichier de configuration va s'ouvrir.
echo      Colle ton token Telegram apres TELEGRAM_BOT_TOKEN=
echo      Enregistre avec Ctrl+S, ferme le Bloc-notes, et le bot va demarrer.
echo.
pause
notepad ".env"
:env_ok
findstr /C:"remplace-moi" ".env" >nul
if errorlevel 1 goto token_ok
echo  [!] Ton token Telegram n'est pas encore dans le fichier .env
echo      Colle-le apres TELEGRAM_BOT_TOKEN= puis enregistre avec Ctrl+S.
notepad ".env"
:token_ok

rem ---- 3. Environnement Python du bot (cree une seule fois)
set "RUNPY=.venv\Scripts\python.exe"
set "MARKER=.venv\requirements.installed"
if exist "%RUNPY%" goto venv_check
echo  [..] Premiere installation, ca prend 1 a 3 minutes...
%PY% -m venv .venv
if errorlevel 1 goto use_system
:venv_check
rem certains Python creent l'environnement sans pip : on le repare, sinon on utilise le Python normal
"%RUNPY%" -m pip --version >nul 2>nul
if not errorlevel 1 goto deps
echo  [..] Reparation de pip...
"%RUNPY%" -m ensurepip --upgrade >nul 2>nul
"%RUNPY%" -m pip --version >nul 2>nul
if not errorlevel 1 goto deps
:use_system
echo  [..] Utilisation du Python installe sur l'ordinateur.
if exist ".venv" rmdir /s /q ".venv"
set "RUNPY=%PY%"
set "MARKER=.requirements.installed"
%PY% -m pip --version >nul 2>nul
if errorlevel 1 %PY% -m ensurepip --upgrade >nul 2>nul

rem ---- 4. Installer / mettre a jour les modules si requirements.txt a change
:deps
fc /b "requirements.txt" "%MARKER%" >nul 2>nul
if not errorlevel 1 goto deps_ok
echo  [..] Installation des modules necessaires...
"%RUNPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto install_error
copy /y "requirements.txt" "%MARKER%" >nul
:deps_ok

rem ---- 5. Lancer le bot
echo  [OK] Le bot demarre. Laisse cette fenetre ouverte.
echo       Pour l'arreter : ferme la fenetre ou fais Ctrl+C.
echo       Dans Telegram, envoie /start a ton bot.
echo.
"%RUNPY%" -m signalbot
echo.
echo  [!] Le bot s'est arrete. Si tu vois une erreur plus haut,
echo      prends une capture d'ecran de cette fenetre.
pause
exit /b 0

:install_error
echo.
echo  [X] L'installation a echoue. Prends une capture d'ecran de cette fenetre.
echo      Astuce : supprime le dossier .venv dans le dossier du bot et relance ce fichier.
pause
exit /b 1
