"""SayNote: press Ctrl+Alt+N anywhere, talk, press it again. What you said is saved as a note.
The window (ui/index.html in WebView2) shows your notes; closing it keeps SayNote running in the tray.
Notes are plain .txt files in Documents\\SayNote, one per note, named by date and time. Windows only, CPU only.
Dev run: .venv\\Scripts\\pythonw.exe saynote.py    Build and install the app: .\\build.ps1"""
import ctypes, hashlib, io, json, logging, os, queue, re, shutil, string, subprocess, sys, tempfile, threading, time, urllib.request, wave, winreg, zipfile
from contextlib import suppress
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pystray
import sounddevice as sd
import webview
from PIL import Image, ImageDraw

VERSION = "1.2"
REPO = "TonyLikeDev/SayNote"  # owner/name of the GitHub repo whose releases are SayNote's updates (release.ps1 publishes them). Empty: no updates
THREADS, RATE = 4, 16000
MIN_SECS, MAX_SECS = 0.4, 30 * 60  # shorter is a key bump; longer is a forgotten recording, stopped and saved
LEVEL_FLOOR_DB, LEVEL_SPAN_DB = -55, 50  # mic meter: -55 dB shows as silence, -5 dB as full. Calibration knob for quiet or hot mics
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MOD_KEYS = {"Ctrl": 0x2, "Alt": 0x1, "Shift": 0x4, "Win": 0x8}  # RegisterHotKey's MOD_ flags, in the order a shortcut is written
WM_HOTKEY, WM_APP = 0x0312, 0x8000
# Whisper's languages (faster_whisper.tokenizer._LANGUAGE_CODES), copied so the window doesn't wait for that import. A test checks it
LANGUAGES = ("af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw he hi hr ht hu hy id is it ja "
             "jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq "
             "sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo zh yue").split()

user32, shell32 = ctypes.WinDLL("user32"), ctypes.windll.shell32  # own user32 instance: argtypes here don't leak into pystray's
user32.LoadImageW.restype = user32.CreateIconFromResourceEx.restype = user32.GetForegroundWindow.restype = wintypes.HANDLE
user32.CreateIconFromResourceEx.argtypes = [ctypes.c_char_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
for f in (user32.IsIconic, user32.SetForegroundWindow):
    f.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateEventW.restype = kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]


def _documents():
    buf = ctypes.create_unicode_buffer(260)
    shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # CSIDL_PERSONAL: follows OneDrive and folder redirection
    return Path(buf.value or Path.home() / "Documents")


HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # bundled files live in _MEIPASS in the built app
NOTES = Path(os.environ.get("SAYNOTE_DIR") or _documents() / "SayNote").absolute()  # the Recycle Bin call needs a full path
LOG = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SayNote" / "saynote.log"
SETTINGS = Path(os.environ.get("SAYNOTE_SETTINGS") or LOG.parent / "settings.json")
DEFAULTS = {"popup": True, "hotkey": "Ctrl+Alt+N", "mic": None, "language": "en"}


def save_note(text, when):
    """Write a new note file. Never overwrites: a name already taken moves to the next second."""
    NOTES.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            with open(NOTES / f"{when:%Y-%m-%d %H-%M-%S}.txt", "x", encoding="utf-8") as f:
                f.write(text + "\n")
            return Path(f.name)
        except FileExistsError:
            when += timedelta(seconds=1)


def note_dict(p, text=None):
    try:
        t = datetime.strptime(p.stem, "%Y-%m-%d %H-%M-%S")
    except ValueError:  # a file you added or renamed yourself
        t = datetime.fromtimestamp(p.stat().st_mtime)
    text = p.read_text(encoding="utf-8", errors="replace").strip() if text is None else text
    return {"id": p.stem, "created": int(t.timestamp() * 1000), "text": text}


def list_notes():
    """-> [{id, created, text}], newest first. A note that can't be read right now (locked by another program, or online-only
    in OneDrive while offline) is left out instead of hiding all the others."""
    notes = []
    for p in NOTES.glob("*.txt"):
        try:
            notes.append(note_dict(p))
        except OSError as e:
            logging.warning("can't read %s: %s", p, e)
    return sorted(notes, key=lambda n: -n["created"])


def write_file(path, text):
    """Write all of it to a .tmp next to it, on disk, then swap it in: a crash or power cut never leaves half a file."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            st = path.stat()  # keep its date: a note not named by date is dated by this
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _note_path(note_id):
    """The file for an id from the page, only if it's a plain name inside the notes folder."""
    p = NOTES / f"{note_id}.txt"
    return p if p.parent == NOTES and p.is_file() else None


def edit_note(note_id, text):
    """Replace a note's text. It keeps its file name, so its date."""
    p = _note_path(note_id)
    if not p or not isinstance(text, str):
        return False
    try:
        p.read_bytes().decode("utf-8")  # a file you added in another encoding: saving would turn its accents into question marks
        write_file(p, text.strip() + "\n")
        return True
    except (OSError, UnicodeError):
        logging.exception("edit %s", p)
        return False


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


def delete_note(note_id):
    """Move a note to the Recycle Bin."""
    p = _note_path(note_id)
    if not p:
        return False
    # FO_DELETE with ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI | WANTNUKEWARNING (asks before a permanent delete)
    op = _SHFILEOPSTRUCTW(wFunc=3, pFrom=str(p) + "\0", fFlags=0x40 | 0x10 | 0x4 | 0x400 | 0x4000)
    return shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted and not p.exists()


def save_audio(audio, when):
    """Last resort when there is no note: keep the recording as a .wav next to the notes, or next to the log when the notes
    folder can't be written (offline OneDrive, full disk). -> its path"""
    for folder in (NOTES, LOG.parent):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{when:%Y-%m-%d %H-%M-%S}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
            return path
        except OSError:
            logging.exception("save audio in %s", folder)
    raise OSError("nowhere to keep the recording")


def model_name(lang):
    return "small.en" if lang == "en" else "small"  # English has its own, more accurate model; every other language shares one


def transcribe(model, audio, lang):
    segs, _ = model.transcribe(audio, language=None if lang == "auto" else lang, vad_filter=True, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip()


def parse_hotkey(name):
    """'ctrl+alt+n' -> ('Ctrl+Alt+N', RegisterHotKey modifiers, virtual key), or None if SayNote can't use it: Ctrl, Alt, Shift and
    Win at most once each, then A-Z, 0-9, F1-F24 or Space. Needs Ctrl, Alt or Win unless it's an F key, so typing never sets it off."""
    if not isinstance(name, str):
        return None
    *mods, key = name.split("+")
    mods = [m.capitalize() for m in mods]
    key = key.upper() if len(key) == 1 else key.capitalize()
    if len(set(mods)) != len(mods) or not set(mods) <= MOD_KEYS.keys():
        return None
    if fkey := re.fullmatch(r"F([1-9]|1\d|2[0-4])", key):
        vk = 0x6F + int(fkey[1])
    elif key == "Space":
        vk = 0x20
    elif len(key) == 1 and key in string.ascii_uppercase + string.digits:
        vk = ord(key)
    else:
        return None
    if not fkey and not {"Ctrl", "Alt", "Win"} & set(mods):
        return None
    return "+".join([m for m in MOD_KEYS if m in mods] + [key]), sum(MOD_KEYS[m] for m in mods) | 0x4000, vk  # MOD_NOREPEAT


def load_settings():
    """The saved settings over the defaults. Anything missing, unreadable or of the wrong kind stays at its default."""
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    s = dict(DEFAULTS)
    if isinstance(saved, dict):
        if isinstance(saved.get("popup"), bool):
            s["popup"] = saved["popup"]
        if hotkey := parse_hotkey(saved.get("hotkey")):
            s["hotkey"] = hotkey[0]
        if isinstance(saved.get("mic"), str):
            s["mic"] = saved["mic"]
        if saved.get("language") == "auto" or saved.get("language") in LANGUAGES:
            s["language"] = saved["language"]
    return s


def save_settings(s):
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        write_file(SETTINGS, json.dumps(s, indent=2))
    except OSError:
        logging.exception("save settings")  # still in effect until SayNote restarts


def refresh_audio():
    """PortAudio reads the devices and the Windows default once, when it starts: restart it to see what changed since. Only under
    App.lock with no recording open. About 30 ms here.
    ponytail: sounddevice's private _terminate/_initialize (0.5.6), since it has no public call for this"""
    try:
        sd.stop()  # closes the last sound's stream through sounddevice; restarting would free it under sounddevice's feet
        if sd._initialized:  # a failed start last time must not stop this one from trying again
            sd._terminate()
        sd._initialize()
    except Exception:
        logging.exception("restart PortAudio")


def mic_ids():
    """[(device index, id, name)] for the microphones on PortAudio's default host API (MME, which opens any of them at 16 kHz).
    MME cuts names at 31 characters: id is that name, with ' #2', ' #3' on repeats, and name is the full one from another host
    API when there is one."""
    devices = sd.query_devices()
    full = list(dict.fromkeys(d["name"] for d in devices if d["max_input_channels"] > 0))
    seen, mics = {}, []
    for i in sd.query_hostapis(sd.default.hostapi)["devices"]:
        cut = devices[i]["name"]
        # "Microsoft Sound Mapper" is MME's stand-in for the Windows default, which the page already offers
        if devices[i]["max_input_channels"] > 0 and "Sound Mapper" not in cut:
            n = seen[cut] = seen.get(cut, 0) + 1
            longer = [f for f in full if f.startswith(cut) and len(f) > len(cut)]
            mics.append((i, cut + (f" #{n}" if n > 1 else ""), longer[n - 1] if len(longer) >= n else cut))
    return mics


def list_mics():
    return [{"id": mic_id, "name": name} for _, mic_id, name in mic_ids()]


def _tone(*hz, secs=0.07, rate=48000):
    t = np.arange(int(secs * rate)) / rate
    return (np.concatenate([np.sin(2 * np.pi * f * t) * np.hanning(t.size) for f in hz]) * 0.2).astype("float32")


# Played when SayNote starts and stops listening while its window stays in the background: up, then down.
# ponytail: the start sound also reaches the mic as the recording begins; Whisper's speech filter drops it. Delay the stream if not
START_CUE, STOP_CUE = _tone(660, 880), _tone(880, 660)


def play(cue):
    try:
        sd.play(cue, 48000)
    except Exception:
        logging.exception("sound")


def recording_icon(size):
    """The tray icon at the tray's own size with a red dot: SayNote is listening. -> icon handle"""
    ico = Image.open(HERE / "saynote.ico")
    img = (ico.ico.getimage((size, size)) if (size, size) in ico.info["sizes"]
           else ico.ico.getimage((256, 256)).resize((size, size), Image.LANCZOS)).convert("RGBA")
    s = size * 4  # drawn at 4x, then scaled down: round edges even at 16 px
    dot = Image.new("RGBA", (s, s))
    draw = ImageDraw.Draw(dot)
    draw.ellipse((s * .4, s * .4, s, s), fill="white")  # a ring that keeps the dot apart from the blue page
    draw.ellipse((s * .5, s * .5, s * .92, s * .92), fill="#E81123")
    img.alpha_composite(dot.resize((size, size), Image.LANCZOS))
    png = io.BytesIO()
    img.save(png, "PNG")
    return user32.CreateIconFromResourceEx(png.getvalue(), len(png.getvalue()), True, 0x30000, size, size, 0)


def version_tuple(v):
    """'v1.2' -> (1, 2). Anything that isn't a version -> ()"""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(v or "").strip())
    return tuple(map(int, m[1].split("."))) if m else ()


def newer_release(release):
    """GitHub's 'latest release' JSON -> {version, notes, url, sha256} when it is newer than this SayNote and has its
    SayNote-<version>.zip from this repo, else None."""
    v = version_tuple(release.get("tag_name"))
    if not v or v <= version_tuple(VERSION):
        return None
    version = ".".join(map(str, v))
    asset = next((a for a in release.get("assets") or [] if a.get("name") == f"SayNote-{version}.zip"), None)
    url = str((asset or {}).get("browser_download_url", ""))
    if not url.startswith(f"https://github.com/{REPO}/releases/download/"):
        return None
    digest = str(asset.get("digest") or "")  # 'sha256:...' on releases GitHub has checksummed
    return {"version": version, "notes": str(release.get("body") or "").strip()[:2000], "url": url,
            "sha256": digest[7:].lower() if digest.startswith("sha256:") else None}


def _get(url, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"SayNote/{VERSION}",
                                                                        "Accept": "application/vnd.github+json"}), timeout=timeout)


def latest_release():
    with _get(f"https://api.github.com/repos/{REPO}/releases/latest", 15) as r:
        return newer_release(json.load(r))


def download_update(release, progress=lambda done: None):
    """Download and unpack a release into a fresh folder in %TEMP%. -> the new SayNote folder (the one with SayNote.exe)"""
    root = Path(tempfile.gettempdir()) / "SayNote-update"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    zipped, sha = root / "update.zip", hashlib.sha256()
    with _get(release["url"], 30) as r, open(zipped, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            progress(done / total if total else 0)
    if release["sha256"] and sha.hexdigest() != release["sha256"]:
        raise OSError("the download was damaged. Try again")
    with zipfile.ZipFile(zipped) as z:
        z.extractall(root)  # zipfile keeps entries inside root, whatever their names say
    zipped.unlink()
    if not (root / "SayNote" / "SayNote.exe").is_file():
        raise OSError("the download doesn't contain SayNote")
    return root / "SayNote"


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve()}" --background'


def autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, "SayNote")[0] == autostart_command()
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "SayNote", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, "SayNote")
            except FileNotFoundError:
                pass


def dark_mode():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def open_folder():
    NOTES.mkdir(parents=True, exist_ok=True)
    os.startfile(NOTES)


class App:
    def __init__(self, hidden=False):
        # Guards stream/busy/quitting: the hotkey, the page, the tray and the window can all act at once. Reentrant because
        # push_state takes it too. Nothing may wait on the window's thread while holding it: that thread takes it at shutdown
        self.lock = threading.RLock()
        # One settings change at a time. Taken before self.lock, never while holding it: a hotkey change waits on the hotkey
        # thread, which may be waiting for self.lock in toggle()
        self.settings_lock = threading.Lock()
        self.outbox = queue.Queue()  # updates for the page, sent in order by _pump
        self.settings, self.mics = load_settings(), []
        # loaded: (model, the language it's set up for), one attribute so a transcription never sees a model with another's language
        self.loaded, self.model_ready, self.model_problem = (None, None), threading.Event(), None
        self.stream, self.busy, self.buf, self.started, self.level, self.discarded = None, False, [], None, 0.0, None
        self.quitting, self.told_tray, self.icon, self.tray_icons, self.hwnd = False, False, None, (None, None), None
        self.hotkey_ok, self.hotkey_thread, self.hotkey_done, self.picking = True, None, threading.Event(), False
        self.update, self.update_progress, self.told_update = None, None, None  # a newer release on GitHub; download progress
        dark = dark_mode()
        self.window = webview.create_window(
            "SayNote", str(HERE / "ui" / "index.html"), width=1000, height=660, min_size=(720, 480),
            hidden=hidden, background_color="#202020" if dark else "#F3F3F3")
        # What the page can call, as plain functions named as in ui/preview.js. Not a js_api object: pywebview lets the page
        # call any dotted path under that, underscores included, so everything the object led to would be reachable
        self.window.expose(self.get_state, list_notes, self.toggle, self.discard, self.undo_discard, edit_note, delete_note,
                           open_folder, self.get_settings, self.set_setting, self.pick_hotkey, self.check_update, self.install_update)
        self.window.events.before_show += self.hook_window
        self.window.events.shown += lambda: self.titlebar(dark)

    def hook_window(self):
        """On the window's thread, before it shows or loads anything. Native events, because pywebview's closing event doesn't
        say why, and pywebview lets the page be navigated away: a link or file dropped on the window would load in its place
        and get the page's API."""
        form = self.window.native
        form.FormClosing += self.on_closing
        form.FormClosed += self.on_closed
        home = self.window.real_url.rsplit("/", 1)[0] + "/"  # http://127.0.0.1:<port>/, pywebview's server for ui/

        def stay_home(_, args):
            if not str(args.Uri).startswith(home):
                args.Cancel = True
        form.browser.webview.NavigationStarting += stay_home

    # ---- runs once the window exists
    def start(self):
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self.listen_hotkey, daemon=True).start()
        threading.Thread(target=self.load_model, daemon=True).start()
        threading.Thread(target=self.watch_updates, daemon=True).start()
        menu = pystray.Menu(
            pystray.MenuItem("Open SayNote", lambda: self.show(), default=True),
            pystray.MenuItem("Start with Windows", lambda: set_autostart(not autostart()), checked=lambda _: autostart()),
            pystray.MenuItem("Quit", lambda: self.quit()))
        self.icon = pystray.Icon("SayNote", Image.open(HERE / "saynote.ico"), f"SayNote ({self.settings['hotkey']})", menu)
        # ponytail: pystray loads its icon at 32 px and lets Windows shrink it, blurring the hand-tuned small frames. Give it the
        # .ico's own tray-sized frame through a private attribute (pystray 0.19.5); drop this if pystray grows a sharper path
        size = user32.GetSystemMetrics(49)  # SM_CXSMICON
        try:
            busy = recording_icon(size)
        except Exception:
            logging.exception("recording icon")
            busy = None
        self.tray_icons = (user32.LoadImageW(None, str(HERE / "saynote.ico"), 1, size, size, 0x10) or None, busy or None)  # IMAGE_ICON, LR_LOADFROMFILE
        self.icon._icon_handle = self.tray_icons[0]
        self.icon._icon_valid = True  # or pystray's first show destroys that handle and loads its own
        self.icon.run_detached()

    def titlebar(self, dark):
        """Make the native title bar match the page: dark caption text and buttons, and the page's own base colour."""
        try:
            self.hwnd = self.window.native.Handle.ToInt64()
            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute(self.hwnd, 20, ctypes.byref(ctypes.c_int(dark)), 4)  # DWMWA_USE_IMMERSIVE_DARK_MODE
            dwm.DwmSetWindowAttribute(self.hwnd, 35, ctypes.byref(ctypes.c_uint(0x202020 if dark else 0xF3F3F3)), 4)  # DWMWA_CAPTION_COLOR
        except Exception:
            logging.exception("title bar")

    def listen_hotkey(self):
        """WM_HOTKEY goes to the thread that registered it, so this thread owns the hotkey: it registers it, and again on WM_APP
        when the shortcut changes in Settings."""
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # gives this thread its message queue before anyone posts to it
        self.hotkey_thread = kernel32.GetCurrentThreadId()  # tests post WM_HOTKEY here instead of pressing keys
        self.register_hotkey()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                if not (self.picking and user32.GetForegroundWindow() == self.hwnd):  # being typed into the Shortcut box
                    self.toggle()
            elif msg.message == WM_APP:
                self.register_hotkey()
                self.hotkey_done.set()

    def register_hotkey(self):
        """On the hotkey thread: (re)register the shortcut in the settings."""
        _, mods, vk = parse_hotkey(self.settings["hotkey"])
        user32.UnregisterHotKey(None, 1)
        self.hotkey_ok = bool(user32.RegisterHotKey(None, 1, mods, vk))
        self.push_state()

    def change_hotkey(self):
        """-> whether the shortcut now in the settings is registered. Waits for the hotkey thread to do it."""
        self.hotkey_done.clear()
        user32.PostThreadMessageW(self.hotkey_thread, WM_APP, 0, 0)
        return self.hotkey_done.wait(5) and self.hotkey_ok

    def load_model(self, fallback=None):
        """Load the speech model for the language in the settings. fallback: the language to go back to if that fails (after a
        change in Settings); None at startup, where a failure is a problem until it's fixed."""
        lang = self.settings["language"]
        try:
            from faster_whisper import WhisperModel
            try:
                model = WhisperModel(model_name(lang), device="cpu", compute_type="int8", cpu_threads=THREADS, local_files_only=True)
            except Exception:  # not downloaded yet (first run on this PC, or a new language): fetch it, about 460 MB
                model = WhisperModel(model_name(lang), device="cpu", compute_type="int8", cpu_threads=THREADS)
            self.loaded, self.model_problem = (model, lang), None
            if fallback is not None:
                with self.settings_lock:
                    self.save()
        except Exception as e:
            logging.exception("speech model")
            if fallback is None or not self.loaded[0]:
                self.model_problem = f"The speech model could not be loaded ({e}). Recordings are kept as .wav files in the notes folder."
            else:
                with self.settings_lock:
                    self.settings["language"] = fallback
                self.say("Couldn't download the speech model for that language. Check your internet connection and try again.", "error")
        self.model_ready.set()
        self.push_state()

    # ---- talking to the page
    def get_state(self):
        state = ("recording" if self.stream else "transcribing" if self.busy
                 else "idle" if self.model_ready.is_set() else "loading")
        started = int(self.started.timestamp() * 1000) if self.stream else None
        hotkey = self.settings["hotkey"]
        problem = self.model_problem or (None if self.hotkey_ok else f"{hotkey} is taken by another program, so the hotkey won't "
                                         "work. Pick another shortcut in Settings, or use the Start recording button.")
        return {"state": state, "problem": problem, "hotkey": hotkey, "hotkey_ok": self.hotkey_ok, "started": started,
                "update": self.update and {"version": self.update["version"], "notes": self.update["notes"], "progress": self.update_progress}}

    def get_settings(self):
        with self.lock:  # PortAudio can only restart while no microphone is open
            if not self.stream:
                refresh_audio()
            try:
                self.mics = list_mics()
            except Exception:
                logging.exception("list microphones")
        return self.settings_view()

    def settings_view(self):
        return {**self.settings, "mics": self.mics, "languages": LANGUAGES, "autostart": autostart(), "version": VERSION}

    def set_setting(self, key, value):
        """A change from the page's Settings. -> {ok, error, settings}"""
        with self.settings_lock:
            try:
                error = self._set(key, value)
            except Exception:
                logging.exception("setting %s", key)
                error = "Couldn't change that setting."
            if not error:
                self.save()
        return {"ok": not error, "error": error, "settings": self.settings_view()}

    def save(self):
        """Save the settings, with the language whose model is loaded: one still downloading is saved once it's there, so a
        restart offline mid-download still has a model."""
        save_settings({**self.settings, "language": self.loaded[1] or self.settings["language"]})

    def _set(self, key, value):
        """Apply one setting. -> None, or a sentence saying why not."""
        s = self.settings
        if key in ("popup", "autostart"):
            if not isinstance(value, bool):
                return "That has to be on or off."
            if key == "popup":
                s["popup"] = value
            else:
                set_autostart(value)
                if self.icon:
                    self.icon.update_menu()  # the tray's checkbox
        elif key == "mic":
            if value is not None and value not in [m["id"] for m in self.mics]:
                return "That microphone isn't connected any more."
            s["mic"] = value
        elif key == "hotkey":
            parsed = parse_hotkey(value)
            if not parsed:
                return f"{value} can't be used. Pick Ctrl, Alt or Win with a letter, a number, F1 to F24 or Space, or an F key on its own."
            if not self.hotkey_thread:
                return "Still starting up. Try again in a moment."
            old, s["hotkey"] = s["hotkey"], parsed[0]
            if not self.change_hotkey():
                s["hotkey"] = old
                self.change_hotkey()
                return f"{parsed[0]} is taken by another program. Pick another one."
        elif key == "language":
            if value != "auto" and value not in LANGUAGES:
                return "SayNote doesn't know that language."
            if not self.model_ready.is_set():
                return "Still getting ready. Try again in a moment."
            model, old = self.loaded
            s["language"] = value
            if model and model_name(value) == model_name(old):
                self.loaded = (model, value)
            else:  # another model: the page shows 'loading' and recordings wait for it
                self.model_ready.clear()
                self.push_state()
                threading.Thread(target=self.load_model, args=(old,), daemon=True).start()
        else:
            return f"Unknown setting {key}."

    def pick_hotkey(self, on):
        self.picking = bool(on)

    def push(self, msg):
        """Never waits on the window, so a page that stops answering can't stall the hotkey, the button or Quit."""
        self.outbox.put(msg)

    def _pump(self):
        """Sends the queued updates to the page, the title bar and the tray, one at a time, in order.
        ponytail: if the page's renderer process dies, evaluate_js never returns and this thread stays stuck: the window stops
        updating until SayNote restarts, while recording, saving and Quit keep working. Reload on CoreWebView2.ProcessFailed if it matters"""
        titles = {"recording": "SayNote - Listening", "transcribing": "SayNote - Writing it down"}
        while True:
            msg = self.outbox.get()
            try:
                if msg["type"] == "state":
                    self.window.title = titles.get(msg["state"], "SayNote")
                    self.tray(msg["state"] == "recording", msg["hotkey"])
                self.window.evaluate_js(f"window.SayNote && window.SayNote.update({json.dumps(msg)})")
            except Exception:  # page not loaded yet, or closing: it asks for the state itself when it loads
                pass

    def tray(self, recording, hotkey):
        """The red dot and tooltip in the tray: how you can tell SayNote is listening while its window stays in the background.
        ponytail: swaps the icon under pystray (private _icon_handle and _message, pystray 0.19.5), like start() does"""
        try:
            h = self.tray_icons[recording]
            if self.icon and h and self.icon._icon_handle != h:
                self.icon._icon_handle = h
                self.icon._message(1, 2, hIcon=h)  # NIM_MODIFY, NIF_ICON
            title = "SayNote - Listening" if recording else f"SayNote ({hotkey})"
            if self.icon and self.icon.title != title:
                self.icon.title = title
        except Exception:
            logging.exception("tray")

    def push_state(self):
        with self.lock:  # read and queue in one go, so the last state queued is never older than the last change
            self.push({"type": "state", **self.get_state()})

    # ---- updates
    def watch_updates(self):
        """Look for a newer version soon after starting, then once a day: SayNote can sit in the tray for weeks."""
        if not REPO:
            return
        time.sleep(60)
        while True:
            self.check_update()
            time.sleep(24 * 3600)

    def check_update(self):
        """-> {update, error}: update as in get_state, or None when this is the newest; error: why it couldn't look"""
        if not REPO:
            return {"update": None, "error": "This copy of SayNote has no place to get updates from."}
        try:
            release = latest_release()
        except Exception as e:
            logging.warning("update check: %s", e)
            return {"update": self.get_state()["update"], "error": "Couldn't reach GitHub. Check your internet connection and try again."}
        if self.update_progress is None:  # never swap the release under a download
            self.update = release
        if release and self.told_update != release["version"] and self.icon and user32.GetForegroundWindow() != self.hwnd:
            self.told_update = release["version"]
            self.icon.notify(f"SayNote {release['version']} is available. Open SayNote to update.", "SayNote")
        self.push_state()
        return {"update": self.get_state()["update"], "error": None}

    def install_update(self):
        """Download the newer version, then quit: update.ps1 swaps it in and starts it. -> whether it started"""
        with self.lock:
            if not self.update or self.update_progress is not None:
                return False
            if self.stream or self.busy:
                self.say("Finish your recording first, then update.", "error")
                return False
            if not getattr(sys, "frozen", False):
                self.say("Updates install into the built app. Run .\\build.ps1 to try this one.", "error")
                return False
            self.update_progress = 0
        self.push_state()
        threading.Thread(target=self._install, args=(self.update,), daemon=True).start()
        return True

    def _install(self, release):
        shown = -1

        def progress(done):
            nonlocal shown
            if int(done * 100) != shown:  # a state push per percent, not per chunk
                shown, self.update_progress = int(done * 100), min(done, .99)
                self.push_state()
        try:
            folder = download_update(release, progress)
            script = folder.parent / "update.ps1"  # run from %TEMP%, not from the folder it replaces
            shutil.copyfile(HERE / "update.ps1", script)
            powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            subprocess.Popen([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-From", str(folder),
                              "-To", str(Path(sys.executable).parent), "-ProcessId", str(os.getpid()), "-Version", release["version"]],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            logging.exception("update")
            self.update_progress = None
            self.say(f"Couldn't update: {e}", "error")
            return self.push_state()
        self.update_progress = 1  # the page says SayNote is restarting
        self.push_state()
        time.sleep(1)  # let the page show it
        self.quit()

    def say(self, text, kind="info", undo=False):
        self.push({"type": "message", "text": text, "kind": kind, "undo": undo})
        if self.icon and not self.settings["popup"] and user32.GetForegroundWindow() != self.hwnd:
            self.icon.notify(text, "SayNote")  # the window stays in the background, so the page's own message would go unseen

    # ---- recording
    def toggle(self):
        with self.lock:
            if self.quitting:
                return
            cue = None
            if self.stream:
                self._stop()
                cue = STOP_CUE
            elif self.busy:  # one note at a time: say so, or the next press would start a recording you meant to stop
                self.say("Still writing down your last note. Press again in a moment." if self.model_ready.is_set() else
                         "The speech model for your language is still downloading. Your note will be written when it's ready.", "error")
            else:
                self._start()
                cue = START_CUE if self.stream else None
            if cue is not None and not self.settings["popup"]:
                play(cue)  # under the lock: never while PortAudio restarts
        if self.settings["popup"]:
            self.show()  # after the lock: it waits on the window's thread

    def mic(self):
        """The chosen microphone's device index, or None for the Windows default (also when the chosen one is unplugged)."""
        refresh_audio()  # a mic plugged in, unplugged or made the Windows default since the last recording
        want = self.settings["mic"]
        if want is None:
            return None
        for i, mic_id, _ in mic_ids():
            if mic_id == want:
                return i
        name = next((m["name"] for m in self.mics if m["id"] == want), want)
        self.say(f"{name} isn't connected, so this recording uses the Windows default microphone.", "error")
        return None

    def _start(self):
        self.buf, self.started, self.discarded = [], datetime.now(), None
        try:
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=self.mic(), callback=self._audio)
            self.stream.start()
        except Exception as e:
            if self.stream:
                self.stream.close()
            self.stream = None
            return self.say(f"Microphone problem: {e}", "error")
        self.push_state()
        threading.Thread(target=self._meter, args=(self.stream,), daemon=True).start()

    def _audio(self, data, *_):  # sounddevice's thread
        self.buf.append(data.copy())
        self.level = float(np.sqrt(np.mean(data * data)))

    def _meter(self, stream):
        """While this recording runs: feed the page's level meter, and stop a forgotten recording."""
        while self.stream is stream:
            db = 20 * np.log10(self.level + 1e-9)
            if self.outbox.empty():  # a slow page skips levels instead of falling behind on them
                self.push({"type": "level", "level": round(min(1, max(0, (db - LEVEL_FLOOR_DB) / LEVEL_SPAN_DB)), 3)})
            if datetime.now() - self.started > timedelta(seconds=MAX_SECS):
                with self.lock:
                    if self.stream is stream:
                        self._stop()
            time.sleep(1 / 15)

    def _end_stream(self):
        """Close the mic. -> what it heard."""
        self.stream.stop()
        self.stream.close()
        self.stream = None
        return np.concatenate(self.buf)[:, 0] if self.buf else np.zeros(0, "float32")

    def _stop(self):
        self._save(self._end_stream(), self.started)

    def _save(self, audio, when):
        if len(audio) < MIN_SECS * RATE:
            self.say("Too short, nothing saved.")
            return self.push_state()
        self.busy = True
        self.push_state()
        # daemon=False outright, so quitting waits for it: a thread inherits daemon from its creator, and the hotkey thread is one
        threading.Thread(target=self._write, args=(audio, when), daemon=False).start()

    def discard(self):
        """Stop recording without a note. The audio stays in memory for Undo until the next recording starts."""
        with self.lock:
            if not self.stream:
                return False
            self.discarded = (self._end_stream(), self.started)
            self.push_state()
        self.say("Recording thrown away.", undo=True)
        return True

    def undo_discard(self):
        with self.lock:
            if not self.discarded or self.stream or self.busy or self.quitting:
                return False
            audio, when = self.discarded
            self.discarded = None
            self._save(audio, when)
        return True

    def _write(self, audio, when):
        self.model_ready.wait()  # you may stop talking before the model has finished loading
        note = error = None
        try:
            model, lang = self.loaded
            text = transcribe(model, audio, lang)
            note = note_dict(save_note(text, when), text) if text else None
        except Exception as e:
            logging.exception("transcribe")
            try:
                p = save_audio(audio, when)
                error = f"Could not write the note, so the recording was kept as {p.name} in {'the notes folder' if p.parent == NOTES else p.parent}."
            except Exception:
                error = f"Could not write the note: {e}"
        with self.lock:
            self.busy = False
        if note:
            self.push({"type": "note", "note": note})
        else:
            self.say(error or "Heard nothing, no note saved.", "error" if error else "info")
        self.push_state()
        if self.quitting:
            self.exit()

    # ---- window and tray
    def show(self):
        """Un-hide, un-minimise and bring to the front. Plain Win32 through ctypes, which lets go of the GIL while the window's
        thread does the work. pywebview's on_top setter keeps the GIL from a worker thread and deadlocked against a page update."""
        self.window.show()  # pywebview hands this one to the window's thread properly; it also keeps WinForms' visible flag right
        if not self.hwnd:
            return
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE: back to normal or maximised, as it was
        for after in (-1, -2):  # HWND_TOPMOST then HWND_NOTOPMOST: in front of whatever you were doing, without staying on top
            user32.SetWindowPos(self.hwnd, after, 0, 0, 0, 0, 0x3)  # SWP_NOSIZE | SWP_NOMOVE
        user32.SetForegroundWindow(self.hwnd)

    def on_closing(self, _, args):
        """WinForms FormClosing, on the window's thread. Closing hides to the tray, except when Windows is ending the session:
        refusing that would hold up every restart and sign-out."""
        if self.quitting or str(args.CloseReason) == "WindowsShutDown":
            return
        args.Cancel = True  # the hotkey has to keep working
        threading.Thread(target=self.window.hide, daemon=True).start()
        if self.icon and not self.told_tray:
            self.told_tray = True
            self.icon.notify(f"SayNote is still running. Press {self.settings['hotkey']} any time, or use the tray icon.", "SayNote")

    def on_closed(self, *_):
        """WinForms FormClosed, on the window's thread. Without quit() this is Windows ending the session, which leaves no time
        to transcribe: keep a recording that is still running as a .wav.
        ponytail: a note already being transcribed survives only if Windows waits for it; save its audio here too if that bites"""
        with self.lock:
            if self.quitting:
                return
            self.quitting = True
            if self.stream:
                try:
                    save_audio(self._end_stream(), self.started)
                except Exception:
                    logging.exception("save at end of session")
        if self.icon:
            self.icon.stop()

    def quit(self):
        with self.lock:
            self.quitting = True
            if self.stream:
                self._stop()  # save what you were saying first
            busy = self.busy
        if busy:
            self.show()
            self.say("Saving your note, then SayNote will close.")
        else:
            self.exit()

    def exit(self):
        if self.icon:
            self.icon.stop()
        self.window.destroy()


def single_instance():
    """The 'show' event handle for the first SayNote. A second launch signals it (the first shows its window) and gets None."""
    h = kernel32.CreateEventW(None, False, False, "Local\\SayNote.show")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.SetEvent(h)
        return None
    return h


def run(hidden=False, then=None):
    """Start SayNote and block until it quits. then(app) runs on its own thread once the window exists (tests drive the app with it)."""
    event = single_instance()
    if not event:
        return None
    app = App(hidden)
    # 'SayNote.exe --quit' (the uninstaller) sets this one: quit the way the tray's Quit does, saving a recording first
    events = (wintypes.HANDLE * 2)(event, kernel32.CreateEventW(None, True, False, "Local\\SayNote.quit"))

    def wake():
        while (which := kernel32.WaitForMultipleObjects(2, events, False, 0xFFFFFFFF)) == 0:
            app.show()
        if which == 1:
            app.quit()

    def started():
        threading.Thread(target=wake, daemon=True).start()
        app.start()
        if then:
            then(app)

    webview.start(started, icon=str(HERE / "saynote.ico"))  # all the .ico's sizes for the title bar and taskbar
    return app


def main():
    if "--quit" in sys.argv:  # ask a running SayNote to quit; nothing to do if none is running
        h = kernel32.OpenEventW(0x2, False, "Local\\SayNote.quit")  # EVENT_MODIFY_STATE
        if h:
            kernel32.SetEvent(h)
        return
    for name in ("stdout", "stderr"):  # the built app has no console; download progress bars would crash writing to None
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # cp1252 would raise on a warning that names a non-Latin path
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG, level=logging.WARNING, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
    threading.excepthook = lambda a: logging.error("uncaught", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    run(hidden="--background" in sys.argv)


if __name__ == "__main__":
    main()
