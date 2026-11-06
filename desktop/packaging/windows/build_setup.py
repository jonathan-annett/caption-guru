"""Build CaptionGuru-Setup.cmd: install.ps1 wrapped in a double-clickable .cmd.

The .cmd copies the PowerShell after its ``::PS1::`` marker to %TEMP% and runs
it, so testers download one file. Used by .github/workflows/installer.yml.

    python desktop/packaging/windows/build_setup.py [out.cmd]
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = "::PS1::"

HEADER = r"""@echo off
rem Caption Guru setup for Windows. Double-click to install (no admin needed).
rem Optional: CaptionGuru-Setup.cmd -Ref feat/sherpa-onnx-streaming
rem Everything below the marker line is the PowerShell installer (install.ps1).
setlocal
set "CG_SELF=%~f0"
set "CG_PS1=%TEMP%\caption-guru-setup-%RANDOM%.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:CG_SELF); $m='::'+'PS1::'; $i=$t.IndexOf($m); if ($i -lt 0) { exit 1 }; [IO.File]::WriteAllText($env:CG_PS1, $t.Substring($i + $m.Length))"
if errorlevel 1 (
  echo Couldn't unpack the installer. Please download it again.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%CG_PS1%" %*
set "CG_RC=%errorlevel%"
del "%CG_PS1%" >nul 2>&1
if not "%CG_RC%"=="0" (
  echo.
  echo Installation failed - see the message above.
)
echo.
pause
exit /b %CG_RC%
"""


def build() -> str:
    ps1 = (HERE / "install.ps1").read_text(encoding="utf-8")
    if MARKER in ps1:
        raise SystemExit(f"install.ps1 must not contain {MARKER!r}")
    if any(ord(c) > 127 for c in ps1):
        raise SystemExit("install.ps1 must be ASCII (Windows PowerShell 5.1 reads it as ANSI)")
    text = HEADER + MARKER + "\n" + ps1.replace("\r\n", "\n")
    return text.replace("\n", "\r\n")  # cmd.exe wants CRLF


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dist" / "CaptionGuru-Setup.cmd"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build().encode("ascii"))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
