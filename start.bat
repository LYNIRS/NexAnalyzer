@echo off
title NexAnalyzer Launcher
echo ============================================
echo   NexAnalyzer - Nexstrom Data Analyzer
echo ============================================
echo.

:: Anchor to this script's folder so paths are stable regardless of how it's launched
cd /d "%~dp0"

:: One definition of the port, used by the stale-server check, the message
:: printed to the user, and the server itself.
set "APP_PORT=8501"

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: ============================================
:: Auto-update: pull the latest version from GitHub (never blocks launch)
:: ============================================
echo Checking for updates...

:: Is Git available?
git --version >nul 2>&1
if errorlevel 1 (
    echo [SKIP] Git not found - cannot auto-update. Launching current version.
    echo        Install Git from https://git-scm.com/downloads to enable updates.
    echo.
    goto :after_update
)

:: Is this a real git checkout? This script sits at the repo root, next to .git.
if not exist ".git" (
    echo [SKIP] Not a git checkout - cannot auto-update. Launching current version.
    echo        Tip: 'git clone' the repo instead of using a ZIP to get auto-updates.
    echo.
    goto :after_update
)

:: Fast-forward only: if local work has diverged, fail cleanly and keep the
:: current version rather than creating a merge commit.
:: Pinned to "origin main" explicitly rather than a bare "git pull" -- a bare
:: pull follows whatever branch.main.remote happens to be set to, and that can
:: silently drift to a different remote (e.g. a personal fork added later),
:: which makes updates go quiet with no error.
git pull --ff-only origin main
if errorlevel 1 goto :update_failed
echo Up to date with the latest version.
echo.
goto :after_update

:update_failed
:: Separate the two real causes. The common one is local edits to tracked files:
:: editing material presets in-app rewrites data\materials.json, which is
:: committed and shared, so git refuses to overwrite it. Reported as a generic
:: warning, that silently pins the user to an old version forever.
git diff --quiet HEAD
if errorlevel 1 goto :update_blocked
echo [WARN] Could not update - offline, or no access to the repo.
echo        Continuing with the current version.
echo.
goto :after_update

:update_blocked
echo [WARN] Update BLOCKED by your local changes to these tracked files:
git --no-pager diff --name-only HEAD
echo.
echo        You will keep launching an OLD version until this is resolved.
echo        Set the changes aside and relaunch:  git stash
echo        To get them back afterwards:         git stash pop
echo        See USER_GUIDE.md ^(Troubleshooting^) for details.
echo.

:after_update

:: Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Virtual environment created.
    echo.
)

:: Activate virtual environment
call venv\Scripts\activate.bat

:: Install/update dependencies (runs every launch, so any pulled requirement
:: changes are picked up automatically)
echo Checking dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo Dependencies are up to date.
echo.

:: ============================================
:: Stale-server check
:: ============================================
:: A server started before the update above is still running the code it
:: imported at ITS launch, and Streamlit does not reload already-imported
:: modules. Worse, on Windows a second Streamlit binds the same port without
:: error, and incoming requests are then split between the two servers
:: unpredictably -- so clicking this launcher with an old copy still running
:: could serve either version, at random, from the URL printed below.
:: Free the port first, or say so plainly.
echo Checking for an already-running NexAnalyzer...

:: netstat lists the same PID twice (IPv4 and IPv6), so keep only the first.
set "STALE_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":%APP_PORT% " ^| findstr /I "LISTENING"') do (
    if not defined STALE_PID set "STALE_PID=%%P"
)

if not defined STALE_PID (
    echo Port %APP_PORT% is free.
    echo.
    goto :launch
)

:: Identify it before touching it: something other than NexAnalyzer may own
:: this port, and killing a stranger's process is not this script's business.
set "STALE_IMAGE=unknown"
for /f "tokens=1 delims=," %%I in ('tasklist /FI "PID eq %STALE_PID%" /NH /FO CSV 2^>nul') do set "STALE_IMAGE=%%~I"

echo.
echo [!] Something is already serving http://localhost:%APP_PORT%
echo     PID %STALE_PID%  (%STALE_IMAGE%)
echo.

echo %STALE_IMAGE% | findstr /I /C:"python" /C:"streamlit" >nul
if errorlevel 1 goto :foreign_owner

echo     That is an older NexAnalyzer. It is still running the version it
echo     started with, NOT the version just downloaded. Leaving it up means
echo     the browser may show either one.
echo.
choice /C YN /N /T 15 /D Y /M "Stop it and start the updated version? [Y/n]  (Y in 15s) "
if errorlevel 2 goto :keep_old

echo.
echo Stopping PID %STALE_PID%...
taskkill /PID %STALE_PID% /T /F >nul 2>&1

:: Killing the listener takes its parent launcher with it, but the port can
:: take a moment to come free. Launching before it does would recreate the
:: double-bind this check exists to prevent.
set /a PORT_WAIT=0
:wait_for_port
netstat -ano | findstr /C:":%APP_PORT% " | findstr /I "LISTENING" >nul
if errorlevel 1 goto :port_released
set /a PORT_WAIT+=1
if %PORT_WAIT% GEQ 10 goto :port_stuck
timeout /t 1 /nobreak >nul
goto :wait_for_port

:port_released
echo Stopped. Port %APP_PORT% is free.
echo.
goto :launch

:port_stuck
echo [WARN] Port %APP_PORT% is still in use after 10 seconds.
echo        Close every NexAnalyzer window and run this launcher again.
echo        Starting anyway would serve an unpredictable mix of versions.
echo.
pause
exit /b 1

:keep_old
echo.
echo Left the running copy alone. It is at http://localhost:%APP_PORT%
echo and may be an OLDER version than the files on disk.
echo To use the updated version, close that window and run this launcher again.
echo.
pause
exit /b 0

:foreign_owner
echo     That is not a Python process, so this launcher will not stop it.
echo     Close whatever owns port %APP_PORT%, or start NexAnalyzer on another
echo     port with:  streamlit run app.py --server.port 8601
echo.
pause
exit /b 1

:: ============================================
:: Launch the app
:: ============================================
:launch
echo Starting NexAnalyzer...
echo The app will open in your browser at http://localhost:%APP_PORT%
echo Press Ctrl+C in this window to stop the server.
echo.
:: Port pinned explicitly so the check above, the URL printed here and the
:: server all refer to the same one.
streamlit run app.py --server.port %APP_PORT%
