# -*- coding: utf-8 -*-
import collections
import json
import os
import queue
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
import tkinter.font as tkfont
import webbrowser
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Hide the console window that would otherwise flash up for every age.exe call
# when this script runs as a windowed (pyinstaller --noconsole) executable.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Only what age's -r accepts. Other SSH key types (ECDSA, sk-...) are not listed:
# age refuses them. The final say is age.exe itself, via a dry run (see
# recipients_dry_run_problem), which also catches mistyped keys.
RECIPIENT_PREFIXES = (
    ("age1tagpq1", "age post-quantum tagged key"),
    ("age1tag1", "age tagged key"),
    ("age1pq1", "age post-quantum public key"),
    ("age1", "age public key"),
    ("ssh-ed25519 ", "SSH Ed25519 public key"),
    ("ssh-rsa ", "SSH RSA public key"),
)

# Colours as (light mode, dark mode). The window is dark, in the Windows 11 style:
# flat surfaces, hairline dividers, icon buttons. The accent is page's blue.
MUTED = ("gray40", "gray63")
ICON_GREY = "#a1a1a1"  # icons are drawn with PIL, which needs hex colours (MUTED's dark value)
WARN = ("#b35c00", "#f0a040")
ACCENT = ("#3a7ebf", "#4a9eff")
OK_COLOR = ("#2f7a43", "#8ac498")
ERR_COLOR = ("#b3261e", "#ff8a80")
BLUE, BLUE_HOVER = "#1f6aa5", "#144870"  # the app colour: Files, Folder, the action button
LINE = ("gray80", "#383838")
ROW_HOVER = ("gray88", "#2a2a2a")
DANGER, DANGER_HOVER = "#8b2c2c", "#a33a3a"
APP_NAME = "page"
APP_VERSION = "0.1.1"
REPO_URL = "https://github.com/t0ma5/page"
AGE_URL = "https://github.com/FiloSottile/age"

SECONDARY = {"fg_color": ("gray78", "gray28"), "hover_color": ("gray70", "gray35"),
             "text_color": ("gray10", "gray92"), "text_color_disabled": ("gray55", "gray50")}
# Flat toolbar / row buttons: only a hover background.
GHOST = {"fg_color": "transparent", "hover_color": ("gray80", "#3a3a3a"),
         "text_color": ("gray10", "gray92"), "text_color_disabled": ("gray55", "gray45")}

RECIPIENT_HELP = (
    "Encrypt: needs at least one recipient or a recipients file. A recipient is an age public key "
    "(\"age1...\", \"age1pq1...\") or an SSH public key (\"ssh-ed25519 AAAA...\", \"ssh-rsa AAAA...\"). "
    "Every listed recipient can decrypt the file. Lines starting with # are comments.\n\n"
    "Decrypt: needs at least one identity file containing secret keys (\"AGE-SECRET-KEY-1...\") "
    "or an SSH private key. Unused identity files are ignored.\n\n"
    "Not supported in this GUI: passphrase-protected identity files and passphrase-protected SSH keys "
    "(age would need a terminal to ask for the passphrase), and \"-\" (stdin).\n\n"
    "Generate new key pair creates an identity file with age-keygen and adds it here, together with "
    "its public key. Keep the identity file secret and backed up: without it, files encrypted to its "
    "public key cannot be decrypted."
)


COMPOUND_EXTENSIONS = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz", ".tar.lzma")


def extension_of(path):
    """os.path.splitext's extension, but "x.tar.gz" keeps ".tar.gz" together
    (-> "x (1).tar.gz") and ".gitignore" has none (-> ".gitignore (1)")."""
    name = os.path.basename(path)
    lower = name.lower()
    for double in COMPOUND_EXTENSIONS:
        if lower.endswith(double) and len(name) > len(double):
            return name[-len(double):]
    return os.path.splitext(name)[1]  # splitext already treats ".gitignore" as no extension


ADMIN_WARNING = ("Warning: page is running as administrator. Windows hides drives mapped by your normal account "
                 "from it, also blocks drag & drop from Explorer. Close page and start it normally.")


PASS_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (i, l, o, 0, 1)


def generate_passphrase():
    """6 groups of 5 characters from a 31-character alphabet (35 characters with the
    dashes), about 148 bits (same as sage)."""
    return "-".join("".join(secrets.choice(PASS_ALPHABET) for _ in range(5)) for _ in range(6))


def format_size(n):
    if n is None:
        return ""
    if n < 1024:
        return f"{n} B"
    v, unit = n / 1024, "KB"
    for unit in ("KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            break
        v /= 1024
    return f"{v:.1f} {unit}" if v < 10 else f"{round(v)} {unit}"


def fit_text(text, max_chars):
    """Cut text to max_chars with an ellipsis (Tk labels don't shorten text themselves)."""
    return text if len(text) <= max_chars else text[: max(1, max_chars - 1)] + "\u2026"


def fit_width(text, measure, max_px, middle=False):
    """Shorten text with an ellipsis until measure(text) fits max_px. middle=True keeps
    both ends ("quarterly-re\u20262025 (1).pdf"), so a name's " (1)" and extension stay visible."""
    if measure(text) <= max_px:
        return text
    lo, hi, best = 1, len(text) - 1, text[:1] + "\u2026"
    while lo <= hi:  # the longest cut that still fits
        keep = (lo + hi) // 2
        if middle:
            head = (keep + 1) // 2
            cand = text[:head] + "\u2026" + text[len(text) - (keep - head):]
        else:
            cand = text[:keep] + "\u2026"
        if measure(cand) <= max_px:
            best, lo = cand, keep + 1
        else:
            hi = keep - 1
    return best


def folder_of(path):
    folder = os.path.dirname(path)
    return folder + os.sep if re.fullmatch(r"[A-Za-z]:", folder) else folder  # a drive root is "F:\"


# Icons from the Windows icon font (Segoe Fluent Icons on Windows 11, Segoe MDL2 Assets
# on Windows 10), drawn into images so they can sit on buttons next to text.
GLYPHS = {
    "add-file": "\ue8e5", "add-folder": "\ue8f4", "trash": "\ue74d", "search": "\ue721",
    "info": "\ue946", "x": "\ue711", "check": "\ue73e", "error": "\ue783", "folder-open": "\ue838",
    "eye": "\ue7b3", "file": "\ue8a5", "lock": "\ue72e", "working": "\ue895",
}
_icon_font_path = None


def glyph_image(name, color, size=16):
    """A CTkImage of one icon glyph in the given colour, or None if the icon font is missing."""
    global _icon_font_path
    try:
        from PIL import Image, ImageDraw, ImageFont
        if _icon_font_path is None:
            fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
            _icon_font_path = next((os.path.join(fonts, f) for f in ("SegoeIcons.ttf", "segmdl2.ttf")
                                    if os.path.isfile(os.path.join(fonts, f))), "")
        if not _icon_font_path:
            return None
        px = size * 4  # drawn large, shown small: sharp at any scaling
        font = ImageFont.truetype(_icon_font_path, px)
        im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((px / 2, px / 2), GLYPHS[name], font=font, fill=color, anchor="mm")
        return ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
    except Exception:
        return None


# Bad network path, network name deleted, bad network name, no network.
NET_UNREACHABLE = {53, 64, 67, 1222}


def without_long_prefix(path):
    r"""The path without the \\?\ long-path prefix (\\?\UNC\server\share -> \\server\share)."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def describe_add_error(path, exc):
    """Why a dropped or chosen path can't be added, in words a user can act on."""
    drive = os.path.splitdrive(without_long_prefix(path))[0]
    if drive.startswith("\\\\"):
        # A network share: the server or share is the problem, not a hidden drive.
        # Decided from the error alone, so there's no second network timeout.
        if getattr(exc, "winerror", None) in NET_UNREACHABLE:
            return f"{path}: network share {drive} isn't reachable."
    elif drive and not os.path.exists(drive + os.sep):
        return f"{path}: drive {drive} isn't available to page."
    if isinstance(exc, FileNotFoundError):
        return f"{path}: not found (moved or deleted?)."
    if isinstance(exc, PermissionError):
        return f"{path}: access denied."
    return f"{path}: can't be read ({getattr(exc, 'strerror', None) or exc})."


PROBLEM_LIMIT = 50  # the line under the list previews a few; "Show all" opens these


def problem_lines(problems, limit=PROBLEM_LIMIT):
    """List unreadable paths, at most `limit`. (When page runs as administrator the
    Admin pill explains hidden drives, so it isn't repeated here.)"""
    lines = [f"  {text}" for text in problems[:limit]]
    if len(problems) > limit:
        lines.append(f"  ...and {len(problems) - limit} more.")
    return "\n".join(lines)


def expand_paths(paths):
    """Dropped or chosen paths -> (files, skipped subfolders, problems, sizes).

    A folder adds the files directly inside it. Paths that can't be read are listed
    in problems with the reason. Runs off the UI thread: a sleeping network drive
    can take a long time to answer. os.scandir gives each entry's type and size in
    one call.
    """
    # tk.splitlist already removed Tcl's {braces}; stripping again would break
    # real names such as "backup{1}" or a "{GUID}" folder.
    # long_path: paths over 260 characters must not be silently skipped.
    files, problems, sizes = [], [], {}
    skipped = 0
    for p in paths:
        # Absolute, so Send-to/command-line paths don't depend on the working folder.
        try:
            p = os.path.normpath(os.path.abspath(p))
            info = os.stat(long_path(p))
        except ValueError:
            problems.append(f"{p}: not a valid path.")
            continue
        except OSError as exc:
            problems.append(describe_add_error(p, exc))
            continue
        if stat.S_ISREG(info.st_mode):
            files.append(p)
            sizes[p] = info.st_size
        elif stat.S_ISDIR(info.st_mode):
            try:
                with os.scandir(long_path(p)) as it:
                    items = sorted(it, key=lambda d: d.name)
            except OSError as exc:
                problems.append(describe_add_error(p, exc))
                continue
            for item in items:
                full_path = os.path.join(p, item.name)
                try:
                    if item.is_file():
                        files.append(full_path)
                        sizes[full_path] = item.stat().st_size
                    elif item.is_dir():
                        skipped += 1
                except OSError:
                    continue  # vanished meanwhile
    return files, skipped, problems, sizes


def age_dry_run(age_path, recipients, recipients_file):
    """Ask age.exe whether it accepts these recipients, by encrypting nothing to
    nowhere. Returns the problem in words, or None (also when it can't be checked:
    the real run then reports any error per file)."""
    cmd = [age_path, "-e"]
    for r in recipients:
        cmd += ["-r", r]
    if recipients_file:
        cmd += ["-R", recipients_file]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    err = proc.stderr.decode("utf-8", "replace")
    lines = [l.strip() for l in err.splitlines()
             if l.strip() and "report unexpected or unhelpful errors" not in l]
    if proc.returncode != 0:
        msg = lines[0] if lines else "age.exe refused these recipients."
        return "age: " + msg.split("error: ", 1)[-1]
    if any("ignoring unsupported SSH key" in l for l in lines):
        return ("The recipients file has an SSH key age can't use (too short or of an "
                "unsupported type); age would skip it. Remove it or comment it out with #.")
    return None


def monitor_rect(x, y):
    """(left, top, right, bottom) of the monitor's work area at screen point x, y, or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        mon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return None


class Tooltip:
    """The full text of a shortened label, shown after a short hover (like sage's titles)."""

    active = None  # the tip on screen; hidden on scroll, focus loss or minimise (hide_all)

    @classmethod
    def hide_all(cls, _e=None):
        if cls.active is not None:
            cls.active.hide()

    def __init__(self, widgets, text):
        self.text, self.tip, self.job = text, None, None
        self.owner = widgets[0]
        for w in widgets:
            w.bind("<Enter>", self.schedule, add="+")
            w.bind("<Leave>", self.hide, add="+")
            w.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _e=None):
        self.hide()
        self.job = self.owner.after(500, self.show)

    def show(self):
        self.job = None
        if not self.text or not self.owner.winfo_exists():
            return
        Tooltip.hide_all()
        px, py = self.owner.winfo_pointerx(), self.owner.winfo_pointery()
        self.tip = tk.Toplevel(self.owner)
        self.tip.wm_overrideredirect(True)
        self.tip.withdraw()  # measured first, then placed so it stays on the pointer's monitor
        tk.Label(self.tip, text=self.text, justify="left", bg="#2b2b2b", fg="#ebebeb", relief="solid",
                 borderwidth=1, padx=8, pady=5, wraplength=520, font=("Segoe UI", 9)).pack()
        self.tip.update_idletasks()
        w, h = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        x, y = px + 14, py + 18
        area = monitor_rect(px, py)
        if area:
            left, top, right, bottom = area
            x = max(left, min(x, right - w))
            if y + h > bottom:
                y = max(top, py - h - 8)  # above the pointer instead
        self.tip.wm_geometry(f"+{x}+{y}")
        self.tip.deiconify()
        Tooltip.active = self

    def hide(self, _e=None):
        if self.job:
            try:
                self.owner.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None
        if Tooltip.active is self:
            Tooltip.active = None


def running_elevated():
    """True when page runs as administrator *with a normal session alongside* (a UAC
    split token, TokenElevationTypeFull). Only then does Windows hide drives mapped by
    the normal session (network drives, Cryptomator vaults) and block drag and drop
    from Explorer. With UAC off, or the built-in Administrator account, there is no
    normal session to switch to, so there is nothing to warn about."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            return False
        try:
            value, size = wintypes.DWORD(), wintypes.DWORD()
            ok = advapi32.GetTokenInformation(token, 18, ctypes.byref(value), ctypes.sizeof(value),
                                              ctypes.byref(size))  # 18 = TokenElevationType
            return bool(ok) and value.value == 2  # TokenElevationTypeFull
        finally:
            kernel32.CloseHandle(token)
    except (AttributeError, OSError):
        return False


def long_path(path):
    """Add the long-path prefix so Windows file calls accept paths over 260 characters."""
    if os.name != "nt" or path.startswith("\\\\?\\") or not os.path.isabs(path):
        return path
    path = os.path.normpath(path)  # \\?\ paths are taken literally: no "/" or ".."
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def name_candidates(path):
    """`path`, then `name (1).ext`, `name (2).ext`, ... (a trailing .age stays last)."""
    is_age = path.lower().endswith(".age")
    base = path[:-4] if is_age else path
    ext = extension_of(base)
    stem = base[:-len(ext)] if ext else base
    i = 0
    while True:
        yield path if i == 0 else f"{stem} ({i}){ext}" + (".age" if is_age else "")
        i += 1


def create_part_file(final_path):
    """Create and open `<final>.part` (or `<final>.N.part`) exclusively, in one step.

    Returns (path, binary write handle). The same O_EXCL handle receives age's
    output, so no other program can have created or opened that file in between.
    A crash or power cut leaves an obvious ".part" file, never a truncated file
    under the real name.
    """
    i = 0
    while True:
        candidate = f"{final_path}.part" if i == 0 else f"{final_path}.{i}.part"
        try:
            return candidate, open(long_path(candidate), "xb")
        except (FileExistsError, IsADirectoryError, PermissionError):
            if not os.path.exists(long_path(candidate)):
                raise  # a real permission problem, not a name clash
        i += 1


def _retry_while_locked(action, tries=10, delay=0.1):
    """Run action, retrying briefly while Windows reports the file as in use
    (antivirus or the search indexer often open a new file for a moment)."""
    for attempt in range(tries):
        try:
            return action()
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32) or attempt == tries - 1:
                raise
            time.sleep(delay)


def publish_part_file(part_path, final_path):
    """Rename the finished .part file to the first free name and return that name.

    On Windows os.rename never replaces an existing file, so even a file created
    while the job ran is not overwritten.
    """
    for candidate in name_candidates(final_path):
        try:
            _retry_while_locked(lambda: os.rename(long_path(part_path), long_path(candidate)))
            return candidate
        except (FileExistsError, PermissionError):
            if not os.path.exists(long_path(candidate)):
                raise


SECRET_MARKERS = ("AGE-SECRET-KEY-", "AGE-PLUGIN-")


def contains_secret(line):
    upper = line.upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def looks_like_base64_blob(line):
    """A long run of base64 characters alone on a line, as in the body of a PEM key.
    age recipients (age1..., lowercase bech32) and "ssh-ed25519 AAAA..." don't match."""
    if len(line) < 40 or line.startswith("age1"):
        return False
    return all(c.isalnum() or c in "+/=" for c in line) and line.isascii()


def without_secrets(value):
    """What is safe to store in the settings file: no age secret keys, no PEM blocks
    (e.g. a pasted SSH private key) and no bare base64 blobs."""
    keep, in_pem = [], False
    for line in value.strip().splitlines():
        t = line.strip()
        if t.startswith("-----BEGIN"):
            in_pem = True
            continue
        if t.startswith("-----END"):
            in_pem = False
            continue
        if in_pem or contains_secret(t) or looks_like_base64_blob(t):
            continue
        keep.append(line.rstrip("\r"))
    return "\n".join(keep)


def looks_like_path(line):
    """A full Windows path ("C:\\..." or "\\\\server\\share\\..."): what Browse and key
    generation put in the boxes. Other text, say "correct:horse/battery" pasted into
    the wrong box, is not written to the settings file."""
    line = line.strip('"')
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", line)) and not looks_like_base64_blob(line)


def keep_lines(value, keep_line):
    """without_secrets, then only blank lines, # comments and lines keep_line accepts.
    Anything else typed into a box (say, a passphrase pasted into the wrong one) is
    not written to the settings file."""
    kept = []
    for line in without_secrets(value).splitlines():
        t = line.strip()
        if not t or t.startswith("#") or keep_line(t):
            kept.append(line)
    return "\n".join(kept).strip()


SUPPORTED_SSH_TYPES = ("ssh-ed25519", "ssh-rsa")


def is_pq_recipient(line):
    return line.startswith(("age1pq1", "age1tagpq1"))


def recipients_file_problem(path):
    """Problems age.exe would not report clearly, as (message or None, kinds).

    age skips SSH keys it can't use (ECDSA, ...) with only a warning, which leaves
    that person unable to open the file, so they block encryption here. kinds says
    whether the file holds post-quantum and/or classic keys (they can't be mixed).
    """
    kinds = set()
    try:
        with open(long_path(path), "r", encoding="utf-8-sig", errors="replace") as f:
            for n, raw in enumerate(f, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("AGE-") or line.startswith("-----BEGIN"):
                    return f"Recipients file line {n}: this is a secret key, not a recipient.", kinds
                key_type = line.split(" ", 1)[0]
                if (key_type.startswith(("ssh-", "ecdsa-", "sk-")) and key_type not in SUPPORTED_SSH_TYPES):
                    return (f"Recipients file line {n}: age can't encrypt to SSH keys of type {key_type}; "
                            "remove it or comment it out with #."), kinds
                kinds.add("pq" if is_pq_recipient(line) else "classic")
    except OSError as exc:
        return f"Recipients file can't be read: {exc.strerror or exc}", kinds
    return None, kinds


MIX_MESSAGE = ("Post-quantum (age1pq1...) and classic keys can't be mixed in one file: the classic key "
               "would make it breakable by a quantum computer. Use one kind.")
CHECKING_KEYS = "Checking the keys with age.exe..."
MAX_ROWS = 100  # rows drawn in the file list; Tk gets slow with many more (the rest are counted)


def decrypted_name(path):
    """`x.txt.age` -> `x.txt`; a file named exactly `.age` -> `.age.decrypted`."""
    if path.lower().endswith(".age") and os.path.basename(path)[:-4]:
        return path[:-4]
    return path + ".decrypted"


class AgeGui(ctk.CTk, TkinterDnD.DnDWrapper):
    SETTINGS_FILE = "pageSettings.json"

    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.set_window_icon()

        # One dict per row in the file list: path, size, status (ready / working / done /
        # failed), output (the file written) and error. "done" rows stay visible with their
        # result but are not processed again; "failed" rows are retried by the next run.
        self.entries = []
        self.row_widgets = {}
        self.mode = None
        self.tab = "Passphrase"
        self.is_processing = False
        self.cancel_event = threading.Event()
        self.current_proc = None
        self.worker_thread = None
        self.inflight_output = None
        self.last_summary = None
        self.notes = ""
        self.notes_warn = False
        self.notes_sticky = False  # a note a run doesn't clear (generated passphrase, settings)
        self.gen_armed = False
        self.show_pass = False  # read from here, not the entry: a placeholder reports show=""
        self.settings_notice = ""  # damaged / unsaveable settings: survives Clear
        self.ui_queue = queue.Queue()
        self.ui_backlog = collections.deque()
        self.add_queue = []  # drops waiting while an earlier one is still being read
        self.adding = False
        self.next_grid_row = 0
        self.more_label = None
        self.add_generation = 0  # Clear bumps it: a drop still being read then lands nowhere
        self._dry_run_cache = {}
        self._dry_run_busy = False  # one age.exe check at a time ...
        self._dry_run_next = None  # ... and only the latest waiting input is checked next
        self.name_labels = {}  # id(entry) -> (label, name): re-fitted when the list gets wider
        self._refit_job = None

        self.base_dir = self.get_base_dir()
        self.age_path = os.path.join(self.base_dir, "age.exe")
        self.batchpass_path = os.path.join(self.base_dir, "age-plugin-batchpass.exe")
        self.keygen_path = os.path.join(self.base_dir, "age-keygen.exe")
        self.inspect_path = os.path.join(self.base_dir, "age-inspect.exe")
        self.settings_path = os.path.join(self.base_dir, self.SETTINGS_FILE)

        # Proportional font for the interface, monospace only where paths and keys are shown.
        available_fonts = set(tkfont.families())
        self.ui_font = "Segoe UI" if "Segoe UI" in available_fonts else "TkDefaultFont"
        self.mono_font = "Consolas" if "Consolas" in available_fonts else "Courier New"
        font = (self.ui_font, 14)
        small_font = self.small_font = (self.ui_font, 13)
        bold_font = (self.ui_font, 14, "bold")
        mono = (self.mono_font, 13)

        # ---------------- Bottom bar ----------------
        # Packed first, against the bottom edge: Tk hands out space in packing order, so
        # the action button keeps its place however small the window gets.
        bottom = ctk.CTkFrame(self, corner_radius=0)
        bottom.pack(side="bottom", fill="x")
        self.progress = ctk.CTkProgressBar(bottom, height=4)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=16, pady=(10, 0))
        self.reset_progress()
        bar = ctk.CTkFrame(bottom, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(8, 12))
        bar.grid_columnconfigure(0, weight=1)
        hint_frame = ctk.CTkFrame(bar, fg_color="transparent")
        hint_frame.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.hint_label = ctk.CTkLabel(hint_frame, text="", anchor="w", justify="left", font=small_font)
        self.hint_label.pack(fill="x")
        self.wrap_to_width(self.hint_label, side_padding=0)
        self.cancel_btn = ctk.CTkButton(
            bar, text="Cancel", width=90, height=38, command=self.cancel, font=font,
            fg_color=DANGER, hover_color=DANGER_HOVER,
        )
        self.cancel_btn.grid(row=0, column=1, padx=(0, 10))
        self.action_btn = ctk.CTkButton(bar, text="Encrypt", width=190, height=38, fg_color=BLUE,
                                        hover_color=BLUE_HOVER, command=self.run_action,
                                        font=(self.ui_font, 14, "bold"))
        self.action_btn.grid(row=0, column=2)

        # ---------------- Passphrase / Keys ----------------
        card = ctk.CTkFrame(self, fg_color="transparent")
        card.pack(side="bottom", fill="x", padx=20)
        options = ctk.CTkFrame(card, fg_color="transparent")
        options.pack(side="bottom", pady=(14, 12))  # no fill: pack centres it
        self.armor_var = ctk.BooleanVar(value=False)
        self.armor_chk = ctk.CTkSwitch(options, text="ASCII armor   (text output for e-mail or chat)",
                                       variable=self.armor_var, command=self.save_settings, font=small_font,
                                       progress_color=BLUE, switch_width=38, switch_height=18)
        self.armor_chk.pack()

        tabs = ctk.CTkFrame(card, fg_color="transparent")
        tabs.pack(pady=(2, 10))
        self.tab_buttons = {}
        for name, label in (("Passphrase", "Passphrase"), ("Recipient", "Keys")):
            col = ctk.CTkFrame(tabs, fg_color="transparent")
            col.pack(side="left", padx=11)
            btn = ctk.CTkButton(col, text=label, width=10, height=28, fg_color="transparent",
                                hover_color=("gray85", "#2d2d2d"), text_color=MUTED, font=font,
                                command=lambda n=name: self.set_tab(n))
            btn.pack()
            # As wide as the label, like sage's tab underline (about 8.5 px per character at 14 pt).
            underline = ctk.CTkFrame(col, width=int(len(label) * 8.5) + 6, height=2, corner_radius=0,
                                     fg_color="transparent")
            underline.pack()
            self.tab_buttons[name] = (btn, underline)
        self.panel_area = ctk.CTkFrame(card, fg_color="transparent")
        self.panel_area.pack(fill="x")

        # Passphrase panel: centred fields, an eye button inside the first one.
        self.passphrase_box = ctk.CTkFrame(self.panel_area, fg_color="transparent")
        pw_wrap = ctk.CTkFrame(self.passphrase_box, fg_color="transparent")
        pw_wrap.pack(pady=(0, 8))
        self.password = ctk.CTkEntry(pw_wrap, placeholder_text="Passphrase", show="*",
                                     width=340, height=36, font=font)
        self.password.pack()
        # The eye sits on top of the entry, so it takes the entry's own colours.
        entry_fg = ctk.ThemeManager.theme["CTkEntry"]["fg_color"]
        self.eye_btn = ctk.CTkButton(pw_wrap, **self.icon_kw("eye", ICON_GREY, "Show"), width=30, height=26,
                                     fg_color=entry_fg, bg_color=entry_fg, hover_color=("gray85", "#4a4d50"),
                                     command=self.toggle_show_password, font=(self.ui_font, 11))
        self.eye_btn.place(relx=1.0, x=-5, rely=0.5, anchor="e")
        self.keep_text_clear_of_eye()
        self.confirm = ctk.CTkEntry(self.passphrase_box, placeholder_text="Repeat passphrase", show="*",
                                    width=340, height=36, font=font)
        self.confirm.pack(pady=(0, 6))
        self.gen_btn = ctk.CTkButton(self.passphrase_box, text="Generate a strong passphrase", width=10, height=24,
                                     fg_color="transparent", hover_color=("gray85", "#2d2d2d"),
                                     text_color=ACCENT, font=(self.ui_font, 13), command=self.generate_passphrase)
        self.gen_btn.pack()
        for entry in (self.password, self.confirm):
            self.bind_input_change(entry, self.on_passphrase_typed)
            entry.bind("<Return>", lambda _e: self.run_action())

        # Keys panel
        recipient_tab = self.recipient_tab = ctk.CTkFrame(self.panel_area, fg_color="transparent")
        row = ctk.CTkFrame(recipient_tab, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Encrypt to", font=bold_font).pack(side="left")
        ctk.CTkLabel(row, text="public keys, one per line \u00b7 # comment",
                     font=small_font, text_color=MUTED).pack(side="left", padx=8)
        self.help_btn = ctk.CTkButton(row, text="?", width=28, height=26, command=self.show_help,
                                      font=bold_font, **GHOST)
        self.help_btn.pack(side="right")
        self.recipient_box = ctk.CTkTextbox(recipient_tab, height=52, font=mono, wrap="none")
        self.recipient_box.pack(fill="x", pady=(4, 6))
        self.bind_input_change(self.recipient_box, self.on_recipient_inputs_changed)

        rf_frame = ctk.CTkFrame(recipient_tab, fg_color="transparent")
        rf_frame.pack(fill="x")
        rf_frame.grid_columnconfigure(0, weight=1)
        self.recipients_file = ctk.CTkEntry(rf_frame, placeholder_text="...and/or a recipients file (-R)",
                                            height=32, font=mono)
        self.recipients_file.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.bind_input_change(self.recipients_file, self.on_recipient_inputs_changed)
        self.recipients_file_btn = ctk.CTkButton(rf_frame, text="Browse...", width=96, height=32,
                                                 command=self.browse_recipients_file, font=small_font, **SECONDARY)
        self.recipients_file_btn.grid(row=0, column=1)

        row = ctk.CTkFrame(recipient_tab, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row, text="Decrypt with", font=bold_font).pack(side="left")
        ctk.CTkLabel(row, text="identity (secret key) files, one path per line",
                     font=small_font, text_color=MUTED).pack(side="left", padx=8)
        self.identity_file_btn = ctk.CTkButton(row, text="Add file...", width=96, height=28,
                                               command=self.browse_identity_file, font=small_font, **SECONDARY)
        self.identity_file_btn.pack(side="right")
        self.identity_box = ctk.CTkTextbox(recipient_tab, height=52, font=mono, wrap="none")
        self.identity_box.pack(fill="x", pady=(4, 8))
        self.bind_input_change(self.identity_box, self.on_recipient_inputs_changed)

        keygen_frame = ctk.CTkFrame(recipient_tab, fg_color="transparent")
        keygen_frame.pack(fill="x")
        self.keygen_btn = ctk.CTkButton(keygen_frame, text="Generate new key pair", height=30,
                                        command=self.generate_key, font=small_font, **SECONDARY)
        self.keygen_btn.pack(side="left")
        self.pq_var = ctk.BooleanVar(value=False)
        self.pq_chk = ctk.CTkSwitch(keygen_frame, text="Post-quantum", variable=self.pq_var,
                                    command=self.save_settings, font=small_font, progress_color=BLUE,
                                    switch_width=38, switch_height=18)
        self.pq_chk.pack(side="left", padx=14)

        # ---------------- Toolbar ----------------
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(side="top", fill="x", padx=12, pady=(8, 0))
        white = "#ffffff"
        self.browse_btn = ctk.CTkButton(toolbar, text="Files", image=self.icon("add-file", white), compound="left",
                                        width=88, height=32, fg_color=BLUE, hover_color=BLUE_HOVER,
                                        font=small_font, command=self.browse)
        self.browse_btn.pack(side="left", padx=(0, 6))
        self.add_folder_btn = ctk.CTkButton(toolbar, text="Folder", image=self.icon("add-folder", white),
                                            compound="left", width=96, height=32, fg_color=BLUE,
                                            hover_color=BLUE_HOVER, font=small_font, command=self.browse_folder)
        self.add_folder_btn.pack(side="left")
        ctk.CTkFrame(toolbar, width=1, height=22, fg_color=LINE).pack(side="left", padx=10)
        text_hex = "#ebebeb"
        self.clear_btn = ctk.CTkButton(toolbar, text="Clear", image=self.icon("trash", text_hex), compound="left",
                                       width=80, height=32, command=self.clear, font=small_font, **GHOST)
        self.clear_btn.pack(side="left")
        self.inspect_btn = ctk.CTkButton(toolbar, text="Inspect", image=self.icon("search", text_hex),
                                         compound="left", width=88, height=32, command=self.inspect,
                                         font=small_font, **GHOST)
        self.inspect_btn.pack(side="left", padx=(2, 0))
        self.about_btn = ctk.CTkButton(toolbar, **self.icon_kw("info", text_hex, "i"), width=32, height=32,
                                       command=self.show_about, font=bold_font, **GHOST)
        self.about_btn.pack(side="right")

        # Running as administrator: a warning dialog at start (like About), and an amber
        # "Admin" pill in the toolbar that reopens it, so a later blocked drag still has
        # an explanation on screen.
        self.elevated = running_elevated()
        if self.elevated:
            self.admin_chip = ctk.CTkButton(toolbar, text="Admin", width=60, height=24, corner_radius=12,
                                            border_width=2, border_color=WARN, fg_color="transparent",
                                            hover_color=("#f6e3cc", "#3a2c1a"), text_color=WARN,
                                            font=(self.ui_font, 12, "bold"), command=self.show_admin_warning)
            self.admin_chip.pack(side="right", padx=(0, 8))
        ctk.CTkFrame(self, height=1, corner_radius=0, fg_color=LINE).pack(side="top", fill="x", pady=(8, 0))

        # ---------------- File list ----------------
        files_frame = ctk.CTkFrame(self, fg_color="transparent")
        files_frame.pack(side="top", fill="both", expand=True, padx=12)
        head = ctk.CTkFrame(files_frame, fg_color="transparent", height=30)
        head.pack(fill="x")
        self.configure_row_grid(head)
        for col, text in ((1, "Name"), (2, "Folder"), (3, "Size")):
            # The Folder/Result column is as wide as the rows' cells (150, scaled alike), so they line up.
            lbl = ctk.CTkLabel(head, text=text, font=(self.ui_font, 12), text_color=MUTED,
                               anchor="e" if col == 3 else "w", **({"width": 150} if col == 2 else {}))
            lbl.grid(row=0, column=col, sticky="ew", padx=(0, 8) if col == 3 else 0, pady=2)
            if col == 2:
                self.col_second = lbl
        ctk.CTkFrame(files_frame, height=1, corner_radius=0, fg_color=LINE).pack(fill="x")
        # The lines under the list are packed (from the bottom up) before the list itself:
        # Tk squeezes the last-packed widget first, and that must be the list, not them.
        notes_box = ctk.CTkFrame(files_frame, fg_color="transparent")
        notes_box.pack(side="bottom", fill="x", padx=8, pady=(0, 14))
        self.notes_label = ctk.CTkLabel(notes_box, text="", anchor="w", justify="left",
                                        font=(self.ui_font, 12), text_color=MUTED)
        self.notes_label.pack(fill="x")
        # A long note (many unreadable paths, age's warnings, failures) is previewed in a
        # few lines; this opens all of it.
        self.notes_more = ctk.CTkButton(notes_box, text="Show all", width=10, height=20, fg_color="transparent",
                                        hover_color=("gray85", "#2d2d2d"), text_color=ACCENT,
                                        font=(self.ui_font, 12), command=self.show_all_notes)
        self.summary_label = ctk.CTkLabel(files_frame, text="", anchor="w", justify="left",
                                          font=(self.ui_font, 12), text_color=MUTED)
        self.summary_label.pack(side="bottom", fill="x", padx=8, pady=(6, 0))
        ctk.CTkFrame(files_frame, height=1, corner_radius=0, fg_color=LINE).pack(side="bottom", fill="x")
        self.list_frame = ctk.CTkScrollableFrame(files_frame, fg_color="transparent", corner_radius=0, height=80)
        self.list_frame.pack(fill="both", expand=True)
        self.list_frame.grid_columnconfigure(0, weight=1)
        for label in (self.summary_label, self.notes_label):
            self.wrap_to_width(label, side_padding=16)

        # Files can be dropped anywhere on the window.
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self.on_drop)
        self.dnd_bind("<<DropEnter>>", lambda e: self.set_drop_highlight(True, e))
        self.dnd_bind("<<DropLeave>>", lambda e: self.set_drop_highlight(False, e))
        # A tooltip goes away on scroll, focus loss (Alt-Tab) and minimise, not only on mouse-out.
        self.bind_all("<MouseWheel>", Tooltip.hide_all, add="+")
        for seq in ("<FocusOut>", "<Unmap>"):
            self.bind(seq, Tooltip.hide_all, add="+")
        # Names are cut to the list's width; re-cut them when it changes (window resized).
        self.list_frame._parent_canvas.bind("<Configure>", self.schedule_refit_names, add="+")

        self.load_settings()
        self.show_standing_notes()
        self.set_tab(self.tab, save=False)
        self.render_list()
        self.set_initial_geometry()
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self.process_ui_queue)
        if self.elevated:
            self.after(400, self.show_admin_warning)  # once the main window is on screen

    # ------------------------------------------------------------------ helpers

    def bind_input_change(self, widget, callback):
        # KeyRelease alone misses mouse paste / context-menu paste.
        handler = lambda _e=None: self.after_idle(callback)
        for seq in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<FocusOut>"):
            widget.bind(seq, handler, add="+")

    def wrap_to_width(self, label, side_padding=20):
        """Wrap a label's text at the width its container gives it, so it wraps instead of being cut off.

        Watches the container, not the label: the label's own width changes with its
        wraplength, which would trigger an endless resize loop.
        """
        container = label.master

        def on_configure(event):
            # CTkFrame.bind attaches to the frame's background canvas, which always
            # spans the whole frame, so event.width is the container's width.
            try:
                scaling = ctk.ScalingTracker.get_widget_scaling(label)
            except Exception:
                scaling = 1.0
            # CTkLabel scales wraplength itself, so work in unscaled units.
            wrap = max(50, int(event.width / scaling) - side_padding - 10)
            if label.cget("wraplength") != wrap:
                label.configure(wraplength=wrap)

        container.bind("<Configure>", on_configure, add="+")

    def icon(self, name, color, size=16):
        """An icon image (cached), or None if the icon font isn't there."""
        key = (name, color, size)
        cache = self.__dict__.setdefault("_icons", {})
        if key not in cache:
            cache[key] = glyph_image(name, color, size)
        return cache[key]

    def icon_kw(self, name, color, fallback, size=16):
        """image= for a button, or a text label when the icon font is missing (no blank buttons)."""
        image = self.icon(name, color, size)
        return {"image": image, "text": ""} if image is not None else {"image": None, "text": fallback}

    def widget_scaling(self):
        try:
            return ctk.ScalingTracker.get_widget_scaling(self)
        except Exception:
            return 1.0

    def keep_text_clear_of_eye(self):
        """A long passphrase must not run under the eye button: pad the entry's text on the right.
        CTkEntry re-grids its inner entry on a DPI change, so this is re-applied on <Configure>."""
        try:
            inner = self.password._entry
            pad = inner.grid_info().get("padx", 6)
            pad = [int(p) for p in (pad if isinstance(pad, (tuple, list)) else str(pad).split())]
            current = (pad[0], pad[-1])  # Tk reports one number when both sides are equal
            want = (pad[0], round(38 * self.widget_scaling()))
            if current != want:  # no change, no new <Configure>: no loop
                inner.grid_configure(padx=want)
            if not getattr(self, "_eye_pad_bound", False):
                self._eye_pad_bound = True
                self.password.bind("<Configure>", lambda _e: self.keep_text_clear_of_eye(), add="+")
        except Exception:
            pass  # a different customtkinter: the text may run under the eye, nothing worse

    def show_standing_notes(self):
        """The notes that stay until fixed: missing age tools, a settings problem."""
        text = "\n".join(t for t in (self.missing_tools_note(), self.settings_notice) if t)
        self.set_notes(text, warn=bool(text), sticky=bool(text))

    def show_all_notes(self):
        self.show_info("Details", self.notes)

    @staticmethod
    def configure_row_grid(frame):
        """The file list's columns: icon, name, folder or result, size, buttons."""
        frame.grid_columnconfigure(0, minsize=30)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(2, minsize=160)
        frame.grid_columnconfigure(3, minsize=64)
        frame.grid_columnconfigure(4, minsize=66)

    def usable_screen(self):
        """(x, y, width, height) of the screen area not covered by the taskbar, in physical pixels."""
        if sys.platform == "win32":
            try:
                import ctypes

                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                r = RECT()
                if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):  # SPI_GETWORKAREA
                    return r.left, r.top, r.right - r.left, r.bottom - r.top
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def set_initial_geometry(self):
        """One size for both tabs (no jumping on tab switch), fitted to the usable screen and centred.

        CTk multiplies width/height in geometry() by the DPI scaling but not x/y, so the
        size is given in unscaled units and the position in physical pixels.
        """
        try:
            scaling = ctk.ScalingTracker.get_window_scaling(self)
        except Exception:
            scaling = 1.0
        sx, sy, sw, sh = self.usable_screen()
        title_bar = int(40 * scaling)
        # Same size as sage (590 x 720), so the two apps look alike.
        width = min(590, int(sw / scaling) - 20)
        height = min(720, int((sh - title_bar) / scaling) - 10)
        self.minsize(min(520, width), min(540, height))
        x = sx + max(0, (sw - int(width * scaling)) // 2)
        y = sy + max(0, (sh - int(height * scaling) - title_bar) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        # Tk sizes the inside of the window, Wails (sage) the whole window including
        # frame and title bar. Subtract page's frame so both windows are the same size.
        frame_w, frame_h = self.window_frame_size()
        if frame_w or frame_h:
            inner_w = round((width * scaling - frame_w) / scaling)
            inner_h = round((height * scaling - frame_h) / scaling)
            self.geometry(f"{inner_w}x{inner_h}+{x}+{y}")

    def window_frame_size(self):
        """(width, height) in physical pixels that the frame and title bar add."""
        if sys.platform != "win32":
            return 0, 0
        try:
            import ctypes
            from ctypes import wintypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or int(self.wm_frame(), 16)
            outer, inner = wintypes.RECT(), wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(outer))
            ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(inner))
            fw = (outer.right - outer.left) - (inner.right - inner.left)
            fh = (outer.bottom - outer.top) - (inner.bottom - inner.top)
            return (fw, fh) if 0 <= fw < 200 and 0 <= fh < 200 else (0, 0)
        except Exception:
            return 0, 0

    def set_window_icon(self):
        """Title bar / taskbar icon. Bundled inside the exe (sys._MEIPASS) or next to the script."""
        folder = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        icon = os.path.join(folder, "icon.ico")
        if os.path.isfile(icon):
            try:
                self.iconbitmap(icon)  # also stops CTk replacing it with its default icon
            except Exception:
                pass

    def get_base_dir(self):
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def active_mode_kind(self):
        return "recipient" if self.tab == "Recipient" else "passphrase"

    def toggle_show_password(self, show=None):
        # The state is kept here: while the placeholder is showing, CTkEntry reports
        # show="" whatever was set, so reading it back made the first click do nothing.
        self.show_pass = (not self.show_pass) if show is None else bool(show)
        for entry in (self.password, self.confirm):
            entry.configure(show="" if self.show_pass else "*")
        kw = self.icon_kw("eye", ACCENT[1] if self.show_pass else ICON_GREY, "Hide" if self.show_pass else "Show")
        self.eye_btn.configure(**kw)

    def pending_files(self):
        """The files the next run will process (finished rows are not run again)."""
        return [e["path"] for e in self.entries if e["status"] != "done"]

    def parse_multiline_lines(self, widget):
        lines = []
        for raw in widget.get("0.0", "end").splitlines():
            line = raw.strip().strip('"')
            if line and not line.startswith("#"):
                lines.append(line)
        return lines

    def classify_recipient(self, line):
        for prefix, kind in RECIPIENT_PREFIXES:
            if line.startswith(prefix):
                return kind
        return None

    def get_recipient_inputs(self):
        recipients = self.parse_multiline_lines(self.recipient_box)
        recipients_file = self.recipients_file.get().strip().strip('"')
        identities = self.parse_multiline_lines(self.identity_box)
        valid_recipients = [r for r in recipients if self.classify_recipient(r)]
        # Full paths: a relative one would depend on the working folder, and a file
        # literally named "-" must not reach age as "-" (which means standard input).
        if recipients_file:
            recipients_file = os.path.abspath(recipients_file)
        recipients_file_ok = bool(recipients_file) and os.path.isfile(long_path(recipients_file))
        valid_identities = [os.path.abspath(i) for i in identities
                            if not contains_secret(i) and os.path.isfile(long_path(os.path.abspath(i)))]
        return recipients, valid_recipients, recipients_file, recipients_file_ok, identities, valid_identities

    def recipient_encrypt_problem(self):
        """Why recipient-mode encryption must not run, or None.

        Anything the user listed but age would not get (a mistyped key, a missing
        recipients file) blocks encryption: silently skipping it would produce files
        that person can never decrypt.
        """
        recipients, valid_r, rfile, rfile_ok, _, _ = self.get_recipient_inputs()
        if any(contains_secret(r) for r in recipients):
            return "A secret key was pasted into Recipients. Use its public key (age1...) instead."
        invalid = len(recipients) - len(valid_r)
        if invalid:
            return f"{invalid} recipient line(s) are not a recognized age/SSH public key. Fix or remove them (or comment them out with #)."
        if rfile and not rfile_ok:
            return "Recipients file not found. Fix the path or clear the field."
        if not valid_r and not rfile_ok:
            return "Add at least one recipient or a recipients file."
        kinds = {"pq" if is_pq_recipient(r) else "classic" for r in valid_r}
        if rfile_ok:
            problem, file_kinds = recipients_file_problem(rfile)
            if problem:
                return problem
            kinds |= file_kinds
        if len(kinds) > 1:
            # age.exe would refuse this for every file; say so before the run.
            return MIX_MESSAGE
        return self.recipients_dry_run_problem(valid_r, rfile if rfile_ok else "")

    def recipients_dry_run_problem(self, recipients, recipients_file):
        """Ask age.exe itself whether it accepts these recipients, by encrypting
        nothing to nowhere. It catches what prefix checks can't: mistyped keys, plugin
        keys without their plugin, and SSH keys age only warns about and skips (short
        RSA keys in a recipients file), which would leave that person out.

        Cached on the inputs (and the recipients file's size and time), so typing
        doesn't start age.exe on every keystroke for the same content."""
        if not os.path.isfile(self.age_path):
            return None
        stamp = None
        if recipients_file:
            try:
                st = os.stat(long_path(recipients_file))
                stamp = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
        key = (tuple(recipients), recipients_file, stamp)
        if key in self._dry_run_cache:
            return self._dry_run_cache[key]
        # age.exe runs in the background (a recipients file on a sleeping network drive
        # must not freeze the window); its answer arrives as a "dry_run" UI message.
        # One check at a time: while typing, only the latest text waits for its turn.
        job = (key, list(recipients), recipients_file)
        if self._dry_run_busy:
            self._dry_run_next = job
        else:
            self.start_dry_run(job)
        return CHECKING_KEYS

    def start_dry_run(self, job):
        key, recipients, recipients_file = job
        self._dry_run_busy = True
        age_path = self.age_path

        def work():
            try:
                problem = age_dry_run(age_path, recipients, recipients_file)
            except Exception:  # an odd paste (NUL, lone surrogate): the run reports it per file
                problem = None
            self.ui_queue.put(("dry_run", key, problem))  # always, or the hint would stay "Checking..."

        threading.Thread(target=work, daemon=True).start()

    def recipient_mode_hint(self):
        """(ready, hint, is_warning) for recipient mode; shown in the bottom bar next to the button."""
        recipients, valid_r, rfile, rfile_ok, identities, valid_i = self.get_recipient_inputs()
        if self.mode == "encrypt":
            problem = self.recipient_encrypt_problem()
            if problem:
                return False, problem, problem != CHECKING_KEYS and bool(recipients or rfile)
            parts = []
            if valid_r:
                kinds = ", ".join(sorted({self.classify_recipient(r) for r in valid_r}))
                parts.append(f"{len(valid_r)} recipient(s) ({kinds})")
            if rfile_ok:
                parts.append("the recipients file")
            return True, "Ready: encrypting to " + " + ".join(parts) + ".", False

        if any(contains_secret(i) for i in identities):
            return False, "A secret key was pasted into 'Decrypt with'. Enter the key FILE path instead.", True
        missing = len(identities) - len(valid_i)
        if not valid_i:
            if missing:
                return False, f"Identity file not found ({missing} path(s)). Check the paths.", True
            return False, "Add an identity file under 'Decrypt with'.", False
        note = f" ({missing} missing path(s) ignored.)" if missing else ""
        return True, f"Ready: decrypting with {len(valid_i)} identity file(s).{note}", False

    def on_recipient_inputs_changed(self):
        self.save_settings()
        self.update_action_buttons()

    # ------------------------------------------------------------------ file list

    def missing_tools_note(self):
        """Which age tools are missing next to page, or "" when all are there."""
        missing = [
            f"MISSING {os.path.basename(path)} ({need})"
            for path, need in (
                (self.age_path, "required"),
                (self.batchpass_path, "needed for the Passphrase tab"),
                (self.keygen_path, "needed for key generation"),
                (self.inspect_path, "needed for Inspect"),
            )
            if not os.path.isfile(path)
        ]
        return "\n".join(missing)

    def render_list(self):
        """Rebuild the list: on add and Clear only. A run's progress and results update
        single rows (update_row). At most MAX_ROWS rows are drawn, each is about ten Tk
        widgets; the rest are counted in a line at the end."""
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.row_widgets = {}
        self.name_labels = {}
        self.next_grid_row = 0
        self.more_label = None
        self.update_column_header()
        if not self.entries:
            empty = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            empty.grid(row=0, column=0, sticky="nsew", pady=(30, 10))
            image = self.icon("add-file", ACCENT[1], 30)
            ctk.CTkLabel(empty, text="", image=image).pack()
            ctk.CTkLabel(empty, text="Drop files or folders here", font=(self.ui_font, 14, "bold")).pack(pady=(6, 2))
            for line in (".age files are decrypted, everything else is encrypted.",
                         "Output goes next to each file; existing files are never overwritten."):
                ctk.CTkLabel(empty, text=line, font=(self.ui_font, 13), text_color=MUTED).pack()
            return
        for entry in self.entries[:MAX_ROWS]:
            self.build_row(entry)
        self.update_more_label()

    def update_more_label(self):
        hidden = len(self.entries) - len(self.row_widgets)
        if hidden <= 0:
            if self.more_label is not None:
                self.more_label.destroy()
                self.more_label = None
            return
        text = (f"...and {hidden} more file{'' if hidden == 1 else 's'}, not shown. "
                "They are processed like the others; the line below counts them.")
        if self.more_label is None:
            self.more_label = ctk.CTkLabel(self.list_frame, text=text, font=(self.ui_font, 12),
                                           text_color=MUTED, anchor="w")
        self.more_label.configure(text=text)
        # After the last row (Tk grids have at most about 10000 rows, so no fixed huge index);
        # build_row takes this slot for a new row, and this is called again after it.
        self.more_label.grid(row=self.next_grid_row, column=0, sticky="ew", padx=8, pady=(6, 4))

    def cell(self, text, max_px, middle=True):
        """Text shortened to fit max_px (unscaled) in the list's font."""
        scaling = self.widget_scaling()
        if getattr(self, "_cell_scaling", None) != scaling:  # moved to a monitor with another DPI
            self._cell_scaling = scaling
            self.cell_font = tkfont.Font(family=self.ui_font, size=-round(13 * scaling))
        return fit_width(text, self.cell_font.measure, max_px * scaling, middle)

    def build_row(self, entry, grid_row=None):
        """Draw one row. grid_row: where it goes (an updated row keeps its place)."""
        if grid_row is None:
            grid_row = self.next_grid_row
            self.next_grid_row += 2
        status = entry["status"]
        row = ctk.CTkFrame(self.list_frame, fg_color="transparent", corner_radius=4, height=34)
        row.grid(row=grid_row, column=0, sticky="ew")
        self.configure_row_grid(row)
        name = os.path.basename(entry["path"])
        icon_name, icon_color, mark = {
            "done": ("check", OK_COLOR[1], "\u2713"), "failed": ("error", ERR_COLOR[1], "!"),
            "working": ("working", ACCENT[1], "\u2026"),
        }.get(status, ("lock" if entry["path"].lower().endswith(".age") else "file", ICON_GREY, ""))
        image = self.icon(icon_name, icon_color)
        ctk.CTkLabel(row, text="" if image else mark, image=image, width=24, text_color=icon_color).grid(
            row=0, column=0, padx=(6, 0))
        name_lbl = ctk.CTkLabel(row, text=self.cell(name, self.name_width()), font=(self.ui_font, 13), anchor="w")
        self.name_labels[id(entry)] = (name_lbl, name)
        name_lbl.grid(row=0, column=1, sticky="ew", padx=(4, 6))
        if status == "done":
            output = entry.get("output") or ""
            detail, color, tip = "\u2192 " + self.cell(os.path.basename(output), 128), MUTED, "Written: " + output
        elif status == "failed":
            error = entry.get("error") or "Failed"
            # The start of the reason in the row; all of it on hover and under the list.
            detail, color, tip = self.cell(error.splitlines()[0], 140, middle=False), ERR_COLOR, error
        else:
            detail, color, tip = self.cell(folder_of(entry["path"]), 140), MUTED, entry["path"]
        # Text cut to 140 inside a 150 label: a gap before the size column.
        detail_lbl = ctk.CTkLabel(row, text=detail, font=(self.ui_font, 13), text_color=color, anchor="w",
                                  width=150)
        detail_lbl.grid(row=0, column=2, sticky="ew")
        ctk.CTkLabel(row, text=format_size(entry.get("size")), font=(self.ui_font, 13), text_color=MUTED,
                     anchor="e").grid(row=0, column=3, sticky="ew", padx=(0, 8))
        actions = ctk.CTkFrame(row, fg_color="transparent")
        actions.grid(row=0, column=4, sticky="e", padx=(0, 4))
        if status == "done":
            ctk.CTkButton(actions, **self.icon_kw("folder-open", ICON_GREY, "\u2197"), width=28, height=28,
                          command=lambda e=entry: self.show_in_folder(e), **GHOST).pack(side="left")
        remove_btn = ctk.CTkButton(actions, **self.icon_kw("x", ICON_GREY, "\u00d7", 12), width=28, height=28,
                                   state="disabled" if self.is_processing else "normal",
                                   command=lambda e=entry: self.remove_entry(e), **GHOST)
        remove_btn.pack(side="left", padx=(2, 0))
        line = ctk.CTkFrame(self.list_frame, height=1, corner_radius=0, fg_color=LINE)
        line.grid(row=grid_row + 1, column=0, sticky="ew")
        # Row hover, like a list in Explorer.
        widgets = [row] + list(row.winfo_children())
        for w in widgets:
            w.bind("<Enter>", lambda _e, r=row: r.configure(fg_color=ROW_HOVER), add="+")
            w.bind("<Leave>", lambda _e, r=row: r.configure(fg_color="transparent"), add="+")
        Tooltip([name_lbl], entry["path"])
        if tip:
            Tooltip([detail_lbl], tip)
        self.row_widgets[id(entry)] = (row, line, remove_btn)

    def name_width(self):
        """The name gets what the other columns leave (icon 30, result 150, size 64, buttons 66,
        paddings); measured on screen: the name column is the list's width minus about 280."""
        list_w = self.list_frame._parent_canvas.winfo_width() / self.widget_scaling()
        return max(140, int(list_w) - 290) if list_w > 100 else 220

    def schedule_refit_names(self, _e=None):
        if self._refit_job:
            self.after_cancel(self._refit_job)
        self._refit_job = self.after(200, self.refit_names)

    def refit_names(self):
        """Only the name labels' text changes: much cheaper than redrawing the rows."""
        self._refit_job = None
        width = self.name_width()
        if getattr(self, "_names_fitted_to", None) == width:
            return
        self._names_fitted_to = width
        for label, name in list(self.name_labels.values()):
            try:
                label.configure(text=self.cell(name, width))
            except Exception:
                pass  # a row destroyed meanwhile

    def update_row(self, entry):
        """Redraw one row in place (a file's result or progress). Rows past MAX_ROWS aren't drawn."""
        old = self.row_widgets.pop(id(entry), None)
        self.name_labels.pop(id(entry), None)
        if old is not None:
            grid_row = old[0].grid_info().get("row", self.next_grid_row)
            for w in old[:2]:
                w.destroy()
            self.build_row(entry, int(grid_row))
        self.update_column_header()

    def set_rows_enabled(self, enabled):
        """The \u00d7 buttons are off while a job runs (instead of redrawing every row)."""
        for _row, _line, remove_btn in self.row_widgets.values():
            remove_btn.configure(state="normal" if enabled else "disabled")

    def update_column_header(self):
        # After a run the second column shows each file's result instead of its folder.
        ran = sum(1 for e in self.entries if e["status"] in ("done", "failed"))
        self.col_second.configure(text="Folder" if ran == 0 else "Result" if ran == len(self.entries)
                                  else "Folder / result")

    def remove_entry(self, entry):
        if self.is_processing:
            return
        self.entries = [e for e in self.entries if e is not entry]
        # The last run's "Finished: 3 OK, 1 failed" no longer matches the list.
        self.last_summary = None
        if not self.entries:
            self.render_list()  # the empty-list hint
        else:
            old = self.row_widgets.pop(id(entry), None)
            self.name_labels.pop(id(entry), None)
            if old is not None:
                for w in old[:2]:
                    w.destroy()
                # A row that was past MAX_ROWS moves up into view.
                if len(self.entries) >= MAX_ROWS:
                    self.build_row(self.entries[MAX_ROWS - 1])
            self.update_more_label()
            self.update_column_header()
        self.update_mode_from_files()
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

    def show_in_folder(self, entry):
        """Show the written file in Explorer, selected."""
        path = entry.get("output") or ""
        if not os.path.isfile(long_path(path)):
            self.set_notes(f"{os.path.basename(path)} isn't there any more.", warn=True)
            return
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        # Explorer parses its own command line: the path must be quoted even without
        # spaces, or a comma in it splits it; and it doesn't take \\?\ paths.
        try:
            subprocess.Popen(f'"{os.path.join(windir, "explorer.exe")}" /select,"{without_long_prefix(path)}"')
        except OSError as exc:
            self.set_notes(f"Explorer couldn't be started: {exc.strerror or exc}", warn=True)

    def set_notes(self, text, warn=False, sticky=False):
        self.notes, self.notes_warn, self.notes_sticky = text, warn, sticky and bool(text)
        self.render_summary()

    def render_summary(self):
        todo = self.pending_files()
        if self.last_summary:
            line = self.last_summary
        elif todo:
            size = sum(e.get("size") or 0 for e in self.entries if e["status"] != "done")
            verb = "decrypted" if self.mode == "decrypt" else "encrypted"
            line = f"{len(todo)} file{'' if len(todo) == 1 else 's'} will be {verb} \u00b7 {format_size(size)}"
        else:
            line = ""
        self.summary_label.configure(text=line)
        self.summary_label.pack_configure(pady=(6, 0) if line else (0, 0))
        # A preview of at most 3 lines (each kept to one line), so a long note can't
        # squeeze the file list; "Show all" opens the whole text.
        lines = self.notes.splitlines()
        long_note = len(lines) > 3 or any(len(l) > 110 for l in lines)
        text = "\n".join(fit_text(l, 110) for l in lines[:3])
        self.notes_label.configure(text=text, text_color=WARN if self.notes_warn else MUTED)
        if long_note:
            more = len(lines) - 3
            self.notes_more.configure(text=f"Show all ({more} more line{'' if more == 1 else 's'})"
                                      if more > 0 else "Show all")
            self.notes_more.pack(anchor="w")
        else:
            self.notes_more.pack_forget()

    def process_ui_queue(self):
        """Apply the workers' messages, for at most about 40 ms per call, so the window
        (and Cancel) stays responsive when files finish faster than rows can be drawn."""
        try:
            while True:
                self.ui_backlog.append(self.ui_queue.get_nowait())
        except queue.Empty:
            pass
        deadline = time.monotonic() + 0.04
        try:
            while self.ui_backlog and time.monotonic() < deadline:
                item = self.ui_backlog.popleft()
                try:
                    self.handle_ui_message(item)
                except Exception as exc:
                    print(f"UI update failed: {exc!r}", file=sys.stderr)
        finally:
            # Always reschedule: if this loop died, the UI would stay locked forever.
            self.after(10 if self.ui_backlog else 50, self.process_ui_queue)

    def handle_ui_message(self, item):
        kind = item[0]
        if kind == "current":
            # Skipped when the file's result is already waiting: no point drawing "working".
            if any(i[0] == "result" and i[1] == item[1] for i in self.ui_backlog):
                return
            entry = self.find_entry(item[1])
            if entry is not None and entry["status"] == "ready":
                for e in self.entries:
                    if e["status"] == "working":
                        e["status"] = "ready"
                        self.update_row(e)
                entry["status"] = "working"
                self.update_row(entry)
        elif kind == "result":
            self.apply_result(*item[1:])
        elif kind == "progress":
            if any(i[0] == "progress" for i in self.ui_backlog):
                return  # a newer one is waiting
            done, total, name = item[1], item[2], item[3]
            self.progress.set(done / total if total else 0)
            if name and not self.cancel_event.is_set():  # keep "Cancelling..." on screen
                self.hint_label.configure(text=f"Working on file {min(done + 1, total)} of {total}: {name}",
                                          text_color=MUTED)
        elif kind == "info":
            self.show_info(item[1], item[2])
        elif kind == "notes":
            self.set_notes(item[1], warn=item[2])
        elif kind == "done":
            self.finish_job(summary=item[1])
        elif kind == "added":
            self.adding = False
            try:
                if item[1] == self.add_generation:  # not from before a Clear
                    self.apply_added(*item[2:])
            finally:
                if self.add_queue:
                    self.start_next_add()
                elif not self.is_processing:
                    self.update_action_buttons()
        elif kind == "dry_run":
            key, problem = item[1], item[2]
            self._dry_run_busy = False
            if len(self._dry_run_cache) > 50:
                self._dry_run_cache.clear()
            self._dry_run_cache[key] = problem
            job, self._dry_run_next = self._dry_run_next, None
            if job and job[0] not in self._dry_run_cache:
                self.start_dry_run(job)
            if not self.is_processing:
                self.update_action_buttons()

    def find_entry(self, path):
        return next((e for e in self.entries if e["path"] == path), None)

    def apply_result(self, path, ok, output, error, cancelled):
        entry = self.find_entry(path)
        if entry is None:
            return
        if ok:
            entry.update(status="done", output=output, error=None)
        elif cancelled:
            entry.update(status="ready", error=None)  # nothing happened to it; the next run does it
        else:
            entry.update(status="failed", error=error)
        self.update_row(entry)

    def finish_job(self, summary=None):
        self.is_processing = False
        self.current_proc = None
        self.worker_thread = None
        for e in self.entries:
            if e["status"] == "working":
                e["status"] = "ready"
                self.update_row(e)
        if summary:
            self.last_summary = summary
        self.update_mode_from_files()
        self.set_rows_enabled(True)  # only the × buttons changed; no full redraw
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

    # ------------------------------------------------------------------ settings

    def load_settings(self):
        if not os.path.exists(self.settings_path):
            return
        try:
            # utf-8-sig: a byte-order mark (Notepad, PowerShell 5) is not damage.
            with open(self.settings_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not a settings object")
        except Exception:
            # Keep the damaged file instead of letting the next save overwrite it.
            bad = None
            for candidate in name_candidates(self.settings_path + ".bad"):  # .bad, .bad (1), ...
                try:
                    os.rename(self.settings_path, candidate)  # never replaces an existing file
                    bad = candidate
                    break
                except FileExistsError:
                    continue
                except OSError:
                    break
            if bad:
                notice = f"The settings file was damaged and was not loaded (kept as {os.path.basename(bad)})."
            else:
                self.settings_broken = True  # can't move it aside: don't save over it
                notice = ("The settings file is damaged and could not be moved aside, "
                          "so settings will not be saved until it is fixed or deleted.")
            self.settings_notice = notice  # shown by show_standing_notes, and again after Clear
            return

        def text(key):
            value = data.get(key)
            return value.strip() if isinstance(value, str) else ""

        if data.get("active_tab") in ("Passphrase", "Recipient"):
            self.tab = data["active_tab"]

        recipients_text = text("recipient_box")
        identities_text = text("identity_box")
        recipients_file = text("recipients_file")
        if recipients_text:
            self.recipient_box.insert("0.0", recipients_text + "\n")
        if identities_text:
            self.identity_box.insert("0.0", identities_text + "\n")
        if recipients_file:
            self.recipients_file.insert(0, recipients_file)
        self.armor_var.set(bool(data.get("armor", False)))
        self.pq_var.set(bool(data.get("pq", False)))

    def save_settings(self):
        if getattr(self, "settings_broken", False):
            return
        # The textboxes are saved so comment lines (# Alice) survive restarts, but never
        # secret keys, PEM blocks or base64 blobs (see without_secrets).
        data = {
            "active_tab": self.tab,
            "recipient_box": keep_lines(self.recipient_box.get("0.0", "end"), self.classify_recipient),
            "recipients_file": keep_lines(self.recipients_file.get(), looks_like_path),
            "identity_box": keep_lines(self.identity_box.get("0.0", "end"), looks_like_path),
            "armor": self.armor_var.get(),
            "pq": self.pq_var.get(),
        }
        # Write to a temp file and swap it in, so a crash mid-write can't wipe the settings.
        tmp_path = self.settings_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.settings_path)
        except Exception as exc:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            # #9: say once that settings can't be saved, instead of failing silently.
            if not getattr(self, "settings_error_shown", False):
                self.settings_error_shown = True
                self.settings_notice = f"Settings can't be saved next to page: {exc}"
                self.show_standing_notes()

    # ------------------------------------------------------------------ UI state

    def set_tab(self, name, save=True):
        self.tab = name
        for tab_name, (btn, underline) in self.tab_buttons.items():
            on = tab_name == name
            btn.configure(text_color=("gray10", "gray95") if on else MUTED,
                          font=(self.ui_font, 14, "bold") if on else (self.ui_font, 14))
            underline.configure(fg_color=BLUE if on else "transparent")
        if name == "Passphrase":
            self.recipient_tab.pack_forget()
            self.passphrase_box.pack()
        else:
            self.passphrase_box.pack_forget()
            self.recipient_tab.pack(fill="x")
        if save:
            self.save_settings()
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

    def update_mode_and_input_visibility(self):
        encrypting = self.mode != "decrypt"
        if encrypting:
            self.confirm.pack(pady=(0, 6), after=self.password.master)
            self.gen_btn.pack(after=self.confirm)
        else:
            self.confirm.pack_forget()
            self.gen_btn.pack_forget()
        self.armor_chk.configure(state="normal" if encrypting and not self.is_processing else "disabled")

    def compute_action(self):
        """(enabled, button text, hint, is_warning) for the main action button.

        The hint says exactly what is missing, instead of leaving an unexplained grey button.
        """
        if not os.path.isfile(self.age_path):
            return False, "Encrypt", "age.exe was not found next to this program.", True
        files = self.pending_files()
        if self.adding:
            return False, "Decrypt" if self.mode == "decrypt" else "Encrypt", "Reading the added files...", False
        if not files:
            if self.entries:
                return False, "Encrypt", "All done. Add more files, or Clear to start over.", False
            return False, "Encrypt", "Drop files or folders above, or click Files.", False
        n = len(files)
        verb = "Decrypt" if self.mode == "decrypt" else "Encrypt"
        text = f"{verb} {n} file" + ("" if n == 1 else "s")
        ready = "Ready. Output goes next to each file; existing files are never overwritten."

        if self.active_mode_kind() == "passphrase":
            if not os.path.isfile(self.batchpass_path):
                return False, text, "age-plugin-batchpass.exe not found. Use the Keys tab.", True
            pw = self.password.get()
            if not pw:
                return False, text, "Enter the passphrase.", False
            # The confirm field only counts when it is shown (encrypting).
            if any(ch in pw + (self.confirm.get() if self.mode == "encrypt" else "") for ch in "\r\n"):
                # A pasted line break would become part of the passphrase, so typing it
                # later would not open the file.
                return False, text, "The passphrase contains a line break (pasted?). Remove it.", True
            if self.mode == "encrypt":
                confirm = self.confirm.get()
                if not confirm:
                    return False, text, "Repeat the passphrase to confirm it.", False
                if pw != confirm:
                    return False, text, "Passphrases do not match.", True
            return True, text, ready, False

        ok, hint, warn = self.recipient_mode_hint()
        return ok, text, hint, warn

    def update_action_buttons(self):
        busy = self.is_processing
        idle_state = "disabled" if busy else "normal"
        for w in (self.browse_btn, self.add_folder_btn, self.clear_btn, self.password, self.confirm,
                  self.eye_btn, self.gen_btn, self.recipient_box, self.recipients_file, self.recipients_file_btn,
                  self.identity_box, self.identity_file_btn, self.pq_chk, self.help_btn,
                  *(btn for btn, _ in self.tab_buttons.values())):
            w.configure(state=idle_state)
        has_age_files = any(e["path"].lower().endswith(".age") for e in self.entries)
        self.inspect_btn.configure(
            state="normal" if (not busy and not self.adding and has_age_files and os.path.isfile(self.inspect_path))
            else "disabled"
        )
        self.keygen_btn.configure(state="normal" if (not busy and os.path.isfile(self.keygen_path)) else "disabled")
        self.armor_chk.configure(state="normal" if not busy and self.mode != "decrypt" else "disabled")
        self.render_summary()

        if busy:
            self.cancel_btn.grid()
            self.action_btn.configure(state="disabled", text="Working...")
            return
        self.cancel_btn.grid_remove()
        enabled, text, hint, warn = self.compute_action()
        self.action_btn.configure(state="normal" if enabled else "disabled", text=text)
        self.hint_label.configure(text=hint, text_color=WARN if warn else MUTED)

    def run_action(self):
        if self.is_processing or not self.compute_action()[0]:
            return
        if self.mode == "decrypt":
            self.decrypt()
        else:
            self.encrypt()

    def set_drop_highlight(self, on, event=None):
        self.list_frame.configure(fg_color=("gray90", "#262c33") if on else "transparent")
        return event.action if event is not None else None

    def on_passphrase_typed(self):
        self.gen_armed = False
        self.update_action_buttons()

    def generate_passphrase(self):
        """Fill both fields with a random passphrase; a typed one is only replaced on a second click."""
        if self.is_processing:
            return
        if self.password.get() and not self.gen_armed:
            self.gen_armed = True
            self.set_notes("Click again to replace the passphrase you typed with a generated one.", warn=True)
            return
        self.gen_armed = False
        p = generate_passphrase()
        for entry in (self.password, self.confirm):
            entry.delete(0, "end")
            entry.insert(0, p)
        self.toggle_show_password(True)
        self.set_notes("Generated a passphrase. Copy it and keep it somewhere safe: without it the files "
                       "can't be decrypted.", warn=True, sticky=True)
        self.update_action_buttons()

    def show_info(self, title, text):
        """Longer messages (Inspect, key generation) in a dialog like About."""
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.transient(self)
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(padx=20, pady=16, fill="both", expand=True)
        ctk.CTkLabel(body, text=title, font=(self.ui_font, 16, "bold"), anchor="w").pack(fill="x")
        box = ctk.CTkTextbox(body, width=460, height=min(300, 40 + 18 * text.count("\n")),
                             font=(self.mono_font, 13), wrap="word")
        box.pack(fill="both", expand=True, pady=(8, 12))
        box.insert("0.0", text)
        box.configure(state="disabled")
        close = ctk.CTkButton(body, text="Close", width=100, height=32, command=win.destroy,
                              font=self.small_font, fg_color=BLUE, hover_color=BLUE_HOVER)
        close.pack(anchor="e")
        win.bind("<Escape>", lambda _e: win.destroy())
        self.center_over_window(win)
        win.after(150, lambda: (win.focus_force(), close.focus_set()))  # CTkToplevel otherwise opens behind

    def show_help(self):
        win = ctk.CTkToplevel(self)
        win.title("Recipient mode")
        win.geometry("560x380")
        win.transient(self)
        box = ctk.CTkTextbox(win, wrap="word", font=self.small_font)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("0.0", RECIPIENT_HELP)
        box.configure(state="disabled")
        win.bind("<Escape>", lambda _e: win.destroy())
        scaling = self.widget_scaling()
        self.center_over_window(win, size=(round(560 * scaling), round(380 * scaling)))
        win.after(150, win.focus_force)  # CTkToplevel otherwise opens behind the main window

    def show_about(self):
        """Name, version, what it is, and where it comes from."""
        win = ctk.CTkToplevel(self)
        win.title(f"About {APP_NAME}")
        win.resizable(False, False)
        win.transient(self)
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(padx=28, pady=20)

        icon = self.about_icon_image()
        if icon is not None:
            ctk.CTkLabel(body, text="", image=icon).pack(pady=(0, 8))
        title = ctk.CTkFrame(body, fg_color="transparent")
        title.pack()
        ctk.CTkLabel(title, text=APP_NAME, font=(self.ui_font, 22, "bold")).pack(side="left")
        ctk.CTkLabel(title, text=f" v{APP_VERSION}", font=(self.ui_font, 14),
                     text_color=MUTED).pack(side="left", pady=(6, 0))
        sub = ctk.CTkFrame(body, fg_color="transparent")
        sub.pack(pady=(2, 14))
        ctk.CTkLabel(sub, text="Python GUI for ", font=(self.ui_font, 15)).pack(side="left")
        ctk.CTkLabel(sub, text="age", font=(self.ui_font, 15, "bold")).pack(side="left")

        self.link_label(body, REPO_URL).pack()
        ctk.CTkLabel(body, text="Encryption by the official age tools:", font=self.small_font,
                     text_color=MUTED).pack(pady=(14, 0))
        self.link_label(body, AGE_URL).pack()
        ctk.CTkButton(body, text="Close", width=100, height=32, command=win.destroy,
                      font=self.small_font).pack(pady=(18, 0))
        win.bind("<Escape>", lambda _e: win.destroy())
        self.center_over_window(win)
        win.after(150, win.focus_force)  # CTkToplevel otherwise opens behind the main window

    def show_admin_warning(self):
        """The running-as-administrator warning, as a dialog like About."""
        win = ctk.CTkToplevel(self)
        win.title(APP_NAME)
        win.resizable(False, False)
        win.transient(self)
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(padx=28, pady=20)
        ring = ctk.CTkFrame(body, width=56, height=56, corner_radius=28, border_width=3,
                            border_color=WARN, fg_color="transparent")
        ring.pack(pady=(0, 6))
        ring.pack_propagate(False)
        ctk.CTkLabel(ring, text="!", text_color=WARN, font=(self.ui_font, 26, "bold"),
                     fg_color="transparent").place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(body, text="Warning", font=(self.ui_font, 22, "bold")).pack()
        # A read-only text area instead of a label, so the app's name can be bold:
        # "<b>page</b> is running as administrator. ... Close <b>page</b> and ...".
        text = ctk.CTkTextbox(body, width=400, height=84, wrap="word", font=(self.ui_font, 14),
                              fg_color="transparent", border_width=0, activate_scrollbars=False)
        text.pack(pady=(8, 0))
        inner = text._textbox
        regular = tkfont.Font(font=inner.cget("font"))
        bold = regular.copy()
        bold.configure(weight="bold")
        inner.tag_configure("center", justify="center")
        inner.tag_configure("bold", font=bold)
        for part in re.split(r"\b(page)\b", ADMIN_WARNING.removeprefix("Warning: ")):
            if part:
                text.insert("end", part, ("center", "bold") if part == APP_NAME else ("center",))
        text.configure(state="disabled", cursor="arrow")
        # As tall as the wrapped text (3 lines with Segoe UI at this width; more if the
        # font or scaling differs), so no line is cut off.
        try:
            win.update_idletasks()
            n = inner.count("1.0", "end-1c", "displaylines")
            n = n[0] if isinstance(n, tuple) else n
            if n:
                text.configure(height=round((n * regular.metrics("linespace") + 12) / self.widget_scaling()))
        except Exception:
            pass
        close = ctk.CTkButton(body, text="Close", width=100, height=32, command=win.destroy,
                              font=self.small_font)
        close.pack(pady=(18, 0))
        win.bind("<Escape>", lambda _e: win.destroy())
        self.center_over_window(win)
        win.after(150, lambda: (win.focus_force(), close.focus_set()))  # CTkToplevel otherwise opens behind

    def center_over_window(self, win, size=None):
        """Place a dialog in the middle of the main window instead of the screen's corner.
        size: its (width, height) in physical pixels when it has a fixed size; otherwise
        its requested size is used (it isn't on screen yet)."""
        win.update_idletasks()
        w, h = size or (win.winfo_reqwidth(), win.winfo_reqheight())
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 3
        # Position only, in physical pixels. Not clamped at 0: a monitor left of or
        # above the main one has negative coordinates.
        win.geometry(f"+{x}+{y}")

    def link_label(self, parent, url):
        """A label that opens url in the default browser."""
        label = ctk.CTkLabel(parent, text=url.replace("https://", ""), font=self.small_font,
                             text_color=ACCENT, cursor="hand2")
        label.bind("<Button-1>", lambda _e: webbrowser.open(url))
        return label

    def about_icon_image(self):
        """The app icon (largest image in icon.ico) as a 72 px CTkImage, or None."""
        folder = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(folder, "icon.ico")
        try:
            from PIL import Image
            im = Image.open(path)
            sizes = getattr(im, "info", {}).get("sizes") or [im.size]
            im.size = max(sizes)  # pick the largest frame of the .ico
            im = im.convert("RGBA")
            return ctk.CTkImage(light_image=im, dark_image=im, size=(72, 72))
        except Exception:
            return None

    # ------------------------------------------------------------------ files

    def browse(self):
        files = filedialog.askopenfilenames()
        if files:
            self.apply_new_paths(list(files))

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.apply_new_paths([folder])

    def browse_recipients_file(self):
        path = filedialog.askopenfilename(title="Select recipients file")
        if path:
            self.recipients_file.delete(0, "end")
            self.recipients_file.insert(0, os.path.normpath(path))
            self.on_recipient_inputs_changed()

    def browse_identity_file(self):
        paths = filedialog.askopenfilenames(title="Select identity file(s)")
        for path in paths:
            self.append_line(self.identity_box, os.path.normpath(path))
        if paths:
            self.on_recipient_inputs_changed()

    def append_line(self, textbox, line):
        current = textbox.get("0.0", "end-1c")  # end-1c skips Tk's own trailing newline
        if current and not current.endswith("\n"):
            textbox.insert("end-1c", "\n")
        textbox.insert("end-1c", line + "\n")

    def deduplicate_paths(self, paths):
        seen = set()
        result = []
        for p in paths:
            norm = os.path.normcase(os.path.abspath(p))
            if norm not in seen:
                seen.add(norm)
                result.append(p)
        return result

    def apply_new_paths(self, paths):
        """Read dropped/chosen paths in the background (a network drive can be slow to
        answer), one drop after another; apply_added puts the result in the list."""
        if self.is_processing:
            self.hint_label.configure(text="Busy: wait for the current job to finish, or press Cancel.",
                                      text_color=WARN)
            return
        self.add_queue.append(list(paths))
        if not self.adding:
            self.start_next_add()

    def start_next_add(self):
        paths = self.add_queue.pop(0)
        self.adding = True
        self.update_action_buttons()
        generation = self.add_generation

        def work():
            try:
                result = expand_paths(paths)
            except Exception as exc:  # never leave the window stuck in "Reading..."
                result = ([], 0, [f"Unexpected error: {exc}"], {})
            self.ui_queue.put(("added", generation) + tuple(result))

        threading.Thread(target=work, daemon=True).start()

    def apply_added(self, new, skipped_subfolders, problems, sizes):
        if self.is_processing:
            # Encrypt, Decrypt and Inspect wait while files are read, so this shouldn't
            # happen; if it does, say so instead of dropping the files silently.
            self.set_notes("Files added while a job ran were not included. Add them again.", warn=True)
            return
        notes = []
        if not new:
            if problems:
                self.set_notes("No files could be added:\n" + problem_lines(problems), warn=True)
            else:
                self.set_notes("No files found in what was added.", warn=True)
            return

        # Added files join the list (instead of replacing it).
        before = self.pending_files()
        combined = self.deduplicate_paths(before + new)
        if len(combined) == len(before):
            notes.append("Those files are already in the list.")
        if any(f.lower().endswith(".age") for f in combined):
            non_age_count = sum(1 for f in combined if not f.lower().endswith(".age"))
            combined = [f for f in combined if f.lower().endswith(".age")]
            if non_age_count:
                notes.append(f".age files present: decrypt mode. Ignored {non_age_count} other file(s).")
        if skipped_subfolders:
            notes.append(f"{skipped_subfolders} subfolder(s) not included: "
                         "a folder adds only the files directly inside it.")
        if problems:
            notes.append("Not added:\n" + problem_lines(problems))
        warn = bool(problems)

        if combined == before:
            self.set_notes("\n".join(notes), warn=warn)  # nothing new: keep the list and last results
            return
        # New files start a new batch: finished rows from the last run go away.
        old = {e["path"]: e for e in self.entries if e["status"] != "done"}
        self.entries = [old.get(p) or {"path": p, "size": sizes.get(p), "status": "ready"} for p in combined]
        self.last_summary = None
        self.reset_progress()
        self.update_mode_from_files()
        self.render_list()
        self.set_notes("\n".join(notes), warn=warn)
        self.update_mode_and_input_visibility()
        self.update_action_buttons()
        if self.tab == "Passphrase":
            self.password.focus_set()


    def on_drop(self, event):
        try:
            files = self.tk.splitlist(event.data)
        except Exception:
            files = [event.data]
        self.set_drop_highlight(False)
        self.apply_new_paths(files)

    def update_mode_from_files(self):
        files = self.pending_files()
        if not files:
            self.mode = None
        elif all(f.lower().endswith(".age") for f in files):
            self.mode = "decrypt"
        else:
            self.mode = "encrypt"

    def reset_progress(self):
        # An empty CTkProgressBar still draws a dot; hide the fill colour until a job runs.
        self.progress.set(0)
        self.progress.configure(progress_color=self.progress.cget("fg_color"))

    def clear(self):
        """Clears the file list and passphrases. Saved recipients/identities are kept."""
        if self.is_processing:
            return
        # A drop still being read, and drops waiting behind it, are dropped too.
        self.add_generation += 1
        self.add_queue.clear()
        self.entries = []
        self.last_summary = None
        self.mode = None
        self.gen_armed = False
        self.reset_progress()
        self.password.delete(0, "end")
        self.confirm.delete(0, "end")
        self.toggle_show_password(False)
        self.show_standing_notes()
        self.render_list()
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

    # ------------------------------------------------------------------ actions

    def encrypt(self):
        if self.is_processing or not self.pending_files():
            return
        if self.active_mode_kind() == "passphrase":
            pw = self.password.get()
            if not pw:
                self.set_notes("Please enter a passphrase.", warn=True)
                return
            if pw != self.confirm.get():
                self.set_notes("Passphrases do not match.", warn=True)
                return
            self.run_process("-e", pw)
        else:
            problem = self.recipient_encrypt_problem()
            if problem:
                self.set_notes(problem, warn=True)
                return
            self.save_settings()
            self.run_process("-e", None)

    def decrypt(self):
        if self.is_processing or not self.pending_files():
            return
        if self.active_mode_kind() == "passphrase":
            pw = self.password.get()
            if not pw:
                self.set_notes("Please enter a passphrase.", warn=True)
                return
            self.run_process("-d", pw)
        else:
            if not self.get_recipient_inputs()[5]:
                self.set_notes("Please provide at least one existing identity file.", warn=True)
                return
            self.save_settings()
            self.run_process("-d", None)

    def cancel(self):
        if not self.is_processing:
            return
        self.cancel_event.set()
        proc = self.current_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self.hint_label.configure(text="Cancelling...", text_color=MUTED)

    def run_tool(self, cmd, env=None, cancellable=False, out_handle=None):
        """Run a CLI tool, keep a handle so Cancel can kill it. Returns (returncode, output).

        With out_handle, the tool's standard output is written into that open file
        (closed afterwards) instead of being captured.
        """
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=out_handle or subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, creationflags=NO_WINDOW,
            )
            self.current_proc = proc
            # Cancel may have run between Popen and the line above, when there was no
            # process to kill yet; catch that here instead of letting age run to the end.
            if cancellable and self.cancel_event.is_set():
                proc.terminate()
            out, err = proc.communicate()
        finally:
            if out_handle:
                out_handle.close()
        self.current_proc = None
        text = ((out or b"").decode("utf-8", "replace") + "\n" + err.decode("utf-8", "replace")).strip()
        # Drop age's generic "report unexpected errors" footer.
        text = "\n".join(l for l in text.splitlines() if "report unexpected or unhelpful errors" not in l)
        return proc.returncode, text

    def build_command(self, is_encrypt, active_kind, in_file, recipients, recipients_file, identities, armor):
        # No -o: age writes to stdout, which run_tool points at the open .part file.
        # Passing -o makes age compare file IDs of input and output, and virtual drives
        # such as Cryptomator vaults report ID 0 for every file, so age would wrongly
        # refuse with "input and output file are the same".
        cmd = [self.age_path, "-e" if is_encrypt else "-d"]
        if is_encrypt and armor:
            cmd.append("-a")
        if active_kind == "passphrase":
            cmd.extend(["-j", "batchpass"])
        elif is_encrypt:
            for r in recipients:
                cmd.extend(["-r", r])
            if recipients_file:
                cmd.extend(["-R", recipients_file])
        else:
            for ident in identities:
                cmd.extend(["-i", ident])
        cmd.append(in_file)
        return cmd

    def run_process(self, flag, password):
        if not os.path.isfile(self.age_path):
            self.set_notes("age.exe not found.", warn=True)
            return

        active_kind = self.active_mode_kind()
        if active_kind == "passphrase" and not os.path.isfile(self.batchpass_path):
            self.set_notes("age-plugin-batchpass.exe not found.", warn=True)
            return

        # Snapshot everything the worker needs; the worker never reads Tk widgets.
        _, valid_r, rfile, rfile_ok, _, valid_i = self.get_recipient_inputs()
        files = self.pending_files()
        armor = self.armor_var.get()
        is_encrypt = flag == "-e"

        env = os.environ.copy()
        if active_kind == "passphrase":
            env["AGE_PASSPHRASE"] = password
            env["PATH"] = self.base_dir + os.pathsep + env.get("PATH", "")

        self.is_processing = True
        self.cancel_event.clear()
        self.last_summary = None
        if not self.notes_sticky:
            self.set_notes("")
        for e in self.entries:
            if e["status"] == "failed":  # retried now
                e.update(status="ready", error=None)
                self.update_row(e)
        self.progress.set(0)
        self.progress.configure(progress_color=ACCENT)
        self.set_rows_enabled(False)
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

        def worker():
            ok = failed = 0
            warnings, failures, leftovers = [], [], []
            summary = "Stopped."
            try:
                for idx, f in enumerate(files):
                    if self.cancel_event.is_set():
                        break
                    self.ui_queue.put(("current", f))
                    self.ui_queue.put(("progress", idx, len(files), os.path.basename(f)))
                    part_file = out_file = None
                    try:
                        final_target = f + ".age" if is_encrypt else decrypted_name(f)
                        part_file, handle = create_part_file(final_target)
                        self.inflight_output = part_file
                        cmd = self.build_command(is_encrypt, active_kind, f, valid_r,
                                                 rfile if rfile_ok else "", valid_i, armor)
                        rc, output = self.run_tool(cmd, env, cancellable=True, out_handle=handle)
                        if rc == 0:
                            out_file = publish_part_file(part_file, final_target)
                    except Exception as exc:
                        rc, output = -1, f"Unexpected error: {exc}"

                    if rc == 0:
                        # A file that finished is kept even if Cancel was pressed meanwhile.
                        ok += 1
                        self.ui_queue.put(("result", f, True, out_file, None, False))
                        if output:
                            warnings.append(f"{os.path.basename(f)}: {output}")
                    else:
                        cancelled = self.cancel_event.is_set()
                        error = output or "unknown error"
                        if not cancelled:
                            failed += 1
                            failures.append(f"  {os.path.basename(f)}: {' '.join(error.split())}")
                        if not self.remove_output(part_file):  # this job's own .part file
                            leftovers.append(f"  {part_file}")
                        self.ui_queue.put(("result", f, False, None, None if cancelled else error, cancelled))
                    self.inflight_output = None
                    self.ui_queue.put(("progress", idx + 1, len(files), None))

                skipped = len(files) - ok - failed
                summary = f"Finished: {ok} OK, {failed} failed"
                if skipped:
                    summary += f", {skipped} not processed (cancelled)"
                # The full reasons, under the list (the rows only have room for their start).
                notes = []
                if failures:
                    notes.append("Failed:\n" + "\n".join(failures))
                if leftovers:
                    notes.append("Partial output that couldn't be deleted (in use?); delete it yourself:\n"
                                 + "\n".join(leftovers))
                if warnings:
                    notes.append("age said:\n" + "\n".join(warnings))
                if notes:
                    self.ui_queue.put(("notes", "\n".join(notes), True))
            finally:
                self.ui_queue.put(("done", summary + "."))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    @staticmethod
    def remove_output(path):
        """Delete a partial output file; True when it is gone. Retries for a moment
        while antivirus or the indexer still has the new file open (as sage does)."""
        if not path:
            return True
        try:
            if os.path.isfile(long_path(path)):
                _retry_while_locked(lambda: os.remove(long_path(path)), tries=30)
        except OSError:
            pass
        return not os.path.exists(long_path(path))

    def inspect(self):
        if self.is_processing or self.adding:
            return
        targets = [e["path"] for e in self.entries if e["path"].lower().endswith(".age")]
        if not targets or not os.path.isfile(self.inspect_path):
            return
        self.is_processing = True
        self.cancel_event.clear()
        self.set_rows_enabled(False)
        self.update_action_buttons()

        def worker():
            parts = []
            try:
                for f in targets:
                    if self.cancel_event.is_set():
                        break
                    try:
                        _, output = self.run_tool([self.inspect_path, f], cancellable=True)
                    except Exception as exc:
                        output = f"Unexpected error: {exc}"
                    parts.append(output)
                if parts:  # cancelled before the first file: no empty dialog
                    self.ui_queue.put(("info", "Inspect", "\n\n".join(parts)))
            finally:
                self.ui_queue.put(("done", None))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def generate_key(self):
        if self.is_processing or not os.path.isfile(self.keygen_path):
            return
        path = filedialog.asksaveasfilename(
            title="Save new identity (secret key) file",
            initialfile="key.txt",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            confirmoverwrite=False,  # we never overwrite, so don't offer to
        )
        if not path:
            return
        path = os.path.normpath(path)
        if os.path.exists(path):
            # age-keygen refuses to overwrite, and destroying an existing secret key would be worse anyway.
            self.set_notes(f"{path} already exists. A key file is never overwritten; pick a new name.", warn=True)
            return

        cmd = [self.keygen_path]
        if self.pq_var.get():
            cmd.append("-pq")
        cmd.extend(["-o", path])
        try:
            rc, output = self.run_tool(cmd)
        except Exception as exc:
            rc, output = -1, str(exc)
        if rc != 0:
            self.show_info("Key generation failed", f"age-keygen failed: {output}")
            return

        public_key = None
        for line in output.splitlines():
            if line.startswith("Public key:"):
                public_key = line.split(":", 1)[1].strip()
        # age refuses to mix post-quantum and classic keys in one file, so only add the
        # new public key to the recipients if it matches the keys already there.
        existing = [r for r in self.parse_multiline_lines(self.recipient_box) if self.classify_recipient(r)]
        mixes = bool(public_key) and any(is_pq_recipient(r) != is_pq_recipient(public_key) for r in existing)
        if public_key and not mixes:
            self.append_line(self.recipient_box, public_key)
        self.append_line(self.identity_box, path)
        self.on_recipient_inputs_changed()
        if mixes:
            about_public = ("Its public key was NOT added to 'Encrypt to': post-quantum and classic keys can't be "
                            "mixed in one file. It is in the key file, on the '# public key:' line.")
        elif public_key:
            about_public = "Its public key was added to 'Encrypt to'."
        else:
            about_public = "Could not read its public key; get it with: age-keygen -y " + path
        self.show_info("New key pair", f"Saved to {path} and added to 'Decrypt with'.\n\n{about_public}\n\n"
                       "Keep the identity file secret and backed up: without it, files encrypted to its "
                       "public key can't be decrypted.")

    def on_close(self):
        self.cancel()
        # Wait for the worker to kill age and delete the partial output; as a daemon
        # thread it would otherwise die with the window and leave a truncated file.
        thread = self.worker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
            if thread.is_alive():
                self.remove_output(self.inflight_output)
        self.save_settings()
        self.destroy()


if __name__ == "__main__":
    app = AgeGui()
    # "Send to" / command-line paths: same rules as a drop.
    launched = [arg for arg in sys.argv[1:] if arg]
    if launched:
        app.apply_new_paths(launched)
    app.mainloop()
