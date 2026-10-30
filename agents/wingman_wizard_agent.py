"""Wingman Wizard: drive the Wingman Mac app through its own wingman-cli.

Every action is one wingman-cli command line. Exit codes follow Wingman's ControlExit:
0 done, 1 Wingman said no, 2 wrong command line, 3 Wingman is not running, 4 still going.

Hard guardrails live in this code, not only in the description:
- No action can sign in, authorize, type a password or code, or pay. There is no account,
  sync, takeover, update or reset action at all. Text that looks like a secret is refused,
  and so is a code typed in pieces (kept on disk, since each call is a fresh process).
- While any bot has an open help request ("Action needed"), type, paste, key, click and done
  are refused: that step is the person's, in Wingman's window. This reads the whole
  conversation of every bot, because the computer is shared. It is a stopgap: wingman-cli
  has no read-only way to ask Wingman's own waiting state, so a request the watch stream
  announced before any row exists is not seen. The authoritative lock belongs in Wingman's
  control socket later.
"""

import glob
import hashlib
import json
import mmap
import os
import re
import subprocess
import tempfile
import time

try:
    from agents.basic_agent import BasicAgent
except ImportError:  # loaded beside the kernel's agents folder
    from basic_agent import BasicAgent

SEND_WAIT = 45  # seconds a send or answer waits before it says "still going"
READ_WAIT = 15  # seconds a read or a check may take
FULL = 5000  # rows read to look for a waiting request; more than this and the check fails closed
PIECES = (600, 6, 4)  # within 10 minutes: at most 5 characters typed in short pieces, at most 3 digits
YOURS = "This step is yours: finish it in Wingman's window."
NOT_RUNNING = "Open Wingman on this Mac, then try again."
STILL_GOING = ("Still going: the bot is still answering. Use action read later to see the reply. "
               "Do not send it again.")
SIGN_IN = ("That looks like a sign-in, an authorization, a password or code, or a payment. "
           "Wingman Wizard never does that for you. " + YOURS)
PIECEMEAL = ("That looks like a code or password typed in pieces. Wingman Wizard never does that for you. "
             + YOURS)
TOO_OLD = "This Wingman cannot do that yet. Update Wingman, then try again."

APPS = ["~/Applications/Wingman.app/Contents/MacOS/wingman-cli", "/Applications/Wingman.app/Contents/MacOS/wingman-cli"]
DEV_BUILDS = ["~/Library/Developer/Wingman/*/Wingman.app/Contents/MacOS/wingman-cli",
              "~/Library/Developer/Wingman/*/out/Products/*/wingman-cli"]
INPUT_MARK = b"click <x> <y>"  # in the usage text of every wingman-cli that can type, key, paste and click

_SIMPLE = {"computer_look": "look", "computer_wake": "wake", "computer_open": "open",
           "computer_control": "control", "computer_watch": "watch", "computer_skip": "skip"}
_LOCKED = {"computer_type", "computer_paste", "computer_key", "computer_click", "computer_done"}
_NEW = _LOCKED | {"computer_control", "computer_watch", "computer_skip"}  # need a wingman-cli with INPUT_MARK
ACTIONS = ["bots", "read", "send", "answer", "computer_snapshot", "computer_type", "computer_key",
           "computer_paste", "computer_click", "computer_done"] + list(_SIMPLE)

_RISKY = re.compile(
    r"pass\s?words?|passcodes?|pass\s?phrase|passwd|\bpw\b|\bpwd\b|credentials?|one[- ]time|verification code|"
    r"security code|auth(?:entication)? code|\b2fa\b|\bmfa\b|\botp\b|\bpin (?:code|number)\b|\bcvv\b|\bcvc\b|"
    r"credit card|card number|api[ _-]?key|access token|auth token|client secret|secret key|"
    r"\bsign(?:ed|ing)?[ -]?(?:in|on)\b|\blog(?:ged|ging)?[ -]?(?:in|on)\b|\bauthori[sz]\w*|"
    r"\bauthenticat\w*|grant access|allow access|captcha|\bpay(?:ment|ing|s)?\b|\bpurchase\w*|checkout",
    re.IGNORECASE)
_CODE_WORD = re.compile(r"\b(?:codes?|pins?|tokens?)\b", re.IGNORECASE)
_PLACE = re.compile(r"^(?:\w+://\S+|[/~]\S*|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|"
                    r"(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|ai|dev|app|co|edu|gov|us|uk)(?:/\S*)?)$")
_NO = re.compile(r"^(no\b|not now|skip|cancel|deny|don't|do not|decline|stop)", re.IGNORECASE)
_MODS = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "option": "alt", "opt": "alt",
         "cmd": "cmd", "command": "cmd"}


def _secret_word(token):
    """One word that looks like a password: letters with digits, or mixed case with a symbol."""
    if len(token) < 6 or _PLACE.match(token):
        return False
    letters, digits = re.search(r"[A-Za-z]", token), re.search(r"\d", token)
    mixed = re.search(r"[a-z]", token) and re.search(r"[A-Z]", token)
    symbol = re.search(r"[^A-Za-z0-9]", token)
    return bool(letters and digits or mixed and symbol and len(token) >= 8)


def _risky(text):
    """True for words about signing in, authorizing, secrets or paying; for anything that looks like a
    code (4 or more digits next to a word like code, 6 or more in a row, or digits alone); and for a
    word that looks like a password."""
    text = (text or "").strip()
    if _RISKY.search(text):
        return True
    digits = sum(c.isdigit() for c in text)
    if digits >= 4 and (_CODE_WORD.search(text) or re.fullmatch(r"[\d\s.-]+", text)):
        return True
    if re.search(r"\d(?:[\s.-]?\d){5,}", text):
        return True
    words = text.split()
    if len(words) == 1:
        return _secret_word(words[0])
    return any(len(w) >= 8 and _secret_word(w) and re.search(r"[A-Z]", w) and re.search(r"[a-z]", w) for w in words)


def _combo(key):
    """(modifiers, last key) of a key like ctrl+l or shift+cmd+v, the way Wingman splits it."""
    parts = key.split("+")
    if key.endswith("+") and len(parts) >= 2:  # ctrl++ is Ctrl and the + key
        parts = parts[:-2] + ["+"]
    return {_MODS.get(p.strip().lower(), "?") for p in parts[:-1]}, parts[-1].strip()


def _can_drive(path):
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return data.find(INPUT_MARK) >= 0
    except (OSError, ValueError):
        return False


def find_cli():
    """(the wingman-cli to call, or None; a problem to say, or None). $WINGMAN_CLI wins. Otherwise the
    installed app or the newest dev build, preferring one that can type, key, paste and click."""
    override = os.environ.get("WINGMAN_CLI")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override, None
        return None, "WINGMAN_CLI is set to %s, which is not a program this Mac can run. Fix it or unset it." % override
    apps = [os.path.expanduser(a) for a in APPS]
    dev = sorted((p for g in DEV_BUILDS for p in glob.glob(os.path.expanduser(g))), key=os.path.getmtime, reverse=True)
    found = [p for p in apps + dev if os.access(p, os.X_OK)]
    return next((p for p in found if _can_drive(p)), found[0] if found else None), None


def _budget():
    """Seconds this call may spend, inside the server's own timeout so the server never stops it first."""
    try:
        limit = float(os.environ.get("RAPPMCP_CALL_TIMEOUT", "110"))
    except ValueError:
        limit = 110.0
    return max(2.0, limit - min(10.0, limit / 4))


def _waiting(rows):
    return any(str(r.get("text", "")).startswith("Action needed") for r in rows)


class WingmanWizardAgent(BasicAgent):
    def __init__(self):
        self.name = "WingmanWizard"
        self.metadata = {
            "name": self.name,
            "description": (
                "Drives Wingman, the Mac app for Grok Bot bots: list bots, read a conversation, send a message, "
                "answer a bot's question, and look at or use the bot's cloud computer. A send waits up to 45 seconds; "
                "if it says still going, read later and never send again. Never signs in, authorizes, types "
                "passwords or codes, or pays. While the bot asks for help, the person finishes that step in Wingman."),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ACTIONS, "description": "What to do."},
                    "bot": {"type": "string", "description": "The bot's name or id (from action bots)."},
                    "text": {"type": "string", "description": "Message to send, answer to give, or text to type or paste."},
                    "last": {"type": "integer", "description": "read: how many rows (default 10)."},
                    "entry": {"type": "string", "description": "answer: the question's id, when read shows several."},
                    "key": {"type": "string", "description": "computer_key: one key, like enter, tab or ctrl+l."},
                    "x": {"type": "integer", "description": "computer_click: pixel from the left, as the snapshot shows."},
                    "y": {"type": "integer", "description": "computer_click: pixel from the top."},
                    "file": {"type": "string", "description": "computer_snapshot: a new .png file to save to (optional)."},
                },
                "required": ["action"],
            },
        }
        super().__init__(name=self.name, metadata=self.metadata)
        self._deadline = time.monotonic() + _budget()

    def _left(self):
        return self._deadline - time.monotonic()

    # One wingman-cli call. Returns (exit code, stdout, stderr without the "wingman-cli: " prefix).
    def _run(self, args, timeout=READ_WAIT):
        cli, problem = find_cli()
        if not cli:
            return 3, "", problem or "not installed"
        timeout = min(timeout, self._left())
        if timeout < 0.5:
            return 4, "", "out of time"
        try:
            done = subprocess.run([cli] + args, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 4, "", "timed out"
        except OSError:
            return 3, "", "could not start"
        err = "\n".join(l[len("wingman-cli: "):] if l.startswith("wingman-cli: ") else l
                        for l in done.stderr.strip().splitlines()[:3])
        return done.returncode, done.stdout.strip(), err

    def _say(self, code, out, err, talking=False):
        if code == 0:
            return out or "Done."
        if code == 3:
            return err if err.startswith("WINGMAN_CLI is set") else NOT_RUNNING
        if code == 4:
            return STILL_GOING if talking else "Wingman did not answer in time. It may be busy; try again."
        if code == 2:
            return "Wingman did not accept that command line: " + (err or "usage error")
        return "Wingman said no: " + (err or "it did not work")

    def _json(self, args, field):
        code, out, _ = self._run(args)
        try:
            value = json.loads(out).get(field) if code == 0 else None
        except (ValueError, AttributeError):
            return None
        return value if isinstance(value, list) else None

    def _rows(self, bot):
        """Every row of a conversation, or None when they cannot all be read (callers then refuse)."""
        rows = self._json(["read", "--last", str(FULL), "--json", bot], "rows")
        return rows if rows is not None and len(rows) < FULL else None

    def _input_block(self, bot, also_bot_words=True):
        """Why input into the computer must stop now, or None. Fails closed."""
        cannot = "Wingman Wizard could not check whether a bot is waiting on you. " + YOURS
        rows, bots = self._rows(bot), self._json(["bots", "--json"], "bots")
        if rows is None or bots is None:
            return cannot
        if _waiting(rows):
            return YOURS
        for other in bots:  # a shared computer's request shows in the conversation of the bot that owns it
            if not isinstance(other, dict) or bot in (other.get("id"), other.get("name")):
                continue
            theirs = self._rows(str(other.get("id") or other.get("name")))
            if theirs is None:
                return cannot
            if _waiting(theirs):
                return YOURS
        said = [r for r in rows if r.get("author") == "bot" and not r.get("widget")]
        if also_bot_words and said and _risky(str(said[-1].get("text", ""))):
            return SIGN_IN
        return None

    def _pieces_block(self, bot, piece):
        """Codes typed a few characters at a time. The record lives on disk: each call is a new process."""
        if piece is None:
            return None
        window, most_chars, most_digits = PIECES
        folder = os.environ.get("WINGMAN_WIZARD_STATE") or os.path.join(
            os.path.expanduser(os.environ.get("RAPPMCP_DATA_DIR", "~/.rappmcp/data")), "wingman-wizard")
        path = os.path.join(folder, "pieces-%s.json" % hashlib.sha256(bot.encode()).hexdigest()[:16])
        now = time.time()
        try:
            with open(path) as f:
                kept = [p for p in json.load(f) if now - p[0] < window]
        except (OSError, ValueError, TypeError, IndexError):
            kept = []
        kept.append([now, len(piece), sum(c.isdigit() for c in piece)])
        if sum(p[1] for p in kept) >= most_chars or sum(p[2] for p in kept) >= most_digits:
            return PIECEMEAL
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "w") as f:
                json.dump(kept, f)
        except OSError:
            return "Wingman Wizard could not keep its record of short typing. " + YOURS
        return None

    def _answer_block(self, bot, text, entry):
        rows = self._rows(bot)
        if rows is None:
            return "Wingman Wizard could not read the question, so it did not answer."
        if entry is not None:  # Wingman matches ids in any case, and cloud ids that read does not show
            asks = [r for r in rows if r.get("widget") and str(r.get("id", "")).lower() == entry.lower()]
            if not asks:
                return "Wingman Wizard cannot find question %s. Use the id that action read shows." % entry
        else:  # any open question Wingman might pick
            asks = [r for r in rows if r.get("widget") and not (r["widget"].get("respondedValue")
                    or r["widget"].get("dismissed") or r["widget"].get("stale"))]
        parts = [p.strip().lstrip("#").strip() for step in text.split(";") for p in step.split(",")]
        for ask in asks:
            widget = ask["widget"]
            steps = widget.get("steps") or [widget]
            prompts = [widget.get("prompt", "")] + [s.get("prompt", "") for s in steps]
            options = [o for s in steps for o in (s.get("options") or [])]

            def words(o):
                return " ".join(str(o.get(k) or "") for k in ("label", "value", "description"))

            def means(part):
                """Every option this part could pick, read as loosely as Wingman might: number, letter, words."""
                low = part.lower()
                hits = [o for s in steps for i, o in enumerate(s.get("options") or [])
                        if low == str(i + 1) or low == chr(ord("a") + i)
                        or any(low and low == str(o.get(k) or "").strip().lower() for k in ("label", "value"))
                        or len(low) >= 3 and (low in words(o).lower() or str(o.get("label") or "~").lower() in low)]
                return hits + [o for o in options if text.strip().lower() in
                               (str(o.get("label") or "").lower(), str(o.get("value") or "").lower())]

            picked = [(part, means(part)) for part in parts]
            labels = [words(o) for _, hits in picked for o in hits] + [p for p, hits in picked if not hits]
            if any(_risky(l) for l in labels):
                return SIGN_IN
            if any(_risky(p) for p in prompts) and not all(
                    _NO.match(p) or hits and all(_NO.match(str(o.get("label") or "")) for o in hits) for p, hits in picked):
                return SIGN_IN
        return None

    def perform(self, action="", bot="", text="", last=None, entry=None, key="", x=None, y=None, file="", **_):
        self._deadline = time.monotonic() + _budget()
        if action not in ACTIONS:
            return "Wingman Wizard cannot do '%s'. It can: %s." % (action, ", ".join(ACTIONS))
        if action == "bots":
            return self._say(*self._run(["bots"]))
        bot = (bot if isinstance(bot, str) else "").strip()
        if not bot or bot.startswith("-") or "\n" in bot or len(bot) > 200:
            return "Name the bot by its name or id. Use action bots to see them."
        text = text if isinstance(text, str) else ""
        if action == "read":
            n = last if isinstance(last, int) and 0 < last <= 200 else 10
            return self._say(*self._run(["read", "--last", str(n), bot]))
        if action == "send":
            if not text.strip():
                return "Say what to send."
            if _risky(text):
                return SIGN_IN
            wait = max(1, int(min(SEND_WAIT, self._left() - 5)))
            code, out, err = self._run(["send", "--timeout", str(wait), bot, "--", text], wait + 10)
            return self._say(code, out, err, talking=True)
        if action == "answer":
            if not text.strip():
                return "Say which answer: its number, like 2, or its words."
            if isinstance(entry, int) and not isinstance(entry, bool):
                entry = str(entry)
            if entry is not None and (not isinstance(entry, str) or not entry.strip() or entry.startswith("-")):
                return "That question id is not valid."
            block = self._answer_block(bot, text.strip(), entry)
            if block:
                return block
            wait = max(1, int(min(SEND_WAIT, self._left() - 5)))
            args = ["answer", "--timeout", str(wait)] + (["--entry", entry] if entry else []) + [bot, "--", text]
            code, out, err = self._run(args, wait + 10)
            return self._say(code, out, err, talking=True)
        if action in _NEW:
            cli, _ = find_cli()
            if cli and not os.environ.get("WINGMAN_CLI") and not _can_drive(cli):
                return TOO_OLD
        if action in _SIMPLE:
            return self._say(*self._run(["computer", bot, _SIMPLE[action]], 60))
        if action == "computer_snapshot":
            path = os.path.abspath(os.path.expanduser(file)) if isinstance(file, str) and file else os.path.join(
                tempfile.gettempdir(), "wingman-%s-%d.png" % (re.sub(r"[^A-Za-z0-9_-]", "_", bot), time.time()))
            if not path.endswith(".png"):
                return "The snapshot file must end in .png."
            if os.path.lexists(path) or not os.path.isdir(os.path.dirname(path)):
                return "Name a new .png file in a folder that exists. Wingman Wizard never replaces a file."
            code, out, err = self._run(["computer", bot, "snapshot", path], 60)
            return ("Saved the computer's screen to %s\n%s" % (path, out)).strip() if code == 0 else self._say(code, out, err)
        # The rest are input into the bot's computer: the sign-in checks, then the help-request lock.
        piece = None
        if action in ("computer_type", "computer_paste"):
            if not text:
                return "Say what text to type or paste."
            if _risky(text):
                return SIGN_IN
            short = text.strip()
            piece = short if short and not re.search(r"\s", short) and (
                len(short) <= 3 or len(short) <= 5 and re.search(r"[^A-Za-z]", short)) else None
        if action == "computer_key":
            key = (key if isinstance(key, str) and key else text if isinstance(text, str) else "").strip()
            key = key.lower() if len(key) > 1 else key
            if not re.fullmatch(r"[a-z0-9+]+|[^\s]", key) or key.startswith("--"):
                return "Name one key, like enter, tab, escape or ctrl+l."
            mods, last_key = _combo(key)
            if last_key.lower() == "v" and mods & {"cmd", "ctrl", "?"}:
                return "That key pastes a clipboard, which may hold a password. Use computer_paste with the text."
            if len(last_key) == 1 and mods <= {"shift"}:
                piece = last_key
        if action == "computer_click" and not (isinstance(x, int) and isinstance(y, int) and x >= 0 and y >= 0):
            return "Give x and y: whole pixels from the top left of the snapshot."
        block = self._input_block(bot, also_bot_words=action != "computer_done") or self._pieces_block(bot, piece)
        if block:
            return block
        if action == "computer_type":
            args = ["computer", bot, "type", "--", text]
        elif action == "computer_paste":
            args = ["computer", bot, "paste", "--", text]
        elif action == "computer_key":
            args = ["computer", bot, "key", key]
        elif action == "computer_click":
            args = ["computer", bot, "click", str(x), str(y)]
        else:
            args = ["computer", bot, "done"]
        return self._say(*self._run(args, 60))
