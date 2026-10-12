"""python tests/test_notes.py [pure] [exe] [app]      (no args = pure app)
pure: note files, the delete guard, the Recycle Bin.
update: offering, downloading (from a local server) and swapping in a new version, all in temp folders.
exe:  the built dist\\SayNote\\SayNote.exe: it starts, the hotkey flips the title, a second launch exits. Run .\\build.ps1 first.
      Presses Ctrl+Alt+N for real, but only while SayNote's own window is in front; otherwise those checks are skipped.
app:  the real window, hotkey thread and Whisper in this process. Fires the hotkey by posting WM_HOTKEY to the hotkey thread
      (no key presses) and swaps what the mic heard for a sentence from Windows' built-in TTS; nothing is played.
Screenshots use PrintWindow (SayNote's own pixels, never other apps) and go to %TEMP%\\saynote-shots.
Everything uses a temp notes folder. Quit a running SayNote first: they share the hotkey. 'exe' must run before 'app'."""
import ctypes, json, os, shutil, subprocess, sys, tempfile, threading, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # window rectangles and screenshots in real pixels
import win32gui, win32process, win32ui
from PIL import Image
import saynote
from common import FAILS, check, tts

user32 = ctypes.windll.user32
SHOTS = Path(tempfile.gettempdir()) / "saynote-shots"


def wait(cond, secs=30, what="condition"):
    end = time.time() + secs
    while time.time() < end:
        if v := cond():
            return v
        time.sleep(0.1)
    raise TimeoutError(f"timed out after {secs}s waiting for {what}")


def fire_hotkey(app):
    user32.PostThreadMessageW(app.hotkey_thread, 0x0312, 1, 0)  # WM_HOTKEY id 1, what Windows sends for Ctrl+Alt+N


def tap_hotkey():
    n = saynote.parse_hotkey(saynote.DEFAULTS["hotkey"])[2]
    for vk in (0x11, 0x12, n):  # Ctrl, Alt, N
        user32.keybd_event(vk, 0, 0, 0)
    for vk in (n, 0x12, 0x11):
        user32.keybd_event(vk, 0, 2, 0)


def windows_of(pid):
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] == pid
                         and win32gui.GetWindowText(h).startswith("SayNote") else None, None)
    return found


def shot(hwnd, name):
    """The window's own pixels, even when other apps cover it."""
    SHOTS.mkdir(exist_ok=True)
    path = SHOTS / f"{name}.png"
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    hdc = win32gui.GetWindowDC(hwnd)
    dc = win32ui.CreateDCFromHandle(hdc)
    mem = dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(dc, r - l, b - t)
    mem.SelectObject(bmp)
    user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT: includes the WebView2 content
    info = bmp.GetInfo()
    Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1).save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    print(f"        screenshot {path}")


def test_pure(tmp):
    saynote.NOTES = Path(tmp) / "pure"
    t = datetime(2026, 10, 11, 9, 0, 0)
    a, b = saynote.save_note("first", t), saynote.save_note("second", t)
    check("same second never overwrites", (a.name, b.name), ("2026-10-11 09-00-00.txt", "2026-10-11 09-00-01.txt"))
    saynote.save_note("older", datetime(2025, 1, 1))
    notes = saynote.list_notes()
    check("newest first", [n["text"] for n in notes], ["second", "first", "older"])
    check("note fields", notes[0], {"id": "2026-10-11 09-00-01", "created": int(datetime(2026, 10, 11, 9, 0, 1).timestamp() * 1000), "text": "second"})
    outside = Path(tmp) / "outside.txt"
    outside.write_text("keep me")
    check("edit keeps the note's name, so its date", (saynote.edit_note(b.stem, "  changed\nline two "), b.read_text(encoding="utf-8"),
                                                       saynote.list_notes()[0]["id"]), (True, "changed\nline two\n", b.stem))
    check("edit refuses names outside the notes folder, and text that isn't text",
          [saynote.edit_note("../outside", "x"), saynote.edit_note("..\\outside", "x"), saynote.edit_note(b.stem, None), outside.read_text()],
          [False, False, False, "keep me"])
    mine = saynote.NOTES / "groceries.txt"  # a note you added yourself is dated by its file time: editing keeps it
    mine.write_text("eggs", encoding="utf-8")
    os.utime(mine, (1.7e9, 1.7e9))
    check("edit keeps the date of a note not named by date", (saynote.edit_note("groceries", "eggs, milk"), mine.stat().st_mtime), (True, 1.7e9))
    mine.write_bytes(b"caf\xe9")  # cp1252, not UTF-8
    check("edit refuses a note in another encoding instead of mangling it", (saynote.edit_note("groceries", "x"), mine.read_bytes()), (False, b"caf\xe9"))
    mine.unlink()
    check("delete refuses names outside the notes folder", [saynote.delete_note(i) for i in ("../outside", "..\\outside", "nope")], [False] * 3)
    check("delete moves the note away", (saynote.delete_note("2026-10-11 09-00-00"), a.exists(), outside.exists()), (True, False, True))
    check("wav fallback", saynote.save_audio(saynote.np.zeros(16000, "float32"), t).stat().st_size, 44 + 32000)
    log, saynote.LOG = saynote.LOG, Path(tmp) / "appdata" / "saynote.log"
    saynote.NOTES = outside / "notes"  # under a file: can't be created, like an offline OneDrive folder
    check("wav goes next to the log when the notes folder can't be written", saynote.save_audio(saynote.np.zeros(16000, "float32"), t).parent, saynote.LOG.parent)
    saynote.NOTES, saynote.LOG = Path(tmp) / "pure", log
    k32 = ctypes.WinDLL("kernel32")
    k32.CreateFileW.restype = ctypes.c_void_p
    held = k32.CreateFileW(str(b), 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, no sharing: as if another program had it open
    try:
        check("a note that can't be read doesn't hide the others", [n["text"] for n in saynote.list_notes()], ["older"])
    finally:
        k32.CloseHandle(ctypes.c_void_p(held))

    check("shortcuts are written one way", [saynote.parse_hotkey(k) for k in ("ctrl+alt+n", "F9", "win+shift+space")],
          [("Ctrl+Alt+N", 0x4003, 0x4E), ("F9", 0x4000, 0x78), ("Shift+Win+Space", 0x400C, 0x20)])
    check("shortcuts that typing would set off, or that aren't keys, are refused",
          [saynote.parse_hotkey(k) for k in ("Shift+A", "N", "Ctrl+Ctrl+A", "Ctrl+Alt+Esc", "Ctrl+Alt+", "Hyper+A", None, "Ctrl+F25")], [None] * 8)
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    check("SayNote's language list is Whisper's", saynote.LANGUAGES, list(_LANGUAGE_CODES))
    saynote.SETTINGS = Path(tmp) / "settings" / "settings.json"
    check("no settings file: the defaults", saynote.load_settings(), saynote.DEFAULTS)
    want = {"popup": False, "hotkey": "Ctrl+Shift+Space", "mic": "Microphone (X)", "language": "vi"}
    saynote.save_settings(want)
    check("settings come back as saved", saynote.load_settings(), want)
    for junk in ('{"popup": "no", "hotkey": "Shift+A", "mic": 3, "language": "xx"}', "not json", "[1, 2]"):
        saynote.SETTINGS.write_text(junk)
        check(f"settings file {junk[:20]!r}: the defaults", saynote.load_settings(), saynote.DEFAULTS)
    mics = saynote.list_mics()
    print(f"        microphones: {[m['name'] for m in mics]}")
    check("microphones have an id and a full name", all(set(m) == {"id", "name"} and m["name"].startswith(m["id"].split(" #")[0]) for m in mics), True)
    check("every microphone can be chosen", len({m["id"] for m in mics}), len(mics))
    check("the tray's recording icon can be made at tray sizes", all(saynote.recording_icon(s) for s in (16, 20, 24, 28, 32)), True)


def test_update(tmp):
    import functools, hashlib, http.server, zipfile
    real = saynote.REPO, saynote.VERSION
    saynote.REPO, saynote.VERSION = "me/SayNote", "1.2"
    try:
        _test_update(tmp)
    finally:
        saynote.REPO, saynote.VERSION = real


def _test_update(tmp):
    import functools, hashlib, http.server, zipfile
    url = "https://github.com/me/SayNote/releases/download/v1.3/SayNote-1.3.zip"
    release = {"tag_name": "v1.3", "body": " Pinned notes. ", "assets": [{"name": "SayNote-1.3.zip", "browser_download_url": url, "digest": "sha256:AB"}]}
    check("a newer release is offered", saynote.newer_release(release), {"version": "1.3", "notes": "Pinned notes.", "url": url, "sha256": "ab"})
    check("versions compare as numbers", (saynote.version_tuple("v1.10") > saynote.version_tuple("1.9"), saynote.version_tuple("beta")), (True, ()))
    not_offered = [dict(release, tag_name="v1.2"), dict(release, tag_name="v1.1"), dict(release, tag_name="nightly"), dict(release, assets=[]),
                   dict(release, assets=[dict(release["assets"][0], browser_download_url="https://evil.example/SayNote-1.3.zip")])]
    check("same, older, odd, zip-less or foreign releases are not offered", [saynote.newer_release(r) for r in not_offered], [None] * 5)

    # download_update against a local server: a good zip unpacks to SayNote\SayNote.exe, a damaged one is refused
    site = Path(tmp) / "site"
    site.mkdir()
    with zipfile.ZipFile(site / "good.zip", "w") as z:
        z.writestr("SayNote/SayNote.exe", "new exe")
        z.writestr("SayNote/_internal/ui/index.html", "new page")
    good = hashlib.sha256((site / "good.zip").read_bytes()).hexdigest()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.func.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base, seen = f"http://127.0.0.1:{server.server_port}", []
    try:
        folder = saynote.download_update({"url": f"{base}/good.zip", "sha256": good}, seen.append)
        check("a download unpacks to a SayNote folder", ((folder / "SayNote.exe").read_text(), seen[-1]), ("new exe", 1.0))
        try:
            saynote.download_update({"url": f"{base}/good.zip", "sha256": "0" * 64})
            damaged = "accepted"
        except OSError as e:
            damaged = str(e)
        check("a damaged download is refused", "damaged" in damaged, True)
    finally:
        server.shutdown()

    # update.ps1 swaps folders, and puts the old one back when it can't
    def run_update(to, frm):
        return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "update.ps1"), "-From", str(frm),
                               "-To", str(to), "-Version", "9.9", "-NoStart"], capture_output=True, timeout=120).returncode
    app, new = Path(tmp) / "Programs" / "SayNote", Path(tmp) / "new" / "SayNote"
    for folder, text in ((app, "old"), (new, "new")):
        (folder / "_internal").mkdir(parents=True)
        (folder / "SayNote.exe").write_text(text)
    run_update(app, new)
    check("update.ps1 swaps in the new version and removes the old", ((app / "SayNote.exe").read_text(), new.exists(), app.with_name("SayNote.old").exists()),
          ("new", False, False))
    (new / "_internal").mkdir(parents=True)
    (new / "SayNote.exe").write_text("newer")
    k32 = ctypes.WinDLL("kernel32")
    k32.CreateFileW.restype = ctypes.c_void_p
    held = k32.CreateFileW(str(app / "SayNote.exe"), 0x80000000, 1, None, 3, 0x80, None)  # open like a running exe: no delete, no rename
    try:
        run_update(app, new)
    finally:
        k32.CloseHandle(ctypes.c_void_p(held))
    check("update.ps1 leaves the old version in place when it can't move it", ((app / "SayNote.exe").read_text(), (new / "SayNote.exe").exists()), ("new", True))


def test_exe(tmp):
    exe = ROOT / "dist" / "SayNote" / "SayNote.exe"
    env = dict(os.environ, SAYNOTE_DIR=str(Path(tmp) / "exe"), SAYNOTE_SETTINGS=str(Path(tmp) / "exe-settings.json"))
    p = subprocess.Popen([str(exe)], env=env)
    try:
        hwnd = wait(lambda: (windows_of(p.pid) or [None])[0], 60, "the exe's window")
        time.sleep(2)  # page and hotkey thread up
        shot(hwnd, "exe-idle")
        if user32.GetForegroundWindow() != hwnd:  # injected keys go to whatever is in front, which must never be someone else's app
            print("SKIP  exe hotkey checks: another app has the foreground, so no keys were pressed")
        else:
            tap_hotkey()
            check("exe: hotkey starts listening", wait(lambda: win32gui.GetWindowText(hwnd) == "SayNote - Listening", 10, "Listening title"), True)
            shot(hwnd, "exe-recording")
            time.sleep(1)
            if user32.GetForegroundWindow() == hwnd:
                tap_hotkey()
                check("exe: second press saves and goes back to idle", wait(lambda: win32gui.GetWindowText(hwnd) == "SayNote", 120, "idle title"), True)
        second = subprocess.run([str(exe)], env=env, timeout=60)
        check("exe: a second launch exits and the first keeps running", (second.returncode, p.poll()), (0, None))
        subprocess.run([str(exe), "--quit"], env=env, timeout=60)  # what the uninstaller does
        check("exe: --quit closes the running SayNote", wait(lambda: p.poll() is not None, 30, "SayNote to quit"), True)
    finally:
        p.kill()


class Muted(list):
    def append(self, _):  # the live mic keeps calling back; ignore it so only the synthetic clip is transcribed
        pass


def test_app(tmp):
    from faster_whisper.audio import decode_audio
    import faster_whisper
    saynote.NOTES, saynote.SETTINGS = Path(tmp) / "app", Path(tmp) / "app-settings.json"
    played, notified, pushed = [], [], []
    saynote.play = played.append  # the start and stop sounds: listed, not played
    wavs = tts(["Buy milk and call the dentist on Friday."])
    clip = decode_audio(os.path.join(wavs, "Zira_0.wav"), sampling_rate=16000)
    seen = {}

    def drive(app):
        try:
            js = app.window.evaluate_js
            wait(lambda: app.hwnd and app.hotkey_thread and app.model_ready.is_set() and app.icon, 60, "window, hotkey thread and speech model")
            app.icon.notify = lambda *a: notified.append(a)  # Windows notifications: listed, not shown
            push = app.push
            app.push = lambda m: (pushed.append(m), push(m))[1]
            if problem := app.get_state()["problem"]:
                return seen.setdefault("problem", problem)
            time.sleep(1)
            seen["empty"] = js("document.body.innerText")
            shot(app.hwnd, "app-empty")
            fire_hotkey(app)
            wait(lambda: app.stream is not None, 10, "recording")
            time.sleep(1.5)
            seen["recording"] = (app.window.title, js("document.body.innerText"))
            shot(app.hwnd, "app-recording")
            app.buf = Muted([clip[:, None]])
            fire_hotkey(app)
            wait(lambda: not (app.stream or app.busy), 120, "transcription")
            time.sleep(1)
            seen["saved"] = (app.window.title, js("document.body.innerText"))
            shot(app.hwnd, "app-saved")
            seen["notes"] = saynote.list_notes()
            # Fix a word in the page's editor, as typing would: it saves to the file on its own
            js("const b = document.getElementById('r-body'); b.focus(); b.textContent = 'Buy oat milk and call the dentist on Friday.';"
               "b.dispatchEvent(new InputEvent('input', {bubbles: true}))")
            seen["edited"] = wait(lambda: [n["text"] for n in saynote.list_notes()] == ["Buy oat milk and call the dentist on Friday."], 10, "the edit to reach the file")
            seen["edited name"] = [n["id"] for n in saynote.list_notes()] == [n["id"] for n in seen["notes"]]
            js("document.getElementById('delete').click()")  # the page's Delete button -> api.delete_note -> Recycle Bin
            wait(lambda: not saynote.list_notes(), 10, "the note to leave the folder")
            time.sleep(0.5)
            seen["deleted"] = js("document.body.innerText")

            # Discard, then Undo: the recording comes back as a note
            fire_hotkey(app)
            wait(lambda: app.stream is not None, 10, "recording")
            app.buf = Muted([clip[:, None]])
            seen["discard"] = (app.discard(), app.stream, app.busy, saynote.list_notes(), app.get_state()["state"])
            seen["undo"] = app.undo_discard()
            wait(lambda: not app.busy, 120, "transcription after Undo")
            seen["undone"] = [n["text"] for n in saynote.list_notes()]
            seen["undo twice"] = app.undo_discard()

            # Shortcuts: a new one works, one another program holds is refused and the old one stays
            r = app.set_setting("hotkey", "ctrl+alt+shift+f11")
            seen["hotkey"] = (r["ok"], app.get_state()["hotkey"], app.hotkey_ok, saynote.load_settings()["hotkey"])
            fire_hotkey(app)
            seen["new hotkey records"] = wait(lambda: app.stream is not None, 10, "recording on the new shortcut")
            app.discard()
            held = user32.RegisterHotKey(None, 7, 0x4007, 0x79)  # Ctrl+Alt+Shift+F10, held by this thread like another program would
            r = app.set_setting("hotkey", "Ctrl+Alt+Shift+F10")
            user32.UnregisterHotKey(None, 7)
            seen["taken"] = (bool(held), r["ok"], bool(r["error"]), app.get_state()["hotkey"], app.hotkey_ok)
            seen["refused"] = [app.set_setting("hotkey", k)["ok"] for k in ("Shift+A", "", None, 5)]
            app.set_setting("hotkey", "Ctrl+Alt+N")

            # Settings and microphones
            s = app.get_settings()
            seen["settings"] = (sorted(s), s["version"], s["popup"], s["mic"])
            seen["bad settings"] = [app.set_setting(k, v)["ok"] for k, v in (("popup", "yes"), ("mic", "No such mic"), ("nope", 1), ("language", "xx"))]
            if s["mics"]:
                app.set_setting("mic", s["mics"][0]["id"])
                fire_hotkey(app)
                seen["chosen mic records"] = wait(lambda: app.stream is not None, 10, "recording on the chosen mic")
                app.discard()
                app.set_setting("mic", None)

            # Popup off: no window, a sound and a red dot in the tray instead
            app.set_setting("popup", False)
            played.clear()
            fire_hotkey(app)
            wait(lambda: app.stream is not None, 10, "recording")
            seen["red dot"] = wait(lambda: app.icon._icon_handle == app.tray_icons[1] and app.icon.title == "SayNote - Listening", 5, "the red dot")
            seen["told"] = [a[0] for a in notified]
            app.discard()
            seen["dot gone"] = wait(lambda: app.icon._icon_handle == app.tray_icons[0], 5, "the normal tray icon")
            copies = [user32.CopyIcon(ctypes.c_void_p(h)) for h in app.tray_icons]  # fails on a destroyed icon
            seen["tray icons alive"] = all(copies)
            for c in filter(None, copies):
                user32.DestroyIcon(ctypes.c_void_p(c))
            seen["sounds"] = [c is saynote.START_CUE for c in played]
            app.set_setting("popup", True)

            # A language whose model can't be downloaded: back to the old one, with a message
            real = faster_whisper.WhisperModel
            def offline(*_, **__):
                time.sleep(1)
                raise OSError("offline")
            faster_whisper.WhisperModel = offline
            try:
                r = app.set_setting("language", "vi")
                seen["language loading"] = (r["ok"], app.get_state()["state"])
                wait(lambda: app.model_ready.is_set(), 10, "the failed model download")
                seen["language back"] = (app.settings["language"], app.loaded[1], saynote.load_settings()["language"], app.get_state()["problem"])
            finally:
                faster_whisper.WhisperModel = real
            js("location.href = 'about:blank'")  # what a link or file dropped on the window does
            time.sleep(2)
            seen["href"] = js("location.href")
            res = ctypes.c_size_t(9)  # Windows asks every window before a restart; ctypes lets go of the GIL the handler needs
            ctypes.WinDLL("user32").SendMessageTimeoutW(ctypes.c_void_p(app.hwnd), 0x0011, 0, 0, 0x2, 5000, ctypes.byref(res))  # WM_QUERYENDSESSION
            seen["endsession"] = res.value
        except Exception as e:
            seen["error"] = repr(e)
        finally:
            app.quit()

    saynote.run(then=drive)
    shutil.rmtree(wavs, ignore_errors=True)
    check("app: no error or problem", (seen.get("error"), seen.get("problem")), (None, None))
    check("app: empty state shows the hotkey", "Ctrl+Alt+N" in seen.get("empty", ""), True)
    title, text = seen.get("recording", ("", ""))
    check("app: recording title and instruction", (title, "again to save" in text), ("SayNote - Listening", True))
    notes = seen.get("notes", [])
    said = notes[0]["text"].lower() if notes else ""
    print(f"        heard: {said!r}")
    check("app: one note with what was said", (len(notes), "milk" in said and "dentist" in said), (1, True))
    title, text = seen.get("saved", ("", ""))
    check("app: page shows the new note, title back to idle", (title, "milk" in text.lower()), ("SayNote", True))
    check("app: Delete button removes the note from disk and page", "milk" in seen.get("deleted", "milk").lower(), False)
    check("app: the page can't be navigated away", seen.get("href", "").startswith("http://127.0.0.1:"), True)
    check("app: doesn't hold up a Windows restart or sign-out", seen.get("endsession"), 1)
    check("app: editing in the page saves to the note's own file", (seen.get("edited"), seen.get("edited name")), (True, True))
    check("app: Discard stops without a note", seen.get("discard"), (True, None, False, [], "idle"))
    check("app: Undo saves the thrown-away recording, once", (seen.get("undo"), any("milk" in t.lower() for t in seen.get("undone", [])),
                                                              seen.get("undo twice")), (True, True, False))
    check("app: a new shortcut is registered, saved and records", (seen.get("hotkey"), seen.get("new hotkey records")),
          ((True, "Ctrl+Alt+Shift+F11", True, "Ctrl+Alt+Shift+F11"), True))
    check("app: a shortcut another program holds is refused, the old one stays", seen.get("taken"), (True, False, True, "Ctrl+Alt+Shift+F11", True))
    check("app: shortcuts that aren't allowed are refused", seen.get("refused"), [False] * 4)
    check("app: settings", seen.get("settings"), (["autostart", "hotkey", "language", "languages", "mic", "mics", "popup", "version"],
                                                 saynote.VERSION, True, None))
    check("app: wrong settings are refused", seen.get("bad settings"), [False] * 4)
    if "chosen mic records" in seen:
        check("app: records from the chosen microphone", seen["chosen mic records"], True)
    check("app: popup off: red dot and tooltip while listening, start sound", (seen.get("red dot"), seen.get("dot gone"), seen.get("sounds")),
          (True, True, [True]))
    check("app: both tray icons are still alive (pystray must not destroy them)", seen.get("tray icons alive"), True)
    print(f"        Windows notifications it would have shown: {notified}")
    check("app: a language that can't be downloaded goes back to English and says so",
          (seen.get("language loading"), seen.get("language back"), any(m["type"] == "message" and "download" in m["text"] for m in pushed)),
          ((True, "loading"), ("en", "en", "en", None), True))


if __name__ == "__main__":
    which = sys.argv[1:] or ["pure", "update", "app"]
    tmp = tempfile.mkdtemp(prefix="saynote-test-")
    try:
        for name in ("pure", "update", "exe", "app"):  # exe before app: the in-process app keeps the single-instance event open
            if name in which:
                print(f"--- {name}")
                globals()[f"test_{name}"](tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nall passed")
    sys.exit(bool(FAILS))
