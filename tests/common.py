"""Helpers shared by the test files. Kept out of test_voice.py because importing that pulls in voice.py and
pywinauto, which changes the COM threading mode that Blurt's window (WinForms) needs."""
import os, subprocess, tempfile

FAILS = []


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))
    if not ok:
        FAILS.append(name)


def tts(phrases):
    """Windows' built-in voices write <David|Zira>_<index>.wav (16 kHz mono) into a temp dir. Nothing is played."""
    tmp = tempfile.mkdtemp(prefix="voiceprj-tts-")
    open(os.path.join(tmp, "p.txt"), "w").write("\n".join(phrases))
    subprocess.run(["powershell", "-NoProfile", "-Command", f"""
        Add-Type -AssemblyName System.Speech
        $lines = @(Get-Content '{tmp}\\p.txt')
        foreach ($v in 'Microsoft David Desktop','Microsoft Zira Desktop') {{
            $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SelectVoice($v)
            $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
            for ($i = 0; $i -lt $lines.Count; $i++) {{
                $s.SetOutputToWaveFile("{tmp}\\$($v.Split(' ')[1])_$i.wav", $fmt); $s.Speak($lines[$i])
            }}
            $s.Dispose()
        }}"""], check=True)
    return tmp
