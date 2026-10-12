# SayNote: voice notes for Windows

Press **Ctrl+Alt+N** anywhere, talk, press **Ctrl+Alt+N** again. The window pops up while it listens, then saves what you said as a note. Open SayNote from Start any time to read old notes, newest first. Closing the window keeps it running in the tray (right-click the tray icon: Open, Start with Windows, Quit).

In the window: search your notes (Ctrl+F), click into a note to fix what it heard (it saves on its own), Esc or Discard throws a recording away (Undo brings it back). **Settings** (the gear, or Ctrl+,): bring the window to the front or stay in the background (a sound and a red dot on the tray icon instead, for games), the shortcut, the microphone, the language, Start with Windows, Check for updates. When a new version is out, an **Update** card appears at the bottom left. Remove SayNote from **Settings > Apps** like any other app; your notes stay.

Speech is turned into text on your PC by [faster-whisper](https://github.com/SYSTRAN/faster-whisper); nothing you say leaves it.

```
.\build.ps1                                          # build SayNote.exe, install to %LOCALAPPDATA%\Programs\SayNote, add it to Start
.\release.ps1 "What changed"                         # publish the build as a GitHub release: installed copies offer it as an update
.\.venv\Scripts\pythonw.exe saynote.py               # or run from source, no build
.\.venv\Scripts\python.exe tests\test_notes.py       # checks: note files, updates, the real window, hotkey thread and Whisper
.\.venv\Scripts\python.exe tests\test_notes.py exe   # checks the built dist\SayNote\SayNote.exe
```

Setup: Windows 10/11, Python 3.11, then `py -3.11 -m venv .venv` and `.\.venv\Scripts\pip install -r requirements.txt`.

- Notes are plain `.txt` files in **Documents\SayNote**, one per note, named by date and time. Delete in the app sends them to the Recycle Bin. You can edit or delete the files yourself; the app re-reads them when its window gets focus.
- If a recording can't be transcribed, it is kept as a `.wav` next to the notes instead of being lost (next to the log if the notes folder can't be written; also when Windows shuts down mid-recording).
- Speech model: `small.en` for English, on the CPU, offline after the first download (about 460 MB). Any other language, or Detect automatically, uses the multilingual `small` (about 480 MB, downloaded the first time you pick one). `model_name()` in `saynote.py`.
- Settings live in `%LOCALAPPDATA%\SayNote\settings.json` (Start with Windows is the registry's Run key). Tests point `SAYNOTE_SETTINGS` and `SAYNOTE_DIR` at a temp folder.
- **Updates** come from this repo's GitHub releases (`REPO` in `saynote.py`). SayNote checks soon after it starts, then daily. Update downloads `SayNote-<version>.zip`, checks it against GitHub's checksum, and quits; `update.ps1` swaps the new folder in, puts the old one back if that fails, and starts SayNote again. To publish: raise `VERSION` in `saynote.py`, run `.\build.ps1`, then `.\release.ps1 "What changed"` (with the GitHub CLI signed in; without it, the script says what to upload on github.com).
- `uninstall.ps1` is copied next to the exe and is what Settings > Apps runs. It quits SayNote (`SayNote.exe --quit`, which saves a recording in progress), then removes the app, the Start Menu shortcut, Start with Windows, `settings.json` and the log. Notes and any kept `.wav` files stay, and so does the speech model in the Hugging Face cache.
- The page is `ui/index.html` (one file). `ui/preview.js` documents the page/Python contract and previews the page in a browser: open `ui/index.html?preview&state=recording&theme=dark` (also `&update`, `&empty`, `&problem`).
- Errors go to `%LOCALAPPDATA%\SayNote\saynote.log`.
- The app is about 250 MB (Python, Whisper runtime, WebView2 bridge). The model lives in your Hugging Face cache, not in the app.
