# -*- coding: utf-8 -*-
import datetime
import json
import os
import queue
import stat
import subprocess
import sys
import threading
import time
import tkinter.font as tkfont
import webbrowser
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

# Colours as (light mode, dark mode).
MUTED = ("gray40", "gray65")
# Fits the Recipient tab with a normal bottom margin; the Passphrase tab shares it so
# switching tabs never moves anything.
TAB_HEIGHT = 314
WARN = ("#b35c00", "#f0a040")
ACCENT = ("#3a7ebf", "#4a9eff")
DANGER, DANGER_HOVER = "#8b2c2c", "#a33a3a"
# Mode badge colours: outline + text. Encrypt in the app's blue, decrypt amber.
ENCRYPT_BADGE = ("#1f6aa5", "#7ab2e6")
DECRYPT_BADGE = ("#b36b00", "#f0b050")
APP_NAME = "page"
APP_VERSION = "0.1.0"
REPO_URL = "https://github.com/t0ma5/page"
AGE_URL = "https://github.com/FiloSottile/age"

SECONDARY = {"fg_color": ("gray78", "gray28"), "hover_color": ("gray70", "gray35"),
             "text_color": ("gray10", "gray92"), "text_color_disabled": ("gray55", "gray50")}

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


def describe_add_error(path, exc):
    """Why a dropped or chosen path can't be added, as (text, drive_missing)."""
    drive = os.path.splitdrive(path)[0]
    if drive and not os.path.exists(drive + os.sep):
        return f"{path}: drive {drive} isn't available to page.", True
    if isinstance(exc, FileNotFoundError):
        return f"{path}: not found (moved or deleted?).", False
    if isinstance(exc, PermissionError):
        return f"{path}: access denied.", False
    return f"{path}: can't be read ({getattr(exc, 'strerror', None) or exc}).", False


def problem_lines(problems, elevated):
    """List unreadable paths (at most 5); if a drive is missing because page runs as
    administrator, say so once."""
    lines = [f"  {text}" for text, _ in problems[:5]]
    if len(problems) > 5:
        lines.append(f"  ...and {len(problems) - 5} more.")
    if elevated and any(missing for _, missing in problems):
        lines.append(ADMIN_WARNING)
    return "\n".join(lines)


def running_elevated():
    """True when page runs as administrator. Windows then blocks drag and drop from
    Explorer (a normal-level program) and hides drives mapped by the user's normal
    session, such as network drives and Cryptomator vaults."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
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
    """A Windows or relative path: has a separator or a drive colon."""
    return ("\\" in line or "/" in line or ":" in line) and not looks_like_base64_blob(line)


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

        self.files = []
        self.mode = None
        self.is_processing = False
        self.cancel_event = threading.Event()
        self.current_proc = None
        self.worker_thread = None
        self.inflight_output = None
        self.last_summary = None
        self.ui_queue = queue.Queue()
        self.write_log = False
        self.log_error_shown = False

        self.base_dir = self.get_base_dir()
        self.age_path = os.path.join(self.base_dir, "age.exe")
        self.batchpass_path = os.path.join(self.base_dir, "age-plugin-batchpass.exe")
        self.keygen_path = os.path.join(self.base_dir, "age-keygen.exe")
        self.inspect_path = os.path.join(self.base_dir, "age-inspect.exe")
        self.log_path = os.path.join(self.base_dir, "log.txt")
        self.settings_path = os.path.join(self.base_dir, self.SETTINGS_FILE)

        # Proportional font for the interface, monospace only where paths and keys are shown.
        available_fonts = set(tkfont.families())
        self.ui_font = "Segoe UI" if "Segoe UI" in available_fonts else "TkDefaultFont"
        self.mono_font = "Consolas" if "Consolas" in available_fonts else "Courier New"
        self.ui_font_size = 14
        font = (self.ui_font, 14)
        small_font = self.small_font = (self.ui_font, 13)
        bold_font = (self.ui_font, 14, "bold")
        mono = (self.mono_font, 13)

        # ---------------- Bottom bar ----------------
        # Packed first, against the bottom edge: Tk hands out space in packing order, so
        # the action button keeps its place however small the window gets.
        bottom = ctk.CTkFrame(self, corner_radius=0)
        bottom.pack(side="bottom", fill="x")
        self.progress = ctk.CTkProgressBar(bottom, height=6)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20, pady=(12, 0))
        self.reset_progress()
        bar = ctk.CTkFrame(bottom, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(8, 12))
        bar.grid_columnconfigure(0, weight=1)
        hint_frame = ctk.CTkFrame(bar, fg_color="transparent")
        hint_frame.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.hint_label = ctk.CTkLabel(hint_frame, text="", anchor="w", justify="left", font=small_font)
        self.hint_label.pack(fill="x")
        self.wrap_to_width(self.hint_label, side_padding=0)
        self.cancel_btn = ctk.CTkButton(
            bar, text="Cancel", width=90, height=40, command=self.cancel, font=font,
            fg_color=DANGER, hover_color=DANGER_HOVER,
        )
        self.cancel_btn.grid(row=0, column=1, padx=(0, 10))
        self.action_btn = ctk.CTkButton(bar, text="Encrypt", width=200, height=40,
                                        command=self.run_action, font=(self.ui_font, 15, "bold"))
        self.action_btn.grid(row=0, column=2)

        options = ctk.CTkFrame(self, fg_color="transparent")
        options.pack(side="bottom", pady=(8, 12))  # no fill: pack centres it
        self.armor_var = ctk.BooleanVar(value=False)
        self.armor_chk = ctk.CTkCheckBox(
            options, text="ASCII armor output (-a)", variable=self.armor_var,
            command=self.save_settings, font=small_font, checkbox_width=20, checkbox_height=20,
        )
        self.armor_chk.pack(side="left", padx=(0, 20))
        self.logfile_var = ctk.BooleanVar(value=False)
        self.logfile_chk = ctk.CTkCheckBox(
            options, text="Write log.txt", variable=self.logfile_var,
            command=self.save_settings, font=small_font, checkbox_width=20, checkbox_height=20,
        )
        self.logfile_chk.pack(side="left")

        # ---------------- Files ----------------
        files_frame = ctk.CTkFrame(self, fg_color="transparent")
        files_frame.pack(fill="both", expand=True, padx=20, pady=(14, 0))
        header = ctk.CTkFrame(files_frame, fg_color="transparent")
        header.pack(fill="x")
        self.files_title = ctk.CTkLabel(header, text="Files", font=(self.ui_font, 16, "bold"))
        self.files_title.pack(side="left")
        # Mode badge: an outline pill (a bordered frame around a label), so it can't be
        # mistaken for one of the filled buttons. Hidden while the list is empty.
        self.mode_badge = ctk.CTkFrame(header, fg_color="transparent", border_width=2,
                                       corner_radius=12, height=24)
        self.mode_badge_label = ctk.CTkLabel(self.mode_badge, text="", font=(self.ui_font, 12, "bold"),
                                             fg_color="transparent", height=18)
        self.mode_badge_label.pack(padx=10, pady=2)
        self.about_btn = ctk.CTkButton(header, text="i", width=30, height=30, command=self.show_about,
                                       font=(self.ui_font, 14, "bold"), **SECONDARY)
        self.about_btn.pack(side="right")
        self.inspect_btn = ctk.CTkButton(header, text="Inspect", width=80, height=30, command=self.inspect,
                                         font=small_font, **SECONDARY)
        self.inspect_btn.pack(side="right", padx=(0, 6))
        self.clear_btn = ctk.CTkButton(header, text="Clear", width=70, height=30, command=self.clear,
                                       font=small_font, **SECONDARY)
        self.clear_btn.pack(side="right", padx=(0, 6))
        self.add_folder_btn = ctk.CTkButton(header, text="Add folder", width=96, height=30,
                                            command=self.browse_folder, font=small_font, **SECONDARY)
        self.add_folder_btn.pack(side="right", padx=(0, 6))
        self.browse_btn = ctk.CTkButton(header, text="Add files", width=90, height=30, command=self.browse,
                                        font=small_font)
        self.browse_btn.pack(side="right", padx=(0, 6))

        self.drop_border = ctk.ThemeManager.theme["CTkTextbox"]["border_color"]
        # A small requested height: the box still expands into all free space, but Tk
        # shrinks the last-packed widget (the tab card) first when space runs short, so a
        # large request here would squeeze the card instead.
        self.file_box = ctk.CTkTextbox(files_frame, height=80, font=mono, border_width=2,
                                       border_color=self.drop_border, wrap="word")
        self.file_box.pack(fill="both", expand=True, pady=(8, 0))
        self.file_box.drop_target_register(DND_FILES)
        self.file_box.dnd_bind("<<Drop>>", self.on_drop)
        self.file_box.dnd_bind("<<DropEnter>>", lambda e: self.set_drop_highlight(True, e))
        self.file_box.dnd_bind("<<DropLeave>>", lambda e: self.set_drop_highlight(False, e))

        # ---------------- Mode tabs ----------------
        # Tall enough for the Recipient tab, so switching tabs doesn't resize the file list.
        self.tabs = ctk.CTkTabview(self, height=TAB_HEIGHT, command=self.on_tab_change)
        self.tabs.pack(fill="x", padx=20, pady=(8, 0))
        self.after(50, self.style_tab_buttons)
        self.tabs.add("Passphrase")
        self.tabs.add("Recipient")
        self.tabs.set("Passphrase")

        # Passphrase tab
        passphrase_tab = self.tabs.tab("Passphrase")
        self.passphrase_box = ctk.CTkFrame(passphrase_tab, fg_color="transparent")
        self.passphrase_box.pack(expand=True)
        ctk.CTkLabel(
            self.passphrase_box, text="For files encrypted with a passphrase (age -p).",
            font=small_font, text_color=MUTED,
        ).pack(pady=(0, 12))
        self.password = ctk.CTkEntry(self.passphrase_box, placeholder_text="Passphrase", show="*",
                                     width=360, height=38, font=font)
        self.password.pack(pady=(0, 8))
        self.confirm = ctk.CTkEntry(self.passphrase_box, placeholder_text="Repeat passphrase", show="*",
                                    width=360, height=38, font=font)
        self.confirm.pack(pady=(0, 8))
        self.show_pw_var = ctk.BooleanVar(value=False)
        self.show_pw_chk = ctk.CTkCheckBox(
            self.passphrase_box, text="Show passphrase", variable=self.show_pw_var,
            command=self.toggle_show_password, font=small_font, checkbox_width=20, checkbox_height=20,
        )
        self.show_pw_chk.pack(anchor="w")
        for entry in (self.password, self.confirm):
            self.bind_input_change(entry, self.update_action_buttons)
            entry.bind("<Return>", lambda _e: self.run_action())

        # Recipient tab
        recipient_tab = self.tabs.tab("Recipient")
        row = ctk.CTkFrame(recipient_tab, fg_color="transparent")
        row.pack(fill="x", padx=6)
        ctk.CTkLabel(row, text="Encrypt to", font=bold_font).pack(side="left")
        ctk.CTkLabel(row, text="public keys, one per line \u00b7 # comment",
                     font=small_font, text_color=MUTED).pack(side="left", padx=8)
        ctk.CTkButton(row, text="?", width=28, height=26, command=self.show_help, font=bold_font,
                      **SECONDARY).pack(side="right")
        self.recipient_box = ctk.CTkTextbox(recipient_tab, height=52, font=mono, wrap="none")
        self.recipient_box.pack(fill="x", padx=6, pady=(4, 6))
        self.bind_input_change(self.recipient_box, self.on_recipient_inputs_changed)

        rf_frame = ctk.CTkFrame(recipient_tab, fg_color="transparent")
        rf_frame.pack(fill="x", padx=6)
        rf_frame.grid_columnconfigure(0, weight=1)
        self.recipients_file = ctk.CTkEntry(rf_frame, placeholder_text="...and/or a recipients file (-R)",
                                            height=32, font=mono)
        self.recipients_file.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.bind_input_change(self.recipients_file, self.on_recipient_inputs_changed)
        self.recipients_file_btn = ctk.CTkButton(rf_frame, text="Browse...", width=96, height=32,
                                                 command=self.browse_recipients_file, font=small_font, **SECONDARY)
        self.recipients_file_btn.grid(row=0, column=1)

        row = ctk.CTkFrame(recipient_tab, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(12, 0))
        ctk.CTkLabel(row, text="Decrypt with", font=bold_font).pack(side="left")
        ctk.CTkLabel(row, text="identity (secret key) files, one path per line",
                     font=small_font, text_color=MUTED).pack(side="left", padx=8)
        self.identity_file_btn = ctk.CTkButton(row, text="Add file...", width=96, height=28,
                                               command=self.browse_identity_file, font=small_font, **SECONDARY)
        self.identity_file_btn.pack(side="right")
        self.identity_box = ctk.CTkTextbox(recipient_tab, height=52, font=mono, wrap="none")
        self.identity_box.pack(fill="x", padx=6, pady=(4, 8))
        self.bind_input_change(self.identity_box, self.on_recipient_inputs_changed)

        keygen_frame = ctk.CTkFrame(recipient_tab, fg_color="transparent")
        keygen_frame.pack(fill="x", padx=6)
        self.keygen_btn = ctk.CTkButton(keygen_frame, text="Generate new key pair", height=30,
                                        command=self.generate_key, font=small_font, **SECONDARY)
        self.keygen_btn.pack(side="left")
        self.pq_var = ctk.BooleanVar(value=False)
        self.pq_chk = ctk.CTkCheckBox(keygen_frame, text="Post-quantum (-pq)", variable=self.pq_var,
                                      command=self.save_settings, font=small_font,
                                      checkbox_width=20, checkbox_height=20)
        self.pq_chk.pack(side="left", padx=12)


        self.load_settings()
        self.show_welcome()
        self.set_initial_geometry()
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self.process_ui_queue)

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

    def style_tab_buttons(self):
        try:
            segmented = getattr(self.tabs, "_segmented_button", None)
            if segmented is not None:
                segmented.configure(font=(self.ui_font, self.ui_font_size))
                for btn in getattr(segmented, "_buttons_dict", {}).values():
                    try:
                        btn.configure(width=140, font=(self.ui_font, self.ui_font_size))
                    except Exception:
                        pass
        except Exception:
            pass

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
        # Same size as sage (590 x 660), so the two apps look alike.
        width = min(590, int(sw / scaling) - 20)
        height = min(660, int((sh - title_bar) / scaling) - 10)
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
        return "recipient" if self.tabs.get() == "Recipient" else "passphrase"

    def toggle_show_password(self):
        show = "" if self.show_pw_var.get() else "*"
        self.password.configure(show=show)
        self.confirm.configure(show=show)

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
        recipients_file_ok = bool(recipients_file) and os.path.isfile(long_path(recipients_file))
        valid_identities = [i for i in identities if os.path.isfile(long_path(i))]
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
        cache = getattr(self, "_dry_run_cache", None)
        if cache and cache[0] == key:
            return cache[1]
        cmd = [self.age_path, "-e"]
        for r in recipients:
            cmd += ["-r", r]
        if recipients_file:
            cmd += ["-R", recipients_file]
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, timeout=10, creationflags=NO_WINDOW)
            err = proc.stderr.decode("utf-8", "replace")
            lines = [l.strip() for l in err.splitlines()
                     if l.strip() and "report unexpected or unhelpful errors" not in l]
            if proc.returncode != 0:
                msg = lines[0] if lines else "age.exe refused these recipients."
                problem = "age: " + msg.split("error: ", 1)[-1]
            elif any("ignoring unsupported SSH key" in l for l in lines):
                problem = ("The recipients file has an SSH key age can't use (too short or of an "
                           "unsupported type); age would skip it. Remove it or comment it out with #.")
            else:
                problem = None
        except (OSError, subprocess.TimeoutExpired):
            problem = None  # can't check now; the real run reports any error per file
        self._dry_run_cache = (key, problem)
        return problem

    def recipient_mode_hint(self):
        """(ready, hint, is_warning) for recipient mode; shown in the bottom bar next to the button."""
        recipients, valid_r, rfile, rfile_ok, identities, valid_i = self.get_recipient_inputs()
        if self.mode == "encrypt":
            problem = self.recipient_encrypt_problem()
            if problem:
                return False, problem, bool(recipients or rfile)
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

    # ------------------------------------------------------------------ logging

    def startup_status_lines(self):
        lines = [
            "Drop files or folders here.",
            "",
            "  .age files are decrypted, everything else is encrypted.",
            "  Output goes next to each file; existing files are never overwritten.",
        ]
        missing = [
            f"  MISSING {os.path.basename(path)} ({need})"
            for path, need in (
                (self.age_path, "required"),
                (self.batchpass_path, "needed for the Passphrase tab"),
                (self.keygen_path, "needed for key generation"),
                (self.inspect_path, "needed for Inspect"),
            )
            if not os.path.isfile(path)
        ]
        lines += missing or ["  age tools: all found."]
        notice = getattr(self, "settings_notice", None)
        if notice:
            lines += ["", "  " + notice]
        if running_elevated():
            lines += ["", ADMIN_WARNING]
        return lines

    def show_welcome(self):
        self._write_box("\n".join(self.startup_status_lines()), clear_first=True)

    def append_to_log_file(self, text):
        if not self.write_log:  # plain bool: Tk variables must not be read from worker threads
            return
        try:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"[{stamp}] {text}\n")
        except Exception as exc:
            # Say once that log.txt can't be written, instead of failing silently.
            # Queued, because this can run on the worker thread.
            if not self.log_error_shown:
                self.log_error_shown = True
                self.ui_queue.put(("log", f"(log.txt can't be written next to page: {exc})", False))

    def _write_box(self, text, clear_first=False):
        # The box is read-only for the user; unlock it only while writing.
        self.file_box.configure(state="normal")
        if clear_first:
            self.file_box.delete("0.0", "end")
        self.file_box.insert("end", text + "\n")
        self.file_box.see("end")
        self.file_box.configure(state="disabled")

    def log_to_box(self, text, clear_first=False):
        """Thread-safe: Tk widgets are only touched from the main thread."""
        if threading.current_thread() is threading.main_thread():
            self._write_box(text, clear_first)
        else:
            self.ui_queue.put(("log", text, clear_first))
        self.append_to_log_file(text)

    def process_ui_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                try:
                    if item[0] == "log":
                        self._write_box(item[1], item[2])
                    elif item[0] == "progress":
                        done, total = item[1], item[2]
                        self.progress.set(done / total if total else 0)
                        self.hint_label.configure(text=f"Working on file {min(done + 1, total)} of {total}...",
                                                  text_color=MUTED)
                    elif item[0] == "done":
                        self.finish_job(clear_files=item[1], summary=item[2])
                except Exception as exc:
                    print(f"UI update failed: {exc!r}", file=sys.stderr)
        except queue.Empty:
            pass
        finally:
            # Always reschedule: if this loop died, the UI would stay locked forever.
            self.after(50, self.process_ui_queue)

    def finish_job(self, clear_files, summary=None):
        self.is_processing = False
        self.current_proc = None
        self.worker_thread = None
        if clear_files:
            # Prevents a second ENCRYPT/Enter from quietly re-processing everything
            # into "name (1).ext.age" copies.
            self.files = []
            self.mode = None
            self.update_mode_and_input_visibility()
        if summary:
            self.last_summary = summary
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
                self.settings_notice = (f"The settings file was damaged and was not loaded "
                                        f"(kept as {os.path.basename(bad)}).")
            else:
                self.settings_broken = True  # can't move it aside: don't save over it
                self.settings_notice = ("The settings file is damaged and could not be moved aside, "
                                        "so settings will not be saved until it is fixed or deleted.")
            return

        def text(key):
            value = data.get(key)
            return value.strip() if isinstance(value, str) else ""

        if data.get("active_tab") in ("Passphrase", "Recipient"):
            target = data["active_tab"]
            self.tabs.set(target)
            # CTkTabview.set() hides all other tabs 100 ms later. The set("Passphrase")
            # made while building the window would then hide this tab as well, leaving
            # the card empty. Select it again after those delayed hides have run.
            self.after(150, lambda: self.tabs.set(target))

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
        self.logfile_var.set(bool(data.get("write_log", False)))
        self.write_log = self.logfile_var.get()
        self.pq_var.set(bool(data.get("pq", False)))

    def save_settings(self):
        self.write_log = self.logfile_var.get()
        if getattr(self, "settings_broken", False):
            return
        # The textboxes are saved so comment lines (# Alice) survive restarts, but never
        # secret keys, PEM blocks or base64 blobs (see without_secrets).
        data = {
            "active_tab": self.tabs.get(),
            "recipient_box": keep_lines(self.recipient_box.get("0.0", "end"), self.classify_recipient),
            "recipients_file": keep_lines(self.recipients_file.get(), looks_like_path),
            "identity_box": keep_lines(self.identity_box.get("0.0", "end"), looks_like_path),
            "armor": self.armor_var.get(),
            "write_log": self.logfile_var.get(),
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
                self._write_box(f"Settings can't be saved next to page: {exc}")

    # ------------------------------------------------------------------ UI state

    def on_tab_change(self):
        self.save_settings()
        self.style_tab_buttons()
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

    def update_mode_and_input_visibility(self):
        if self.active_mode_kind() == "passphrase" and self.mode != "decrypt":
            self.confirm.pack(pady=(0, 8), after=self.password)
        else:
            self.confirm.pack_forget()
        self.armor_chk.configure(state="disabled" if self.mode == "decrypt" else "normal")

    def update_mode_badge(self):
        if not self.files:
            self.mode_badge.pack_forget()
            return
        n = len(self.files)
        colour = DECRYPT_BADGE if self.mode == "decrypt" else ENCRYPT_BADGE
        word = "DECRYPT" if self.mode == "decrypt" else "ENCRYPT"
        self.mode_badge.configure(border_color=colour)
        self.mode_badge_label.configure(text=f"{word} · {n}", text_color=colour)
        if not self.mode_badge.winfo_ismapped():
            self.mode_badge.pack(side="left", padx=10, after=self.files_title)

    def compute_action(self):
        """(enabled, button text, hint, is_warning) for the main action button.

        The hint says exactly what is missing, instead of leaving an unexplained grey button.
        """
        if not os.path.isfile(self.age_path):
            return False, "Encrypt", "age.exe was not found next to this program.", True
        if not self.files:
            return False, "Encrypt", "Drop files or folders above, or click Add files.", False
        n = len(self.files)
        verb = "Decrypt" if self.mode == "decrypt" else "Encrypt"
        text = f"{verb} {n} file" + ("" if n == 1 else "s")
        ready = "Ready. Output goes next to each file; existing files are never overwritten."

        if self.active_mode_kind() == "passphrase":
            if not os.path.isfile(self.batchpass_path):
                return False, text, "age-plugin-batchpass.exe not found. Use the Recipient tab.", True
            pw = self.password.get()
            if not pw:
                return False, text, "Enter the passphrase.", False
            if any(ch in pw + self.confirm.get() for ch in "\r\n"):
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
                  self.show_pw_chk, self.recipient_box, self.recipients_file, self.recipients_file_btn,
                  self.identity_box, self.identity_file_btn, self.armor_chk, self.pq_chk, self.logfile_chk):
            w.configure(state=idle_state)
        has_age_files = any(f.lower().endswith(".age") for f in self.files)
        self.inspect_btn.configure(
            state="normal" if (not busy and has_age_files and os.path.isfile(self.inspect_path)) else "disabled"
        )
        self.keygen_btn.configure(state="normal" if (not busy and os.path.isfile(self.keygen_path)) else "disabled")
        if self.mode == "decrypt":
            self.armor_chk.configure(state="disabled")
        self.update_mode_badge()

        if busy:
            self.cancel_btn.grid()
            self.action_btn.configure(state="disabled", text="Working...")
            return
        self.cancel_btn.grid_remove()
        enabled, text, hint, warn = self.compute_action()
        if self.last_summary:
            hint = f"{self.last_summary} {hint}"
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
        self.file_box.configure(border_color=ACCENT if on else self.drop_border)
        return event.action if event is not None else None

    def show_help(self):
        win = ctk.CTkToplevel(self)
        win.title("Recipient mode")
        win.geometry("560x380")
        win.transient(self)
        box = ctk.CTkTextbox(win, wrap="word", font=self.small_font)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("0.0", RECIPIENT_HELP)
        box.configure(state="disabled")
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
        win.after(150, win.focus_force)  # CTkToplevel otherwise opens behind the main window

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

    def expand_paths(self, paths):
        # tk.splitlist already removed Tcl's {braces}; stripping again would break
        # real names such as "backup{1}" or a "{GUID}" folder.
        # long_path: paths over 260 characters must not be silently skipped.
        # Paths that can't be read are kept in self.add_problems with the reason.
        expanded = []
        self.skipped_subfolders = 0
        self.add_problems = []
        for p in paths:
            # Absolute, so Send-to/command-line paths don't depend on the working folder.
            try:
                p = os.path.normpath(os.path.abspath(p))
                info = os.stat(long_path(p))
            except ValueError:
                self.add_problems.append((f"{p}: not a valid path.", False))
                continue
            except OSError as exc:
                self.add_problems.append(describe_add_error(p, exc))
                continue
            if stat.S_ISREG(info.st_mode):
                expanded.append(p)
            elif stat.S_ISDIR(info.st_mode):
                try:
                    names = sorted(os.listdir(long_path(p)))
                except OSError as exc:
                    self.add_problems.append(describe_add_error(p, exc))
                    continue
                for name in names:
                    full_path = os.path.join(p, name)
                    if os.path.isfile(long_path(full_path)):
                        expanded.append(full_path)
                    elif os.path.isdir(long_path(full_path)):
                        self.skipped_subfolders += 1
        return expanded

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
        if self.is_processing:
            self.hint_label.configure(text="Busy: wait for the current job to finish, or press Cancel.",
                                      text_color=WARN)
            return
        new = self.expand_paths(paths)
        if not new:
            # Append, so the welcome text (including any missing-tool line) stays visible.
            if self.add_problems:
                self._write_box("No files could be added:\n" + problem_lines(self.add_problems, running_elevated()))
            else:
                self._write_box("No files found in what was added.")
            return

        # Added files join the list instead of replacing it.
        combined = self.deduplicate_paths(self.files + new)
        note = None
        if len(combined) == len(self.files):
            note = "Those files are already in the list."
        if any(f.lower().endswith(".age") for f in combined):
            non_age_count = sum(1 for f in combined if not f.lower().endswith(".age"))
            combined = [f for f in combined if f.lower().endswith(".age")]
            if non_age_count:
                note = f"Detected .age files - decrypt mode. Ignored {non_age_count} non-.age file(s)."

        self.files = combined
        self.last_summary = None
        self.update_file_box()
        if note:
            self._write_box(note)
        if self.skipped_subfolders:
            self._write_box(f"{self.skipped_subfolders} subfolder(s) not included: "
                            "a folder adds only the files directly inside it.")
        if self.add_problems:
            self._write_box("Not added:\n" + problem_lines(self.add_problems, running_elevated()))
        self.update_mode_from_files()
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

    def on_drop(self, event):
        try:
            files = self.tk.splitlist(event.data)
        except Exception:
            files = [event.data]
        self.apply_new_paths(files)

    def update_mode_from_files(self):
        if not self.files:
            self.mode = None
        elif all(f.lower().endswith(".age") for f in self.files):
            self.mode = "decrypt"
        else:
            self.mode = "encrypt"

    def update_file_box(self):
        # File name first, folder after: long paths would otherwise hide the names.
        lines = [f"{os.path.basename(f)}    ({os.path.dirname(f)})" for f in self.files]
        self._write_box("\n".join(lines), clear_first=True)
        self.reset_progress()

    def reset_progress(self):
        # An empty CTkProgressBar still draws a dot; hide the fill colour until a job runs.
        self.progress.set(0)
        self.progress.configure(progress_color=self.progress.cget("fg_color"))

    def clear(self):
        """Clears the file list and passphrases. Saved recipients/identities are kept."""
        self.files = []
        self.last_summary = None
        self.mode = None
        self.show_welcome()
        self.reset_progress()
        self.password.delete(0, "end")
        self.confirm.delete(0, "end")
        self.update_mode_and_input_visibility()
        self.update_action_buttons()

    # ------------------------------------------------------------------ actions

    def encrypt(self):
        if self.is_processing or not self.files:
            return
        if self.active_mode_kind() == "passphrase":
            pw = self.password.get()
            if not pw:
                self.log_to_box("Please enter a passphrase.", clear_first=True)
                return
            if pw != self.confirm.get():
                self.log_to_box("Passphrases do not match.", clear_first=True)
                return
            self.run_process("-e", pw)
        else:
            problem = self.recipient_encrypt_problem()
            if problem:
                self.log_to_box(problem, clear_first=True)
                return
            self.save_settings()
            self.run_process("-e", None)

    def decrypt(self):
        if self.is_processing or not self.files:
            return
        if self.active_mode_kind() == "passphrase":
            pw = self.password.get()
            if not pw:
                self.log_to_box("Please enter a passphrase.", clear_first=True)
                return
            self.run_process("-d", pw)
        else:
            if not self.get_recipient_inputs()[5]:
                self.log_to_box("Please provide at least one existing identity file.", clear_first=True)
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
        self.log_to_box("Cancelling...")

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
            self.log_to_box("age.exe not found.", clear_first=True)
            return

        active_kind = self.active_mode_kind()
        if active_kind == "passphrase" and not os.path.isfile(self.batchpass_path):
            self.log_to_box("age-plugin-batchpass.exe not found.", clear_first=True)
            return

        # Snapshot everything the worker needs; the worker never reads Tk widgets.
        _, valid_r, rfile, rfile_ok, _, valid_i = self.get_recipient_inputs()
        files = list(self.files)
        armor = self.armor_var.get()
        is_encrypt = flag == "-e"

        env = os.environ.copy()
        if active_kind == "passphrase":
            env["AGE_PASSPHRASE"] = password
            env["PATH"] = self.base_dir + os.pathsep + env.get("PATH", "")

        self.is_processing = True
        self.cancel_event.clear()
        self.progress.set(0)
        self.progress.configure(progress_color=ACCENT)
        self.update_action_buttons()

        def worker():
            ok = failed = 0
            last_folder = None
            summary = "Stopped."
            self.ui_queue.put(("progress", 0, len(files)))
            try:
                self.log_to_box("=== Processing ===", clear_first=True)
                self.log_to_box("Output is written next to the source files; existing files are never overwritten.")
                if active_kind == "passphrase":
                    self.log_to_box("Using passphrase mode via age-plugin-batchpass.")
                else:
                    self.log_to_box("Using recipient mode.")
                    if is_encrypt:
                        if valid_r:
                            self.log_to_box(f"Recipients from textbox: {len(valid_r)}")
                        if rfile_ok:
                            self.log_to_box(f"Recipients file: {rfile}")
                    else:
                        self.log_to_box(f"Identity files: {len(valid_i)}")

                for idx, f in enumerate(files, 1):
                    if self.cancel_event.is_set():
                        break
                    verb = "Encrypting" if is_encrypt else "Decrypting"
                    folder = os.path.dirname(f)
                    if folder != last_folder:
                        self.log_to_box(f"In {folder}:")
                        last_folder = folder
                    self.log_to_box(f"  [{idx}/{len(files)}] {verb} {os.path.basename(f)}")

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
                        self.log_to_box(f"      -> {os.path.basename(out_file)}")
                        if output:
                            self.log_to_box(output)
                    else:
                        if not self.cancel_event.is_set():
                            failed += 1
                        self.remove_output(part_file)  # this job's own .part file
                        self.log_to_box("      Cancelled" if self.cancel_event.is_set() else f"      FAILED: {output or 'unknown error'}")
                    self.inflight_output = None
                    self.ui_queue.put(("progress", idx, len(files)))

                skipped = len(files) - ok - failed
                summary = f"Finished: {ok} OK, {failed} failed"
                if skipped:
                    summary += f", {skipped} skipped (cancelled)"
                self.log_to_box(f"=== {summary} ===")
            finally:
                self.ui_queue.put(("done", True, summary + "."))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    @staticmethod
    def remove_output(path):
        try:
            if path and os.path.isfile(long_path(path)):
                os.remove(long_path(path))
        except OSError:
            pass

    def inspect(self):
        if self.is_processing:
            return
        targets = [f for f in self.files if f.lower().endswith(".age")]
        if not targets or not os.path.isfile(self.inspect_path):
            return
        self.is_processing = True
        self.cancel_event.clear()
        self.update_action_buttons()

        def worker():
            try:
                self.log_to_box("=== age-inspect ===", clear_first=True)
                for f in targets:
                    if self.cancel_event.is_set():
                        break
                    try:
                        _, output = self.run_tool([self.inspect_path, f], cancellable=True)
                    except Exception as exc:
                        output = f"Unexpected error: {exc}"
                    self.log_to_box(output)
                    self.log_to_box("")
                # Inspect replaced the visible file list, but the files stay loaded.
                self.log_to_box(f"{len(files_loaded)} file(s) still loaded for decryption.")
            finally:
                self.ui_queue.put(("done", False, None))

        files_loaded = list(self.files)
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
            self.log_to_box(f"{path} already exists. A key file is never overwritten; pick a new name.", clear_first=True)
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
            self.log_to_box(f"age-keygen failed: {output}", clear_first=True)
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
        self.log_to_box(f"New identity saved to {path} and added to Identity files.", clear_first=True)
        if mixes:
            self.log_to_box("Its public key was NOT added to Recipients: post-quantum and classic keys "
                            "can't be mixed in one file. It is in the key file, on the '# public key:' line.")
        elif public_key:
            self.log_to_box("Its public key was added to Recipients.")
        else:
            self.log_to_box("Could not read its public key; get it with: age-keygen -y " + path)
        self.log_to_box("Keep the identity file secret and backed up.")

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
