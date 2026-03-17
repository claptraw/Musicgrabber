@echo off
setlocal EnableDelayedExpansion

title MusicGrabber Setup

echo.
echo  =============================================
echo   MusicGrabber - Windows Setup
echo  =============================================
echo.

:: -------------------------------------------------------
:: 1. Check for Docker
:: -------------------------------------------------------
echo [1/4] Checking for Docker...
docker --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Docker Desktop is not installed.
    echo  Downloading Docker Desktop installer...
    echo.
    set DOCKER_INSTALLER=%TEMP%\DockerDesktopInstaller.exe
    curl -L -o "%DOCKER_INSTALLER%" "https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe"
    if errorlevel 1 (
        echo  Download failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    echo  Launching installer...
    echo.
    echo  After installing Docker Desktop:
    echo    1. Reboot if prompted
    echo    2. Start Docker Desktop and wait for it to finish loading
    echo    3. Run this setup script again
    echo.
    start /wait "" "%DOCKER_INSTALLER%"
    pause
    exit /b 1
)
echo  Docker found.

:: -------------------------------------------------------
:: 2. Check Docker is actually running
:: -------------------------------------------------------
echo [2/4] Checking Docker is running...
docker info >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Docker is installed but not running.
    echo  Please start Docker Desktop, wait for it to finish loading
    echo  (the whale icon in the system tray should stop animating),
    echo  then run this script again.
    echo.
    pause
    exit /b 1
)
echo  Docker is running.

:: -------------------------------------------------------
:: 3. Choose music folder
:: -------------------------------------------------------
echo [3/4] Setting up folders...
echo.
echo  Where would you like MusicGrabber to save your music?
echo  Press Enter to use the default: %USERPROFILE%\Music\MusicGrabber
echo.
set /p MUSIC_PATH="  Music folder path: "
if "%MUSIC_PATH%"=="" set MUSIC_PATH=%USERPROFILE%\Music\MusicGrabber

:: Strip any trailing backslash
if "%MUSIC_PATH:~-1%"=="\" set MUSIC_PATH=%MUSIC_PATH:~0,-1%

:: Create folders
set DATA_PATH=%APPDATA%\MusicGrabber
if not exist "%MUSIC_PATH%" (
    mkdir "%MUSIC_PATH%"
    echo  Created music folder: %MUSIC_PATH%
)
if not exist "%DATA_PATH%" (
    mkdir "%DATA_PATH%"
    echo  Created data folder: %DATA_PATH%
)

:: Write docker-compose.yml into the data folder
echo  Writing configuration...
(
    echo services:
    echo   music-grabber:
    echo     image: g33kphr33k/musicgrabber:latest
    echo     container_name: music-grabber
    echo     restart: unless-stopped
    echo     shm_size: '2gb'
    echo     ports:
    echo       - "38274:8080"
    echo     volumes:
    echo       - "%MUSIC_PATH%:/music"
    echo       - "%DATA_PATH%:/data"
    echo     environment:
    echo       - MUSIC_DIR=/music
    echo       - DB_PATH=/data/music_grabber.db
) > "%DATA_PATH%\docker-compose.yml"

echo  Configuration saved to: %DATA_PATH%\docker-compose.yml

:: -------------------------------------------------------
:: 4. Pull the image
:: -------------------------------------------------------
echo [4/4] Downloading MusicGrabber (this may take a few minutes)...
echo.
docker pull g33kphr33k/musicgrabber:latest
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to pull MusicGrabber image.
    echo  Check your internet connection and try again.
    echo.
    pause
    exit /b 1
)

:: -------------------------------------------------------
:: Done
:: -------------------------------------------------------
echo.
echo  =============================================
echo   Setup complete!
echo  =============================================
echo.
echo  Music will be saved to: %MUSIC_PATH%
echo  Settings are stored in: %DATA_PATH%
echo.
echo  To start MusicGrabber, run: run.bat
echo  (copy run.bat to your Desktop for easy access)
echo.

:: Copy run.bat to data folder for convenience
copy /Y "%~dp0run.bat" "%DATA_PATH%\run.bat" >nul 2>&1
copy /Y "%~dp0stop.bat" "%DATA_PATH%\stop.bat" >nul 2>&1

set /p LAUNCH="  Start MusicGrabber now? (Y/N): "
if /i "%LAUNCH%"=="Y" (
    cd /d "%DATA_PATH%"
    docker compose up -d
    timeout /t 3 /nobreak >nul
    start http://localhost:38274
)

echo.
pause
