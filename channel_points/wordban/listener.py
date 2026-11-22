"""Word ban listener: hears GreMi say the banned word and fires Streamer.bot.

Replaces Streamer.bot's built-in Windows speech commands for the "IRL word ban"
channel point reward. Runs on GreMi's PC from Documents\\GreMiChannelPoints:

  wordban.exe              this program (built by .github/workflows/channel_points.yml)
  wordban\\model\\          offline Vosk English model
  wordban\\ban.json         written by Streamer.bot on a redeem: {"word", "until" (unix s)}
  wordban\\status.json      written here every few seconds: {"state", "time", "mic", "error"}
  wordban\\config.json      optional: {"mic": "part of the mic name", "sb_port": 8080,
                            "sb_pass": "", "action": "Banned word spoken"}
  wordban\\listener.log     what it heard and did

Streamer.bot starts it on each redeem; it quits by itself a minute after the
ban ends. If the mic drops out (Windows audio error, unplugged headset) it
reopens it every few seconds instead of giving up.
Hits fire the Streamer.bot action through its websocket server (DoAction).
"""
import base64
import hashlib
import json
import logging
import os
import queue
import re
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
DIR = os.path.join(HERE, "wordban") if os.path.isdir(os.path.join(HERE, "wordban")) else HERE
BAN = os.path.join(DIR, "ban.json")
STATUS = os.path.join(DIR, "status.json")
CONFIG = os.path.join(DIR, "config.json")
MODEL = os.path.join(DIR, "model")
IDLE_EXIT = 60          # seconds without an active ban before quitting
log = logging.getLogger("wordban")

# Words people say or spell many ways: saying any of them counts as the banned word.
GROUPS = [
    {"ok", "okay", "oke", "okey", "okie"},
    {"yeah", "yea", "yah"},
    {"bro", "bruh", "brah"},
    {"gonna", "going to"},
    {"wanna", "want to"},
    {"omg", "oh my god", "o m g"},
    {"damn", "dam", "damned"},
]


# ---------------------------------------------------------------- matching

def norm(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9' ]+", " ", text).replace("'", "")
    return " ".join(text.split())


def metaphone(word):
    """A small sound-alike key (simplified Metaphone): words that sound the same
    get the same key, e.g. 'verstappen' / 'ferstapen'."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    for a, b in (("^kn", "n"), ("^gn", "n"), ("^pn", "n"), ("^wr", "r"), ("^x", "s"), ("^wh", "w"),
                 ("mb$", "m"), ("ph", "f"), ("ck", "k"), ("sch", "sk"), ("tch", "ch"),
                 ("c(?=[iey])", "s"), ("q", "k"), ("dg(?=[iey])", "j"), ("d", "t"),
                 ("gh(?![aeiou])", ""), ("g(?=[iey])", "j"), ("sh", "x"), ("ch", "x"), ("c", "k"),
                 ("th", "0"), ("v", "f"), ("z", "s"), ("w(?![aeiou])", ""), ("y(?![aeiou])", ""),
                 ("h(?![aeiou])", "")):
        w = re.sub(a, b, w)
    first, rest = w[0], re.sub(r"[aeiouy]", "", w[1:])
    return re.sub(r"(.)\1+", r"\1", ("a" if first in "aeiouy" else first) + rest)


def targets(word):
    """Every written form that counts as the banned word or phrase."""
    w = norm(word)
    forms = {w}
    for group in GROUPS:
        if w in group:
            forms |= group
    if w and " " not in w and len(w) >= 3:
        forms |= {w + "s", w + "es"}
    return {f for f in forms if f}


class Matcher:
    def __init__(self, word):
        self.word = word
        self.forms = targets(word)
        flat = norm(word).replace(" ", "")
        # sound-alike matching only for longer words: short ones collide too easily
        self.sound = metaphone(flat) if len(flat) >= 6 else ""

    def count(self, text):
        """How many times the banned word is in this recognised text."""
        words = norm(text).split()
        hits, i = 0, 0
        while i < len(words):
            took = 0
            for n in (3, 2, 1):
                part = words[i:i + n]
                chunk = " ".join(part)
                if len(part) == n and (chunk in self.forms or (
                        self.sound and n <= 2 and len(chunk.replace(" ", "")) >= 5
                        and metaphone(chunk.replace(" ", "")) == self.sound)):
                    took = n
                    break
            if took:
                hits, i = hits + 1, i + took
            else:
                i += 1
        return hits


# ---------------------------------------------------------------- files

def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_status(state, mic="", error=""):
    tmp = STATUS + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"state": state, "time": time.time(), "mic": mic, "error": error}, f)
        os.replace(tmp, STATUS)
    except OSError:
        pass


def active_ban(now=None):
    ban = read_json(BAN) or {}
    word = str(ban.get("word") or "").strip()
    if word and float(ban.get("until") or 0) > (now or time.time()):
        return word
    return ""


# ---------------------------------------------------------------- Streamer.bot

def sha_b64(text):
    return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


def fire(config, word, heard):
    """Run the Streamer.bot action over its websocket server."""
    import websocket  # websocket-client
    ws = websocket.create_connection(f"ws://127.0.0.1:{int(config.get('sb_port', 8080))}/", timeout=5)
    try:
        hello = json.loads(ws.recv() or "{}")
        auth = hello.get("authentication")
        if auth and config.get("sb_pass"):
            secret = sha_b64(config["sb_pass"] + auth["salt"])
            ws.send(json.dumps({"request": "Authenticate", "id": "auth",
                                "authentication": sha_b64(secret + auth["challenge"])}))
            ws.recv()
        ws.send(json.dumps({"request": "DoAction", "id": f"wordban{int(time.time() * 1000)}",
                            "action": {"name": config.get("action", "Banned word spoken")},
                            "args": {"bannedWord": word, "heard": heard}}))
        reply = json.loads(ws.recv() or "{}")
        if reply.get("status") not in (None, "ok"):
            log.warning("Streamer.bot said: %s", reply)
    finally:
        ws.close()


# ---------------------------------------------------------------- audio

def pick_mic(sd, want):
    inputs = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    if want:
        for i, d in inputs:
            if want.lower() in d["name"].lower():
                return i, d
        log.warning("No mic matching %r; using the Windows default. Mics: %s",
                    want, "; ".join(d["name"] for _, d in inputs))
    i = sd.default.device[0]
    return i, sd.query_devices(i)


def listen(config, recognize):
    """Feed mic audio to recognize(bytes, rate) until there's no ban for a while.
    Reopens the mic after any error."""
    import sounddevice as sd
    idle_since = None
    while True:
        if not active_ban():
            idle_since = idle_since or time.time()
            write_status("idle")
            if time.time() - idle_since > IDLE_EXIT:
                log.info("No ban active, stopping.")
                return
            time.sleep(2)
            continue
        idle_since = None
        name = "?"
        try:
            index, dev = pick_mic(sd, config.get("mic", ""))
            name = dev["name"]
            rate = int(dev["default_samplerate"])
            q = queue.Queue()
            with sd.RawInputStream(device=index, samplerate=rate, blocksize=rate // 5, dtype="int16",
                                   channels=1, callback=lambda data, frames, t, status: q.put(bytes(data))):
                log.info("Listening on %s (%d Hz)", name, rate)
                last_status = 0
                while active_ban():
                    try:
                        data = q.get(timeout=3)
                    except queue.Empty:
                        raise RuntimeError("the mic stopped sending audio")
                    recognize(data, rate)
                    if time.time() - last_status > 2:
                        write_status("listening", name)
                        last_status = time.time()
        except Exception as e:  # mic gone, Windows audio error ...
            log.warning("Mic problem (%s): %s; retrying in 3 s", name, e)
            write_status("mic-error", name, str(e))
            time.sleep(3)
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass


def wav_text(model, path):
    """Self-test: what the model hears in a wav file."""
    import wave
    from vosk import KaldiRecognizer
    with wave.open(path) as w:
        rec = KaldiRecognizer(model, w.getframerate())
        while True:
            data = w.readframes(4000)
            if not data:
                break
            rec.AcceptWaveform(data)
        return json.loads(rec.FinalResult())["text"]


def main():
    os.makedirs(DIR, exist_ok=True)
    logging.basicConfig(filename=os.path.join(DIR, "listener.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    if len(sys.argv) == 4 and sys.argv[1] == "--wav":   # --wav <file> <word>
        text = wav_text(Model(MODEL), sys.argv[2])
        result = json.dumps({"text": text, "hits": Matcher(sys.argv[3]).count(text)})
        with open(sys.argv[2] + ".json", "w", encoding="utf-8") as f:   # the windowed exe has no console
            f.write(result)
        if sys.stdout:
            print(result)
        return
    status = read_json(STATUS) or {}
    if status.get("state") in ("listening", "idle", "starting") and time.time() - float(status.get("time") or 0) < 8:
        log.info("Already running.")
        return
    write_status("starting")
    config = read_json(CONFIG, {}) or {}
    model = Model(MODEL)
    state = {"rec": None, "rate": 0, "word": "", "matcher": None, "seen": 0, "last_hit": 0}

    def recognize(data, rate):
        word = active_ban()
        if word != state["word"] or rate != state["rate"] or state["rec"] is None:
            state.update(rec=KaldiRecognizer(model, rate), rate=rate, word=word, matcher=Matcher(word), seen=0)
            log.info("Banned word: %r (counts: %s)", word, ", ".join(sorted(state["matcher"].forms)))
        rec = state["rec"]
        final = rec.AcceptWaveform(data)
        text = json.loads(rec.Result() if final else rec.PartialResult()).get("text" if final else "partial", "")
        n = state["matcher"].count(text)
        if n > state["seen"]:
            log.info("HEARD %r in: %s", word, text)
            try:
                fire(config, word, text)
            except Exception as e:
                log.warning("Could not reach Streamer.bot: %s", e)
            state["seen"] = n
        if final:
            state["seen"] = 0

    try:
        listen(config, recognize)
    finally:
        write_status("stopped")


if __name__ == "__main__":
    main()
