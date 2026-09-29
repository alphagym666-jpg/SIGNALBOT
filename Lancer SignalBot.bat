@echo off
chcp 65001 >nul
title SignalBot
cd /d "%~dp0"

echo.
echo  ==========================================
echo     SignalBot - Nasdaq, Or et Actions
echo  ==========================================
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
if exist ".venv\Scripts\python.exe" goto venv_ok
echo  [..] Premiere installation, ca prend 1 a 3 minutes...
%PY% -m venv .venv
if errorlevel 1 goto install_error
:venv_ok

rem ---- 4. Installer / mettre a jour les modules si requirements.txt a change
fc /b "requirements.txt" ".venv\requirements.installed" >nul 2>nul
if not errorlevel 1 goto deps_ok
echo  [..] Installation des modules necessaires...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto install_error
copy /y "requirements.txt" ".venv\requirements.installed" >nul
:deps_ok

rem ---- 5. Lancer le bot
echo  [OK] Le bot demarre. Laisse cette fenetre ouverte.
echo       Pour l'arreter : ferme la fenetre ou fais Ctrl+C.
echo       Dans Telegram, envoie /start a ton bot.
echo.
".venv\Scripts\python.exe" -m signalbot
echo.
echo  [!] Le bot s'est arrete. Si tu vois une erreur plus haut,
echo      prends une capture d'ecran de cette fenetre.
pause
exit /b 0

:install_error
echo.
echo  [X] L'installation a echoue. Prends une capture d'ecran de cette fenetre.
pause
exit /b 1
