@echo off
setlocal
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
set "REPO=%HERE%\..\..\.."
where python >nul 2>nul
if %errorlevel%==0 (
  set "PY=python"
) else (
  set "PY=py -3"
)
%PY% "%REPO%\scripts\make_bundle_preview.py" --bundle-dir "%HERE%"
if errorlevel 1 (
  echo.
  echo Failed.
  pause
  exit /b 1
)
echo.
echo Created "%HERE%\preview.png"
pause
