@echo off
REM Entry point used by Windows Task Scheduler.
REM Schedules to register:
REM   - At log on
REM   - Every 15 minutes
REM   - On workstation lock
setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%"
python capture.py
popd
endlocal
