## Caption Guru installers

### Windows

**Download [`CaptionGuru-Setup.cmd`](https://github.com/jonathan-annett/caption-guru/releases/download/installer/CaptionGuru-Setup.cmd) and double-click it.** Nothing else needs to be installed first, and no admin rights are needed. Windows only asks permission if it has to add the Microsoft VC++ runtime or WebView2.

It asks which version to install: **main** (stable), or a branch or tag for beta testing. It then downloads and sets up everything in `%LOCALAPPDATA%\CaptionGuru` and opens a **Caption Guru** folder on your Desktop with the shortcuts.

- Run it again at any time to switch version or update.
- To skip the menu: `CaptionGuru-Setup.cmd -Ref feat/sherpa-onnx-streaming`
- To uninstall: use the *Uninstall Caption Guru* shortcut.

**If Windows warns about the download:** in the browser choose *Keep*. If SmartScreen says "Windows protected your PC", click *More info → Run anyway*. The file is a plain text script, so you can open it in Notepad to see exactly what it does.

`install.ps1` and `install.cmd` are the same installer as two separate files, for people who prefer that.

### macOS and Linux

Open **Terminal** and paste:

```
curl -fsSL https://github.com/jonathan-annett/caption-guru/releases/download/installer/install.sh | bash
```

It asks which version to install, the same way as the Windows installer, then sets everything up in `~/.caption-guru` without needing admin rights. It puts a **Caption Guru** folder on your Desktop and opens it. Double-click a launcher there to start; on macOS it opens in Terminal.

- **macOS:** the first time captions start, macOS asks to let Terminal use the microphone. Allow it.
- **Linux:** live captions need PortAudio (`sudo apt install libportaudio2` on Debian/Ubuntu). Captions show in Chrome/Chromium kiosk mode, or your default browser if neither is installed.
- To skip the menu: `curl -fsSL …/install.sh | bash -s -- --ref feat/sherpa-onnx-streaming`
- To uninstall: use *Uninstall Caption Guru* in the Desktop folder.
