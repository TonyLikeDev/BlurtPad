// Preview harness, and the page <-> Python contract. Active only when the page is opened with ?preview
// (from disk, in a browser). Inside the app it does nothing and window.pywebview is the real Python side.
//   index.html?preview&state=idle|loading|recording|transcribing&theme=light|dark&empty&problem&message=Some+text
//   also &update (a new version is out), &team=Linh,Minh,Tony (opens Team meeting with those names), &check=1 (and the
//   voice check for person 1), &meeting (state=recording|transcribing is a team meeting), &transcript (opens a team note),
//   &settings (opens Settings), &q=coffee (searches)
//
// Python side (window.pywebview.api.*, each returns a Promise):
//   get_state()      -> {state, problem, hotkey, hotkey_ok, started}
//                       state: 'loading' (speech model not ready yet; recording already works) | 'idle' | 'recording' | 'transcribing'
//                       problem: null, or a sentence that stays until it's fixed (hotkey taken, speech model failed to load)
//                       hotkey: 'Ctrl+Alt+N'    hotkey_ok: false when another program owns the hotkey (only the button works)
//                       started: ms since epoch when the recording began, else null
//   list_notes()     -> [{id, created, text}], newest first. id is the file name, created is ms since epoch
//   toggle()         -> start recording, or stop and save (same as the hotkey). While transcribing it only shows a 'message'
//   discard()        -> stop recording and throw it away, no note. Sends a 'message' with undo: true. false if not recording
//   undo_discard()   -> save the last thrown-away recording after all (it arrives as a 'note' like any other).
//                       false once it's gone: a newer recording started, or BlurtPad is busy or quitting
//   edit_note(id, text) -> true once the new text is saved. The note keeps its id and date. false: show that it wasn't saved
//   delete_note(id)  -> true once the note is in the Recycle Bin
//   open_folder()    -> opens the notes folder in Explorer
//   get_settings()   -> {popup, hotkey, mic, mics, language, languages, autostart, version}. Re-reads the mic list (up to ~1 s)
//                       popup: bring the window to the front when recording starts. Off: it stays where it is, a short sound
//                              plays when listening starts and stops, the tray icon gets a red dot, and messages go to a
//                              Windows notification while the window isn't in front
//                       hotkey: 'Ctrl+Alt+N'   mic: null (the Windows default) or an id from mics   mics: [{id, name}]
//                       language: 'en' | 'auto' (detect it each time) | a Whisper code from languages ('vi', 'fr', ...).
//                                 Name codes with Intl.DisplayNames; skip any it can't name
//                       autostart: start with Windows (also in the tray menu)   version: '1.1'
//   set_setting(key, value) -> {ok, error, settings}. error: null, or a sentence to show. settings: all of them after the call
//                       'popup' | 'autostart': true/false     'mic': null or an id from mics
//                       'hotkey': like 'Ctrl+Shift+Space'. Ctrl, Alt, Shift, Win (in that order) + one key: A-Z, 0-9, F1-F24 or
//                                 Space. Needs Ctrl, Alt or Win unless the key is F1-F24. Fails when another program has it,
//                                 and the old one stays. Success also pushes a 'state' with the new hotkey
//                       'language': fails while state is 'loading'. A language other than English downloads a bigger
//                                 speech model (about 480 MB) the first time: state is 'loading' until it's ready, and
//                                 recording still works. If that fails, a 'message' says so and the old language stays
//   pick_hotkey(on)  -> call with true while the page waits for a new shortcut and false after: in between, the current
//                       hotkey doesn't start a recording while BlurtPad's window is in front
//   check_update()   -> {update, error}. update: as in get_state (null: this is the newest). error: null, or a sentence
//                       (offline, or this copy has no update source). BlurtPad also checks by itself: soon after it
//                       starts, then daily
//   install_update() -> true once the download has started (progress arrives in 'state'). false when there's nothing to
//                       install or a recording or transcription is running (a 'message' says which). When the download
//                       is ready, BlurtPad quits and the new version starts by itself; a failed download sends a
//                       'message' and progress goes back to null
//   get_team()       -> {voices, sentence, seconds, max}: names with a saved voice (sorted), the sentence a voice check
//                       reads, how many seconds it records, the most people in a meeting. Also starts fetching the voice model
//   record_voice(name) -> {ok, error}. Records `seconds` of that person reading `sentence` (the call takes that long, and
//                       'level' messages arrive meanwhile), then remembers the voice under that name, replacing an older one
//   forget_voice(name) -> true once that voice is deleted. get_settings() also lists voices: the saved names
//   start_meeting(names) -> {ok, error}. 2..max different names, each with a saved voice. Starts recording, like toggle();
//                       stop it with toggle() or the hotkey; Discard and Undo work as usual. The note that arrives reads
//                       "Team note: Linh, Minh\n\nLinh (0:00): ...\n\nMinh (0:07): ..." ("Unknown" when no voice matched)
// get_state() and 'state' also carry team: null, or the meeting's names while it records or is written down, and
//                       progress: null, or 0..1 while a meeting is written down
// get_state() and 'state' also carry update: null, or {version: '1.3', notes: "what's new, plain text",
//                       progress: null (available) | 0..0.99 (downloading) | 1 (installing: BlurtPad is about to restart)}
// Call them once window.pywebview.api exists (the 'pywebviewready' event), never before.
//
// Python -> page: window.BlurtPad.update(msg). The page must define window.BlurtPad at load. msg is one of
//   {type: 'state', state, problem, hotkey, hotkey_ok, started}   (same fields as get_state)
//   {type: 'level', level}                     0..1, about 15 per second while recording (0 silence, ~0.5 talking)
//   {type: 'note', note: {id, created, text}}  a note was just saved: add it to the top and show it
//   {type: 'message', text, kind, undo}        kind 'info' | 'error'. One line of feedback, e.g. 'Heard nothing, no note saved.'
//                                              undo: true -> offer Undo, which calls undo_discard()
(function () {
  const q = new URLSearchParams(location.search)
  if (!q.has('preview')) return
  if (q.get('theme')) document.documentElement.dataset.theme = q.get('theme')

  const DAY = 86400000, now = Date.now(), at = (daysAgo, h, m) => {
    const d = new Date(now - daysAgo * DAY)
    d.setHours(h, m, 0, 0)
    return Math.min(d.getTime(), now - 60000 * (25 - h))  // a sample "later today" stays in the past, in order, with its own id
  }
  const id = (t) => {
    const d = new Date(t), p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}`
  }
  const note = (t, text) => ({ id: id(t), created: t, text })
  let notes = q.has('empty') ? [] : [
    note(at(0, 9, 41), 'Plan for the week. Monday finish the landing page copy and send it to Linh for review. Tuesday morning is the dentist at nine thirty, so no meetings before eleven. Wednesday and Thursday are for the voice notes app: make it a real app with an icon, then fix the tray menu. Friday, invoice the two clients and back up the laptop. If there is time left, start reading the book on habits that Minh recommended.'),
    note(at(0, 8, 5), 'Buy milk, eggs and coffee beans on the way home.'),
    note(at(1, 15, 30), 'Team note: Linh, Minh, Tony\n\nLinh (0:00): Okay, let\'s start. The main topic today is the release date for the new version.\n\nMinh (0:06): I think we can ship on Friday if the testing goes well.\n\nTony (0:11): Testing is almost done. I found two small bugs in the settings page yesterday.\n\nLinh (0:19): Are they serious, or can they wait for the next update?\n\nTony (0:23): They can wait. Neither one loses any data.\n\nUnknown (0:28): Sorry I\'m late.\n\nLinh (0:31): Good. Then Friday it is.'),
    note(at(1, 21, 17), 'Idea for the blog: a short post about why I stopped using five different note apps and went back to plain text files. Main point is that I never lost a single note in a text file, but I lost two years of notes when a sync service shut down.'),
    note(at(1, 14, 2), 'Call the landlord about the heater before Friday.'),
    note(at(3, 18, 30), 'Gift ideas for mom: the blue scarf from the market, a framed photo from the trip to Da Lat, or a cooking class for two.'),
    note(at(6, 11, 12), 'Meeting notes. The client wants the first version by the end of the month. Budget is fixed. They care most about the search feature and least about the dashboard, so cut the dashboard to one page.'),
    note(at(12, 16, 45), 'Remember to renew the passport. It expires in March.'),
  ]
  let state = ['loading', 'idle', 'recording', 'transcribing'].includes(q.get('state')) ? q.get('state') : 'idle'
  let started = state === 'recording' ? now - 47000 : null, timer = null
  const problem = q.has('problem') ? 'Ctrl+Alt+N is taken by another program. Is BlurtPad already running?' : null
  const hotkey = 'Ctrl+Alt+N', hotkey_ok = !problem
  let update = q.has('update') ? { version: '1.3', notes: 'Notes can be pinned to the top.\nThe tray icon shows how long you have been recording.', progress: null } : null
  let meeting = q.has('meeting') ? ['Linh', 'Minh', 'Tony', 'An'] : null, progress = q.has('meeting') && state === 'transcribing' ? 0.35 : null
  const voices = ['Minh']
  const snapshot = () => ({ state, problem, hotkey: settings.hotkey, hotkey_ok, started, update,
    team: state === 'recording' || state === 'transcribing' ? meeting : null, progress: state === 'transcribing' ? progress : null })
  const send = (msg) => window.BlurtPad && window.BlurtPad.update(msg)
  const setState = (s, since = Date.now()) => {
    state = s
    started = s === 'recording' ? since : null
    send({ type: 'state', ...snapshot() })
    clearInterval(timer)
    if (s === 'recording') {
      let t = 0
      timer = setInterval(() => send({ type: 'level', level: Math.max(0, 0.45 + 0.35 * Math.sin((t += 0.7)) * Math.sin(t / 3.1) + (Math.random() - 0.5) * 0.25) }), 66)
    }
  }

  const LANGS = 'af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo zh yue'.split(' ')
  const settings = {
    popup: true, hotkey, mic: null, language: 'en', languages: LANGS, autostart: false, version: '1.1',
    mics: [{ id: 'Microphone (PD100X Podcast Micr', name: 'Microphone (PD100X Podcast Microphone)' },
      { id: 'Microphone (HTW FHD Webcam Audi', name: 'Microphone (HTW FHD Webcam Audio)' },
      { id: 'Microphone (High Definition Aud', name: 'Microphone (High Definition Audio Device)' }],
  }
  const HOTKEY = /^(Ctrl\+)?(Alt\+)?(Shift\+)?(Win\+)?([A-Z0-9]|Space|F([1-9]|1\d|2[0-4]))$/
  let thrownAway = null
  const save = (text) => {
    const n = note(Date.now(), text)
    notes = [n, ...notes]
    send({ type: 'note', note: n })
    setState('idle')
  }
  const result = (error) => ({ ok: !error, error: error || null, settings: { ...settings } })

  window.pywebview = { api: {
    get_state: async () => snapshot(),
    list_notes: async () => notes,
    toggle: async () => {
      if (state === 'transcribing') return null
      if (state !== 'recording') return thrownAway = null, meeting = null, setState('recording'), null
      if (meeting) {  // the preview writes a meeting down in 3 s
        progress = 0
        setState('transcribing')
        const tick = setInterval(() => {
          progress = Math.min(1, progress + 0.1)
          send({ type: 'state', ...snapshot() })
          if (progress < 1) return
          clearInterval(tick)
          const names = meeting
          meeting = progress = null
          save(`Team note: ${names.join(', ')}\n\n` + names.map((n, i) => `${n} (0:${String(i * 7).padStart(2, '0')}): This is what ${n} said in the preview.`).join('\n\n'))
        }, 300)
        return null
      }
      setState('transcribing')
      setTimeout(() => save('This note was made in the preview. In the app, what you said appears here.'), 1500)
      return null
    },
    discard: async () => {
      if (state !== 'recording') return false
      thrownAway = 'This recording was thrown away, then brought back with Undo.'
      setState('idle')
      send({ type: 'message', text: 'Recording thrown away.', kind: 'info', undo: true })
      return true
    },
    undo_discard: async () => {
      if (!thrownAway || state !== 'idle') return false
      const text = thrownAway
      thrownAway = null
      setState('transcribing')
      setTimeout(() => save(text), 1500)
      return true
    },
    edit_note: async (nid, text) => {
      const n = notes.find((x) => x.id === nid)
      if (!n || q.has('editfail')) return false
      n.text = text.trim()
      return true
    },
    delete_note: async (nid) => { notes = notes.filter((n) => n.id !== nid); return true },
    open_folder: async () => null,
    get_settings: async () => new Promise((resolve) => setTimeout(() => resolve({ ...settings, voices: [...voices].sort() }), 300)),
    set_setting: async (key, value) => {
      if (key === 'hotkey') {
        const m = HOTKEY.exec(value || '')
        if (!m || !(m[1] || m[2] || m[4] || m[6])) return result(`${value} can't be used. Add Ctrl, Alt or Win, or pick F1 to F24.`)
        if (m[4]) return result(`${value} is taken by another program. Pick another one.`)  // the preview's stand-in for a taken shortcut
        settings.hotkey = value
        send({ type: 'state', ...snapshot() })
      } else if (key === 'language') {
        if (state === 'loading') return result('Still getting ready. Try again in a moment.')
        const was = settings.language
        settings.language = value
        if ((was === 'en') !== (value === 'en')) {  // the preview pretends to download the other speech model
          setState('loading')
          setTimeout(() => setState('idle'), 3000)
        }
      } else if (key in settings) settings[key] = value
      else return result(`Unknown setting ${key}.`)
      return result()
    },
    pick_hotkey: async () => null,
    get_team: async () => ({ voices: [...voices].sort(), sentence: "The quick brown fox jumps over the lazy dog. I'm reading this so BlurtPad can learn how my voice sounds.", seconds: 3, max: 10 }),
    record_voice: async (name) => new Promise((resolve) => {  // 3 s of a fake voice; a person named Error isn't heard
      let t = 0
      const tick = setInterval(() => send({ type: 'level', level: Math.max(0, 0.4 + 0.3 * Math.sin((t += 0.6))) }), 66)
      setTimeout(() => {
        clearInterval(tick)
        if (name === 'Error') return resolve({ ok: false, error: "BlurtPad couldn't hear you. Check the microphone, then try again." })
        if (!voices.includes(name)) voices.push(name)
        resolve({ ok: true, error: null })
      }, 3000)
    }),
    forget_voice: async (name) => voices.includes(name) && !!voices.splice(voices.indexOf(name), 1),
    start_meeting: async (names) => {
      const missing = names.filter((n) => !voices.includes(n))
      if (missing.length) return { ok: false, error: `${missing.join(', ')} still need a voice check.` }
      meeting = names
      thrownAway = null
      setState('recording')
      return { ok: true, error: null }
    },
    check_update: async () => new Promise((resolve) => setTimeout(() => resolve({ update, error: null }), 600)),
    install_update: async () => {  // the preview downloads in 3 s, then stops at "Installing"
      if (!update || update.progress != null || state === 'recording' || state === 'transcribing') return false
      const tick = setInterval(() => {
        update = { ...update, progress: Math.min(1, (update.progress || 0) + 0.05) }
        send({ type: 'state', ...snapshot() })
        if (update.progress >= 1) clearInterval(tick)
      }, 150)
      return true
    },
  } }
  window.addEventListener('load', () => {
    window.dispatchEvent(new Event('pywebviewready'))
    if (state === 'recording') setState('recording', started)  // starts the fake mic level, 47 s into a recording
    if (q.get('message')) setTimeout(() => send({ type: 'message', text: q.get('message'), kind: 'info' }), 50)
    // Preview-only shortcuts into the team screens, through the page's own buttons and fields
    const $ = (sel) => document.querySelector(sel)
    if (q.has('team')) setTimeout(() => {
      const names = (q.get('team') || '').split(',').filter(Boolean)
      $('#team-btn').click()
      if (names.length > 2) { $('#t-count').value = names.length; $('#t-count').dispatchEvent(new Event('input')) }
      setTimeout(() => {
        names.forEach((n, i) => { const f = document.querySelectorAll('.p-name')[i]; if (f) { f.value = n; f.dispatchEvent(new Event('input')) } })
        if (q.get('check')) document.querySelectorAll('.p-go')[Number(q.get('check')) - 1]?.click()
      }, 100)
    }, 300)
    if (q.has('settings')) setTimeout(() => $('#gear').click(), 300)
    if (q.get('q')) setTimeout(() => { $('#q').value = q.get('q'); $('#q').dispatchEvent(new Event('input')) }, 300)
    if (q.has('transcript')) setTimeout(() => document.querySelector('#list .item:nth-child(1)') && [...document.querySelectorAll('#list .item')].find((b) => b.textContent.includes('Team note'))?.click(), 300)
  })
})()
