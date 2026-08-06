@echo off
REM ===========================================================================
REM run.bat  --  one entry point, in its own environment. Windows.
REM
REM From PowerShell or cmd, inside the repo folder:
REM
REM   .\run.bat            start the browser interface  (http://localhost:8000)
REM   .\run.bat test       every test suite, in order
REM   .\run.bat search     interactive search prompt
REM   .\run.bat compare    old Sphinx pipeline vs new, side by side
REM   .\run.bat evaluate   replay the query log through both engines
REM   .\run.bat audit      measure the old system's failures from your export
REM   .\run.bat doctor     check the environment without running anything
REM
REM In PowerShell the leading .\ is required. `run.bat` alone will not be found.
REM
REM There are no dependencies to install - every import is standard library.
REM The virtualenv exists to isolate from a broken host Python, not to hold
REM packages. See run.sh for the full reasoning.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "CMD=%~1"
if "%CMD%"=="" set "CMD=serve"

REM ---------------------------------------------------------------- find python
REM `py` is the Windows launcher and is the most reliable of the three, so it
REM goes first. Each candidate is asked its own version rather than trusted.
set "PYTHON="
for %%P in (py python3 python) do (
    if not defined PYTHON (
        %%P -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,7) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON=%%P"
    )
)

if not defined PYTHON (
    echo.
    echo No Python 3.7 or newer found on PATH.
    echo.
    echo   Install it from https://www.python.org/downloads/
    echo   Tick "Add python.exe to PATH" in the installer, then open a NEW
    echo   PowerShell window so it picks up the change.
    echo.
    echo   Everything here is standard library, so any 3.7+ will do.
    echo.
    exit /b 1
)

REM ------------------------------------------------------- build env on first run
if not exist "%VENV%\Scripts\python.exe" (
    echo First run: creating an isolated environment in .venv\
    %PYTHON% -m venv "%VENV%" >nul 2>&1
    if exist "%VENV%\Scripts\python.exe" (
        echo   done
    ) else (
        echo   could not create one. Falling back to %PYTHON% directly.
        echo   Nothing needs installing, so this behaves identically -
        echo   you only lose the isolation.
        echo.
    )
)

if exist "%VENV%\Scripts\python.exe" (
    set "PY=%VENV%\Scripts\python.exe"
) else (
    set "PY=%PYTHON%"
)

REM Everything is invoked through :run below, which keeps "%PY%" quoted -
REM the repo path may well contain a space, and an unquoted interpreter path
REM fails in a way that looks like Python is missing rather than misquoted.
REM -E ignores PYTHON* environment variables, -s ignores user site-packages,
REM so another project's PYTHONPATH cannot shadow a standard library module.

REM --------------------------------------------------------------------- doctor
if /i "%CMD%"=="doctor" (
    echo python      !PY!
    for /f "delims=" %%V in ('!PY! -V 2^>^&1') do echo version     %%V
    if exist "%VENV%\Scripts\python.exe" (
        echo environment .venv ^(isolated^)
    ) else (
        echo environment system interpreter ^(no venv^)
    )
    if exist "data\card_database.csv" (
        echo export      data\card_database.csv
    ) else (
        echo export      MISSING - see below
        call :nodata
    )
    goto :done
)

REM ------------------------------------------------------------- need an export
if not exist "data\card_database.csv" if not exist "data\card_database.csv.gz" if not exist "data\card_database.zip" (
    call :nodata
    exit /b 1
)

REM -------------------------------------------------------------------- dispatch
if /i "%CMD%"=="test" (
    echo =========== 1/3  engine tests ===========
    "%PY%" -E -s search_engine.py <nul
    echo.
    echo =========== 2/3  edge cases =============
    call :run test_edge_cases.py
    echo.
    echo =========== 3/3  query log replay =======
    call :run evaluate.py
    goto :done
)
if /i "%CMD%"=="search"   ( call :run search_engine.py & goto :done )
if /i "%CMD%"=="compare"  ( call :run search_engine.py --compare & goto :done )
if /i "%CMD%"=="evaluate" ( call :run evaluate.py & goto :done )
if /i "%CMD%"=="edge"     ( call :run test_edge_cases.py & goto :done )
if /i "%CMD%"=="audit"    ( call :run audit_catalogue.py & goto :done )
if /i "%CMD%"=="serve"    ( call :run serve.py & goto :done )
if /i "%CMD%"=="help"     ( goto :usage )
if /i "%CMD%"=="-h"       ( goto :usage )
if /i "%CMD%"=="--help"   ( goto :usage )

echo Unknown command: %CMD%
goto :usage

REM ------------------------------------------------------------------ subroutines
:run
"%PY%" -E -s %*
goto :eof

:nodata
echo.
echo No card export found in data\
echo.
echo   Copy your export into the data folder. Use YOUR real path -
echo   from PowerShell, in this folder:
echo.
echo       copy C:\Users\you\Downloads\card_database.csv data\
echo.
echo   or just drag the file into the data\ folder in Explorer.
echo.
echo   CSV please, not xlsx. Excel reformats the dates and mangles the
echo   apostrophes this depends on.
echo.
goto :eof

:usage
echo.
echo   .\run.bat            the browser interface  ^(http://localhost:8000^)
echo   .\run.bat test       all three suites
echo   .\run.bat compare    old pipeline vs new
echo   .\run.bat search     interactive prompt
echo   .\run.bat evaluate   replay the query log
echo   .\run.bat audit      measure the old system
echo   .\run.bat doctor     check the environment
echo.
exit /b 1

:done
endlocal
