@echo off
:: CAMA remote launcher (2026-09-01). Starts CAMA over Streamable HTTP on
:: 127.0.0.1:8765 and an ngrok tunnel in front of it so Claude's custom
:: connector (web + phone) can reach it.
::
:: Secret path: ~\.cama\http_secret.txt   (delete it to rotate; re-add connector after)
:: Static domain: ~\.cama\ngrok_domain.txt (one line, e.g. yourname.ngrok-free.app)
::   Claim a free static domain at https://dashboard.ngrok.com/domains so the
::   connector URL survives restarts. Without it ngrok picks a new random
::   hostname every run and the connector has to be re-added.
::
:: Connector URL = https://<domain>/<secret>/mcp
setlocal
cd /d %~dp0

set CAMA_TRANSPORT=http
set CAMA_PORT=8765
set CAMA_HOST=127.0.0.1
set NGROK=%LOCALAPPDATA%\ngrok\ngrok.exe
set SECRET_FILE=%USERPROFILE%\.cama\http_secret.txt
set DOMAIN_FILE=%USERPROFILE%\.cama\ngrok_domain.txt

if not exist "%NGROK%" (
  echo ngrok not found at %NGROK%
  pause
  exit /b 1
)

echo Starting CAMA HTTP server on %CAMA_HOST%:%CAMA_PORT% ...
start "CAMA remote (HTTP)" .venv\Scripts\python.exe cama_mcp.py

:: Wait for the secret file (created on first server start) and the port.
set /a tries=0
:waitloop
if exist "%SECRET_FILE%" goto haveSecret
set /a tries+=1
if %tries% geq 60 (
  echo Server did not create %SECRET_FILE% in time. Check the CAMA window.
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul
goto waitloop

:haveSecret
set /p SECRET=<"%SECRET_FILE%"
set NGROK_DOMAIN=
if exist "%DOMAIN_FILE%" set /p NGROK_DOMAIN=<"%DOMAIN_FILE%"

echo.
if defined NGROK_DOMAIN (
  echo Connector URL:  https://%NGROK_DOMAIN%/%SECRET%/mcp
  echo Health check:   https://%NGROK_DOMAIN%/healthz
  echo.
  echo Keep this window open. Ctrl+C stops the tunnel.
  "%NGROK%" http --url=%NGROK_DOMAIN% %CAMA_PORT%
) else (
  echo No static domain in %DOMAIN_FILE%
  echo The connector URL is  https://^<hostname shown below^>/%SECRET%/mcp
  echo and it will change every restart until you save a static domain there.
  echo.
  "%NGROK%" http %CAMA_PORT%
)
