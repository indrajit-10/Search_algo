@echo off
REM run.bat  --  one entry point, in its own environment. Windows.
REM
REM   run.bat            start the browser interface  (http://localhost:8000)
REM   run.bat test       every test suite, in order
REM   run.bat search     interactive search prompt
REM   run.bat compare    old Sphinx pipeline vs new, side by side
REM   run.bat evaluate   replay the query log through both engines
REM   run.bat audit      measure the old system's failures from your export
REM   run.bat doctor     check the environment without running anything
REM
REM There are no dependencies to install - every import is standard library.
REM The virtualenv exists to isolate from a broken host Python, not to hold
REM packages. See run.sh for the full reasoning.

setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"
set "CMD=%~1"
if "%CMD%"=="" set "CMD=serve"

REM ---- find an interpreter -------------------------------------------------
set "PYTHON="
for %%P in (py python3 python) do (
    if not defined PYTHON (
        %%P -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,7) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON=%%P"
    )
)
if not defined PYTHON (
    echo No Python 3.7 or newer found on PATH.
    echo   Install from https://www.python.org/downloads/
    echo   Tick "Add Python to PATH" in the installer.
    echo   Everything here is standard library, so any 3.7+ will do.
    exit /b 1
)

REM ---- build the environment on first run ----------------------------------
if not exist "%VENV%\Scripts\python.exe" (
    echo First run: creating an isolated environment in .venv\
    %PYTHON% -m venv "%VENV%" >nul 2>&1
    if errorlevel 1 (
        echo   could not create one. Falling back to %PYTHON% directly.
        echo   Nothing needs installing, so this works exactly the same.
        echo.
    )
)

if exist "%VENV%\Scripts\python.exe" (
    set "PY=%VENV%\Scripts\python.exe"
) else (
    set "PY=%PYTHON%"
)

REM ---- is there an export? -------------------------------------------------
if /i not "%CMD%"=="doctor" (
    if not exist "data\card_database.csv" (
        if not exist "data\card_database.csv.gz" (
            if not exist "data\card_database.zip" (
                echo No card export found.
                echo.
                echo   Copy your export in:
                echo       copy C:\path\to\card_database.csv data\
                echo.
                echo   Then run this again. CSV please, not xlsx - Excel reformats
                echo   the dates and mangles the apostrophes this depends on.
                exit /b 1
            )
        )
    )
)

REM -E ignores PYTHON* env vars, -s ignores user site-packages.
if /i "%CMD%"=="doctor"   ( "%PY%" -V & echo environment: %PY% & goto :eof )
if /i "%CMD%"=="test" (
    echo =========== 1/3  engine tests ===========
    "%PY%" -E -s search_engine.py <nul
    echo.
    echo =========== 2/3  edge cases =============
    "%PY%" -E -s test_edge_cases.py
    echo.
    echo =========== 3/3  query log replay =======
    "%PY%" -E -s evaluate.py
    goto :eof
)
if /i "%CMD%"=="search"   ( "%PY%" -E -s search_engine.py & goto :eof )
if /i "%CMD%"=="compare"  ( "%PY%" -E -s search_engine.py --compare & goto :eof )
if /i "%CMD%"=="evaluate" ( "%PY%" -E -s evaluate.py & goto :eof )
if /i "%CMD%"=="edge"     ( "%PY%" -E -s test_edge_cases.py & goto :eof )
if /i "%CMD%"=="audit"    ( "%PY%" -E -s audit_catalogue.py & goto :eof )
if /i "%CMD%"=="serve"    ( "%PY%" -E -s serve.py & goto :eof )

echo Unknown command: %CMD%
echo Try: run.bat        (interface)
echo      run.bat test   (all suites)
exit /b 1
