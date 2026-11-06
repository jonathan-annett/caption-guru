## Caption Guru for Windows: setup

**Download [`CaptionGuru-Setup.cmd`](https://github.com/jonathan-annett/caption-guru/releases/download/installer/CaptionGuru-Setup.cmd) and double-click it.** Nothing else needs to be installed first, and no admin rights are needed. Windows only asks permission if it has to add the Microsoft VC++ runtime or WebView2.

It asks which version to install: **main** (stable), or a branch or tag for beta testing. It then downloads and sets up everything in `%LOCALAPPDATA%\CaptionGuru` and opens a **Caption Guru** folder on your Desktop with the shortcuts.

- Run it again at any time to switch version or update.
- To skip the menu: `CaptionGuru-Setup.cmd -Ref feat/sherpa-onnx-streaming`
- To uninstall: use the *Uninstall Caption Guru* shortcut.

**If Windows warns about the download:** in the browser choose *Keep*. If SmartScreen says "Windows protected your PC", click *More info → Run anyway*. The file is a plain text script, so you can open it in Notepad to see exactly what it does.

`install.ps1` and `install.cmd` are the same installer as two separate files, for people who prefer that.
