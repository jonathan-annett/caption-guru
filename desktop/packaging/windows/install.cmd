@echo off
rem Caption Guru - Windows installer. Double-click to install (no admin needed).
rem Optional: install.cmd -Ref feat/sherpa-onnx-streaming
set "PS1=%~dp0install.ps1"
if not exist "%PS1%" (
  echo Downloading the installer...
  set "PS1=%TEMP%\caption-guru-install.ps1"
  powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing https://github.com/jonathan-annett/caption-guru/releases/download/installer/install.ps1 -OutFile $env:TEMP\caption-guru-install.ps1"
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
if errorlevel 1 (
  echo.
  echo Installation failed - see the message above.
)
echo.
pause
