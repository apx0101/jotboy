#!/usr/bin/env python3
# jotboy - a two-panel terminal task outliner and text editor
# Copyright (C) 2026  <YOUR NAME>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""jotboy - a two-panel ncurses task outliner / text editor.

Left panel : top-level task lists.
Right panel: a word-wrapping text editor whose lines form a task tree,
             nesting expressed with a two-space indent.

Run `jotboy.py --help` for options, Ctrl+G inside the app for keys.
"""

from __future__ import annotations

import argparse
import base64
import curses
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import NamedTuple

# --------------------------------------------------------------------------
# key input
# --------------------------------------------------------------------------

_NAMED = {}


def _init_named():
    _NAMED.update({
        curses.KEY_UP: "UP",
        curses.KEY_DOWN: "DOWN",
        curses.KEY_LEFT: "LEFT",
        curses.KEY_RIGHT: "RIGHT",
        curses.KEY_HOME: "HOME",
        curses.KEY_END: "END",
        curses.KEY_NPAGE: "PGDN",
        curses.KEY_PPAGE: "PGUP",
        curses.KEY_BACKSPACE: "BACKSPACE",
        curses.KEY_DC: "DELETE",
        curses.KEY_IC: "INSERT",
        curses.KEY_ENTER: "ENTER",
        curses.KEY_BTAB: "BTAB",
        curses.KEY_RESIZE: "RESIZE",
        curses.KEY_SLEFT: "SHIFT_LEFT",
        curses.KEY_SRIGHT: "SHIFT_RIGHT",
        curses.KEY_F1: "F1",
        curses.KEY_F2: "F2",
        curses.KEY_F10: "F10",
    })


# extended terminfo names: kUP5 = ctrl+up, kRIT3 = alt+right, ...
_XKEY = re.compile(r"^k(RIT|LFT|UP|DN)(\d)$")
_XBASE = {"RIT": "RIGHT", "LFT": "LEFT", "UP": "UP", "DN": "DOWN"}
_CSI_LETTER = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT",
               "H": "HOME", "F": "END"}
_CSI_TILDE = {1: "HOME", 2: "INSERT", 3: "DELETE", 4: "END",
              5: "PGUP", 6: "PGDN", 7: "HOME", 8: "END"}


def _with_mod(base: str, mod: int) -> str:
    """mod is the xterm modifier parameter (1 = none)."""
    bits = max(0, mod - 1)
    if bits & 4:
        return "CTRL_" + base
    if bits & 2:
        return "ALT_" + base
    if bits & 1:
        return "SHIFT_" + base
    return base


class KeyReader:
    """Turns raw curses input into stable key names.

    Returns a one-character string for literal characters (``"a"``, ``"Z"``),
    ``"C-x"`` for control chords, ``"M-x"`` for alt chords, and an uppercase
    word for everything else (``"UP"``, ``"CTRL_LEFT"``, ``"BACKSPACE"``).
    """

    ESC_TIMEOUT = 40  # ms to wait for the rest of an escape sequence

    def __init__(self, win):
        self.win = win

    def _raw(self, timeout=None):
        self.win.timeout(-1 if timeout is None else timeout)
        try:
            return self.win.get_wch()
        except curses.error:
            return None          # timed out
        except KeyboardInterrupt:
            return "\x03"

    def get(self):
        try:
            ch = self._raw()
            if ch is None:
                return None
            if isinstance(ch, int):
                return self._named(ch)
            if ch == "\x1b":
                return self._escape()
            return self._char(ch)
        finally:
            self.win.timeout(-1)

    # -- helpers ----------------------------------------------------------
    def _named(self, code: int) -> str:
        name = _NAMED.get(code)
        if name:
            return name
        try:
            raw = curses.keyname(code).decode("ascii", "replace")
        except ValueError:
            raw = ""
        m = _XKEY.match(raw)
        if m:
            return _with_mod(_XBASE[m.group(1)], int(m.group(2)))
        return "UNKNOWN"

    @staticmethod
    def _char(ch: str) -> str:
        o = ord(ch)
        if o == 8 and curses.erasechar() == b"\x08":
            # this terminal sends ^H for Backspace, so it cannot also be
            # Ctrl+H; deleting text matters more than the panel toggle
            return "BACKSPACE"
        if o == 9:
            return "TAB"
        if o in (10, 13):
            return "ENTER"
        if o == 0:
            return "C-space"
        if o == 127:
            return "BACKSPACE"
        if o == 29:
            return "C-]"
        if o == 28:
            return "C-\\"
        if o == 31:
            return "C-/"
        if 1 <= o <= 26:
            return "C-" + chr(o + 96)
        if o < 32:
            return "UNKNOWN"
        return ch

    def _escape(self) -> str:
        alt = False
        nxt = self._raw(self.ESC_TIMEOUT)
        if nxt is None:
            return "ESC"
        if nxt == "\x1b":                    # some terminals double the Esc
            alt = True
            nxt = self._raw(self.ESC_TIMEOUT)
            if nxt is None:
                return "ESC"
        if isinstance(nxt, int):
            # curses decoded a whole key after our Esc, so Alt was held
            return self._alt(self._named(nxt))
        if nxt not in ("[", "O"):
            return "M-" + nxt if nxt.isprintable() else "ESC"
        params, final = "", None
        while True:
            c = self._raw(self.ESC_TIMEOUT)
            if c is None:
                return "ESC"
            if isinstance(c, int):
                return self._alt(self._named(c))
            o = ord(c)
            if 0x20 <= o <= 0x3F:
                params += c
            elif 0x40 <= o <= 0x7E:
                final = c
                break
            else:
                return "ESC"
        name = self._csi(params, final)
        return self._alt(name) if alt else name

    _MOVEMENT = ("UP", "DOWN", "LEFT", "RIGHT", "HOME", "END",
                 "PGUP", "PGDN", "DELETE", "INSERT")

    @classmethod
    def _alt(cls, name: str) -> str:
        """An Esc that prefixed an already-complete key means Alt was held.

        Only applied where the Esc is provably surplus - a plain ``Esc [ A``
        is what an unmodified arrow looks like and is never upgraded.
        """
        return "ALT_" + name if name in cls._MOVEMENT else name

    @staticmethod
    def _csi(params: str, final: str) -> str:
        parts = [p for p in params.split(";")]
        try:
            first = int(parts[0]) if parts and parts[0] else 1
        except ValueError:
            first = 1
        try:
            mod = int(parts[1]) if len(parts) > 1 and parts[1] else 1
        except ValueError:
            mod = 1
        if final in _CSI_LETTER:
            return _with_mod(_CSI_LETTER[final], mod)
        if final == "~" and first in _CSI_TILDE:
            return _with_mod(_CSI_TILDE[first], mod)
        if final == "Z":
            return "BTAB"
        return "UNKNOWN"


# --------------------------------------------------------------------------
# word wrap
# --------------------------------------------------------------------------

def wrap_segments(text: str, first_w: int, cont_w: int):
    """Greedy word wrap.

    Returns contiguous ``(start, end)`` index pairs covering ``text`` so that
    every caret position maps to exactly one visual row.
    """
    first_w = max(1, first_w)
    cont_w = max(1, cont_w)
    segs = []
    i, n, first = 0, len(text), True
    while True:
        w = first_w if first else cont_w
        if n - i <= w:
            segs.append((i, n))
            return segs
        brk = text.rfind(" ", i, i + w + 1)
        if brk > i:
            nxt = brk + 1
            while nxt < n and text[nxt] == " ":
                nxt += 1
        else:
            nxt = i + w
        if nxt <= i:
            nxt = i + w
        segs.append((i, nxt))
        i, first = nxt, False


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

@dataclass
class Line:
    text: str = ""
    indent: int = 0
    collapsed: bool = False

    def copy(self) -> "Line":
        return Line(self.text, self.indent, self.collapsed)


@dataclass
class TaskList:
    title: str = "untitled"
    lines: list = field(default_factory=lambda: [Line()])
    cursor: int = 0
    col: int = 0
    plain: bool = False          # plain text: no indenting, folding or bold

    def normalise(self):
        if not self.lines:
            self.lines = [Line()]
        if self.plain:
            for ln in self.lines:
                ln.indent, ln.collapsed = 0, False
        # no line may be more than one level deeper than the one above it
        prev = -1
        for ln in self.lines:
            ln.indent = max(0, min(ln.indent, prev + 1))
            prev = ln.indent
        self.cursor = max(0, min(self.cursor, len(self.lines) - 1))
        self.col = max(0, min(self.col, len(self.lines[self.cursor].text)))


def block_end(lines, i: int) -> int:
    """Index just past the subtree rooted at line ``i``."""
    j, lvl = i + 1, lines[i].indent
    while j < len(lines) and lines[j].indent > lvl:
        j += 1
    return j


def has_children(lines, i: int) -> bool:
    return i + 1 < len(lines) and lines[i + 1].indent > lines[i].indent


def parent_of(lines, i: int):
    lvl = lines[i].indent
    if lvl == 0:
        return None
    for j in range(i - 1, -1, -1):
        if lines[j].indent < lvl:
            return j
    return None


def visible_indices(lines):
    out, i, n = [], 0, len(lines)
    while i < n:
        out.append(i)
        i = block_end(lines, i) if lines[i].collapsed else i + 1
    return out


def is_hidden(lines, i: int) -> bool:
    lvl = lines[i].indent
    for j in range(i - 1, -1, -1):
        if lines[j].indent < lvl:
            if lines[j].collapsed:
                return True
            lvl = lines[j].indent
            if lvl == 0:
                return False
    return False


def tidy_blanks(lines, keep=None):
    """Enforce that no blank line is indented, and return the new ``keep``.

    A blank line is a separator between top-level tasks, so it has no business
    sitting at depth - two lines that render identically would otherwise
    belong to different tasks. Flattening one that still has deeper lines
    under it would adopt them, so those are dropped instead.

    ``keep`` is the line being edited, exempt so that Enter on a task can open
    an empty subtask for you to type into; it is tidied once you leave it.
    """
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.indent and not ln.text.strip() and i != keep:
            if i + 1 < len(lines) and lines[i + 1].indent > 0:
                del lines[i]
                if keep is not None and keep > i:
                    keep -= 1
                continue
            ln.indent = 0
        i += 1
    return keep


def reveal(lines, i: int):
    """Expand whatever is hiding line ``i``."""
    p = parent_of(lines, i)
    while p is not None:
        lines[p].collapsed = False
        p = parent_of(lines, p)


def nearest_visible(lines, i: int) -> int:
    """Closest ancestor of ``i`` that is actually on screen."""
    while is_hidden(lines, i):
        p = parent_of(lines, i)
        if p is None:
            return 0
        i = p
    return i


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def blank_store():
    """A new, empty buffer - jotboy never invents content of its own."""
    return [TaskList("untitled", [Line()])]


def load(path):
    if not os.path.exists(path):
        return blank_store(), False
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    lists = []
    for raw in data.get("lists", []):
        tl = TaskList(
            raw.get("title", "untitled"),
            [Line(l.get("t", ""), int(l.get("i", 0)), bool(l.get("c")))
             for l in raw.get("lines", [])] or [Line()],
            int(raw.get("cursor", 0)),
            int(raw.get("col", 0)),
            bool(raw.get("plain", False)),
        )
        tl.normalise()
        tidy_blanks(tl.lines)            # a hand-edited file may break it
        tl.normalise()
        lists.append(tl)
    return (lists or blank_store()), True


def save(path, lists):
    data = {"version": 1, "lists": [
        {"title": tl.title, "cursor": tl.cursor, "col": tl.col,
         "plain": tl.plain,
         "lines": [{"t": l.text, "i": l.indent, "c": l.collapsed}
                   for l in tl.lines]}
        for tl in lists]}
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".jotboy-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_text(tl) -> str:
    """One task list as the plain indented text the panel shows."""
    body = "\n".join(("  " * ln.indent + ln.text).rstrip() for ln in tl.lines)
    return body.rstrip("\n") + "\n"


def as_text(lists) -> str:
    out = []
    for tl in lists:
        out.append("# " + tl.title)
        out.append(list_text(tl).rstrip("\n"))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------
# the system clipboard
# --------------------------------------------------------------------------

CLIPBOARD_TOOLS = (
    ("wl-copy", ()),                                  # wayland
    ("xclip", ("-selection", "clipboard")),           # x11
    ("xsel", ("--clipboard", "--input")),
    ("pbcopy", ()),                                   # macos
    ("clip.exe", ()),                                 # wsl
)


def to_clipboard(text: str):
    """Hand UTF-8 to whichever clipboard tool exists. Returns its name."""
    data = text.encode("utf-8")
    for name, args in CLIPBOARD_TOOLS:
        exe = shutil.which(name)
        if exe is None:
            continue
        try:
            done = subprocess.run([exe, *args], input=data,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return name
    return None


def osc52(text: str) -> str:
    """The terminal's own clipboard channel: works over ssh, if allowed."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return "\x1b]52;c;%s\x07" % payload


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

@dataclass
class Row:
    line: int
    start: int
    end: int
    first: bool
    pad: int
    last: bool


HELP_LEFT = [
    ("Left panel - task lists", ""),
    ("Up / Down", "select a list"),
    ("Enter", "open the list in the editor"),
    ("Ctrl+N", "new list; asks whether it is an"),
    ("", "structured or plain text"),
    ("Ctrl+D", "delete list"),
    ("Ctrl+R  (or F2)", "rename list"),
    ("Alt+Up / Alt+Down", "move the list up / down"),
]

HELP_RIGHT = [
    ("Right panel - editor", ""),
    ("Up/Down/Left/Right", "move the caret (wrapped rows)"),
    ("Home/End PgUp/PgDn", "row ends, page up/down"),
    ("typing, Backspace, Del", "edit text"),
    ("Ctrl+Right / Ctrl+Left", "indent / outdent the task"),
    ("two spaces at col 0", "indent: make this a subtask"),
    ("Backspace at col 0", "outdent, then merge into the line above"),
    ("Enter", "split line / add sibling or first subtask"),
    ("Ctrl+Up", "collapse task (or jump to parent and collapse)"),
    ("Ctrl+Down", "expand task"),
    ("Alt+Up / Alt+Down", "move task up / down among its siblings;"),
    ("", "subtasks always travel with it"),
    ("Ctrl+K", "cut the task and all its subtasks"),
    ("Ctrl+U", "paste it after the task under the"),
    ("", "caret, at the indent it was cut at"),
    ("", ""),
    ("A plain text list has no indenting, folding or bold: it is"
     " just lines.", ""),
]

HELP_GLOBAL = [
    ("Both panels", ""),
    ("Tab", "move focus between the two panels"),
    ("Ctrl+S", "save"),
    ("Ctrl+O", "write out: save under a name you"),
    ("", "can edit, then keep that name"),
    ("Ctrl+Y", "copy the whole open list to the"),
    ("", "system clipboard, as UTF-8"),
    ("Ctrl+H", "hide or show the task list panel"),
    ("Ctrl+F", "find text in the open list; press it"),
    ("", "again for the next match, and Enter"),
    ("", "alone repeats the last search"),
    ("Ctrl+T", "cycle colour theme (--theme lists them)"),
    ("Ctrl+X", "quit"),
    ("Ctrl+G  (or F1)", "this help"),
    ("", ""),
    ("In a prompt: Ctrl+U clears it, Ctrl+K cuts to the end,", ""),
    ("Esc cancels.", ""),
]


# --------------------------------------------------------------------------
# themes
# --------------------------------------------------------------------------
#
# Every pair names both a foreground *and* a background, and the screen gets a
# painted backdrop, so nothing shows through from the terminal's own palette:
# a solarised or light-background terminal still renders jotboy the same way.
#
# The 256-colour tones are cube indices (16+), which terminal themes leave
# alone - it is the 0-15 range they redefine. That is what makes the hues
# predictable without rewriting the terminal's palette out from under other
# programs with init_color().

class Theme(NamedTuple):
    bg: int         # the page: every cell that is not something else
    text: int       # ordinary task text
    bright: int     # fold arrows, messages, the open list's title
    dim: int        # counts, the column rule, gutters
    deep: int       # the focused panel's slab
    bar: int        # title and key bands, carrying bg-coloured text
    plain: tuple    # (fg, bg) for terminals with only eight colours
    desc: str


_G, _M, _B = curses.COLOR_GREEN, curses.COLOR_MAGENTA, curses.COLOR_BLUE
_Y, _C, _W, _K = (curses.COLOR_YELLOW, curses.COLOR_CYAN, curses.COLOR_WHITE,
                  curses.COLOR_BLACK)

THEMES = {
    #             bg  text bright dim deep bar
    "green":   Theme(16,  41, 48,  28, 22,  35, (_G, _K), "green on black"),
    "purple":  Theme(16, 141, 177, 97, 53,  98, (_M, _K), "purple on black"),
    "blue":    Theme(16,  39, 51,  25, 17,  32, (_B, _K), "blue on black"),
    "white":   Theme(16, 252, 231, 245, 238, 250, (_W, _K), "white on black"),
    "amber":   Theme(16, 214, 220, 130, 58, 214, (_Y, _K),
                     "amber on black, VT220 phosphor"),
    "amstrad": Theme(18, 226, 229, 184, 17, 226, (_Y, _B),
                     "yellow on blue, Amstrad CPC"),
    "c64":     Theme(61, 189, 231, 147, 18, 189, (_C, _B),
                     "light blue on blue, Commodore 64"),
    "mac":     Theme(231, 16, 16, 245, 252, 16, (_K, _W),
                     "black on white, Macintosh"),
}
THEME_NAMES = list(THEMES)


def theme_pairs(name):
    """{pair number: (fg, bg)} for the named theme, sized to the terminal."""
    t = THEMES[name]
    if curses.COLORS >= 256:
        return {1: (t.bg, t.bar), 2: (t.bright, t.deep), 4: (t.dim, t.bg),
                5: (t.dim, t.bg), 6: (t.bright, t.bg), 7: (t.bright, t.bg),
                8: (t.bg, t.bar), 9: (t.text, t.deep), 10: (t.text, t.bg)}
    # eight colours: no room for tones, so the bands and the slab are simply
    # the theme reversed
    fg, bg = t.plain
    return {1: (bg, fg), 2: (bg, fg), 4: (fg, bg), 5: (fg, bg), 6: (fg, bg),
            7: (fg, bg), 8: (bg, fg), 9: (bg, fg), 10: (fg, bg)}


class App:
    LEFT_MIN, LEFT_MAX = 16, 34

    def __init__(self, stdscr, path, lists, existed, theme="green"):
        self.scr = stdscr
        self.path = path
        self.theme = theme if theme in THEMES else THEME_NAMES[0]
        self.apply_theme()
        self.lists = lists
        self.reader = KeyReader(stdscr)
        self.sel = 0                 # selected list in the left panel
        self.focus = "right"
        self.dirty = False
        if existed:
            where = os.path.basename(path)
        elif path:
            where = "new file: " + path
        else:
            where = "new buffer - Ctrl+O names it"
        self.msg = "%s  -  Ctrl+G for help" % where
        self.clip = []
        self.needle = ""          # last Ctrl+F term
        self.show_left = True     # Ctrl+H hides the list panel
        self.last_key = ""
        self.left_top = 0
        self.top = 0                 # first visible row of the editor
        self.goal_x = None
        self.running = True
        self.unicode = "utf" in (locale.getpreferredencoding() or "").lower()

    # -- geometry ---------------------------------------------------------
    @property
    def cur(self):
        return self.lists[self.sel] if self.lists else None

    def layout(self):
        h, w = self.scr.getmaxyx()
        if self.show_left:
            lw = max(self.LEFT_MIN, min(self.LEFT_MAX, w // 4))
            lw = min(lw, max(8, w - 24))
            self.rx = lw + 1              # a column for the rule
        else:
            lw = self.rx = 0              # the editor takes the whole width
        self.h, self.w, self.lw = h, w, lw
        self.ctop = 2
        self.cheight = max(1, h - 4)
        # the list panel needs no caption, so it starts where the editor's
        # title sits and gets that row back as another list
        self.ltop = 1
        self.lheight = self.cheight + 1
        self.rw = max(4, w - self.rx)
        self.text_w = max(8, self.rw - 1)

    # -- rows -------------------------------------------------------------
    def build_rows(self):
        rows, tl = [], self.cur
        if tl is None:
            return rows
        lines = tl.lines
        for li in visible_indices(lines):
            ln = lines[li]
            pad = 0 if tl.plain else min(2 * ln.indent + 2,
                                        max(4, self.text_w - 8))
            segs = wrap_segments(ln.text, self.text_w - pad, self.text_w - pad)
            for k, (a, b) in enumerate(segs):
                rows.append(Row(li, a, b, k == 0, pad, k == len(segs) - 1))
        return rows

    def cursor_row(self, rows):
        tl = self.cur
        found = 0
        for i, r in enumerate(rows):
            if r.line == tl.cursor and r.start <= tl.col:
                found = i
        return found

    def row_x(self, rows, idx):
        r = rows[idx]
        return min(r.pad + (self.cur.col - r.start), self.text_w - 1)

    # -- drawing ----------------------------------------------------------
    def put(self, y, x, s, attr=0, limit=None):
        if y < 0 or y >= self.h or x >= self.w or not s:
            return
        end = self.w if limit is None else min(self.w, limit)
        s = s[:max(0, end - x)]
        if not s:
            return
        try:
            self.scr.addstr(y, x, s, attr)
        except curses.error:
            pass

    def draw(self):
        self.layout()
        self.scr.erase()
        if self.h < 8 or self.w < 40:
            self.put(0, 0, "terminal too small")
            self.scr.refresh()
            return
        c = self.pair
        title = " jotboy "
        self.put(0, 0, " " * self.w, c(1))
        self.put(0, 0, title, c(1) | curses.A_BOLD)
        flag = " *" if self.dirty else ""
        right = "%s%s " % (
            self.short_path(self.path or "(no file)",
                            self.w - len(title) - len(flag) - 3), flag)
        self.put(0, max(len(title) + 1, self.w - len(right)), right, c(1))

        lact = self.focus == "left"
        head = " " + (self.cur.title if self.cur else "no list")
        if self.cur is not None and self.cur.plain:
            head += "  [plain text]"
        self.put(1, self.rx, head.ljust(self.rw),
                 c(2) | (curses.A_BOLD if not lact else curses.A_DIM))
        if self.show_left:
            for y in range(1, self.h - 2):
                self.put(y, self.lw, "|", c(4))
            self.draw_left()
        self.draw_right()
        self.draw_status()
        self.place_cursor()
        self.scr.refresh()

    def apply_theme(self):
        if not curses.has_colors():
            return
        for n, (fg, bg) in theme_pairs(self.theme).items():
            try:
                curses.init_pair(n, fg, bg)
            except curses.error:
                pass
        # paint the backdrop too, so untouched cells are the theme's black
        # rather than whatever the terminal happens to use
        self.scr.bkgd(" ", curses.color_pair(10))

    def copy_node(self):
        """Ctrl+Y: the whole open list onto the system clipboard, as UTF-8."""
        if self.cur is None:
            self.msg = "nothing to copy"
            return
        text = list_text(self.cur)
        size = len(text.encode("utf-8"))
        via = to_clipboard(text)
        if via is None:                  # no helper installed: ask the terminal
            try:
                sys.stdout.write(osc52(text))
                sys.stdout.flush()
                via = "OSC 52"
            except (OSError, ValueError):
                via = None
            self.scr.clearok(True)       # that went behind curses' back
        if via is None:
            self.msg = "no clipboard - install xclip, xsel or wl-copy"
            return
        self.msg = "copied %r: %d lines, %d bytes of UTF-8, via %s" % (
            self.cur.title, len(self.cur.lines), size, via)

    def toggle_left(self):
        self.show_left = not self.show_left
        if not self.show_left:
            self.focus = "right"         # nothing left to focus
            self.msg = "list panel hidden - Ctrl+H shows it"
        else:
            self.msg = "list panel shown"
        self.scr.clearok(True)           # the whole width changes meaning

    def cycle_theme(self):
        nxt = (THEME_NAMES.index(self.theme) + 1) % len(THEME_NAMES)
        self.theme = THEME_NAMES[nxt]
        self.apply_theme()
        self.scr.clearok(True)               # repaint every cell, not a diff
        self.msg = "theme: %s - %s" % (self.theme, THEMES[self.theme].desc)

    def pair(self, n):
        return curses.color_pair(n) if curses.has_colors() else 0

    def short_path(self, path, room):
        """Trim a path from the left so the interesting end survives."""
        if len(path) <= room or room < 4:
            return path
        return ("…" if self.unicode else "<") + path[-(room - 1):]

    def panel_attr(self):
        """The focused left panel's own background: a dark slab, not a flash.

        Falls back to plain reverse video where there are no colours at all,
        so the focused panel is always distinguishable.
        """
        if self.focus != "left":
            return 0
        return self.pair(9) if curses.has_colors() else curses.A_REVERSE

    def draw_left(self):
        c = self.pair
        h, top = self.lheight, self.ltop
        # the whole panel takes a darker background while it has focus, so
        # which side is live is obvious at a glance; the selection then stands
        # out against it by being reversed
        inv = self.panel_attr()
        for i in range(h):
            self.put(top + i, 0, " " * self.lw, inv, self.lw)
        if self.sel < self.left_top:
            self.left_top = self.sel
        if self.sel >= self.left_top + h:
            self.left_top = self.sel - h + 1
        for i in range(h):
            idx = self.left_top + i
            if idx >= len(self.lists):
                break
            tl = self.lists[idx]
            n = sum(1 for l in tl.lines if l.indent == 0 and l.text.strip())
            count = " %d" % n
            tag = " [p]" if tl.plain else ""
            room = self.lw - 3 - len(count) - len(tag)
            name = tl.title if len(tl.title) <= room else tl.title[:room - 1] + "~"
            body = " " + name.ljust(room) + tag + count + " "
            if idx == self.sel:
                attr = inv | curses.A_REVERSE | curses.A_BOLD
                self.put(top + i, 0, body.ljust(self.lw), attr, self.lw)
            else:
                self.put(top + i, 0, body.ljust(self.lw), inv, self.lw)
                self.put(top + i, self.lw - 1 - len(count) - 1,
                         count, inv or c(5), self.lw)
        if not self.lists:
            self.put(top, 1, "(none - Ctrl+N)", inv or c(5), self.lw)

    def marks(self, kind):
        if not self.unicode:
            return {"open": "- ", "shut": "+ ", "leaf": "  "}[kind]
        return {"open": "▾ ", "shut": "▸ ", "leaf": "  "}[kind]

    @staticmethod
    def fold_mark(lines, i):
        """Which fold marker line ``i`` gets.

        A blank line is a spacer, not a task, so it stays bare even when a
        deeper line follows it - unless it is collapsed, where the marker is
        the only clue that something is hidden underneath.
        """
        ln = lines[i]
        if not has_children(lines, i):
            return "leaf"
        if ln.collapsed:
            return "shut"
        return "leaf" if not ln.text.strip() else "open"

    def draw_right(self):
        c = self.pair
        tl = self.cur
        if tl is None:
            self.put(self.ctop, self.rx + 1, "Ctrl+N in the left panel "
                     "creates a task list.", c(5))
            self.rows = []
            return
        rows = self.rows = self.build_rows()
        cr = self.cursor_row(rows)
        if cr < self.top:
            self.top = cr
        if cr >= self.top + self.cheight:
            self.top = cr - self.cheight + 1
        self.top = max(0, min(self.top, max(0, len(rows) - self.cheight)))

        lines = tl.lines
        for i in range(self.cheight):
            ri = self.top + i
            if ri >= len(rows):
                break
            r = rows[ri]
            ln = lines[r.line]
            y = self.ctop + i
            if r.first and not tl.plain:
                kind = self.fold_mark(lines, r.line)
                gut = "  " * ln.indent + self.marks(kind)
                self.put(y, self.rx, gut[:r.pad].ljust(r.pad),
                         c(5) if kind == "leaf" else c(6))
            attr = 0 if tl.plain else (
                curses.A_BOLD if (ln.indent == 0 and ln.text.strip()) else 0)
            body = ln.text[r.start:r.end].rstrip()
            self.put(y, self.rx + r.pad, body, attr, self.rx + self.text_w)
            if ln.collapsed and r.last and not tl.plain:
                n = block_end(lines, r.line) - r.line - 1
                tag = ("  (%d)" if body else "(%d)") % n
                x = self.rx + r.pad + len(body)
                if x + len(tag) < self.rx + self.text_w:
                    self.put(y, x, tag, c(5))

    def editing_list(self):
        """True when the keys that matter are the left panel's."""
        if not self.show_left:
            return False
        return self.focus == "left" or self.cur is None

    def hints(self):
        """(drop-order, text) for the focused panel.

        The bar advertises what the keys do *here* - offering "indent" while
        the list panel has focus would just be a lie. Lowest drop-order
        survives longest when the terminal is narrow.
        """
        ud = "↑/↓" if self.unicode else "Up/Dn"
        lr = "←/→" if self.unicode else "Left/Right"
        common = [(2, "Tab panel" if self.show_left else "CTRL+H lists"),
                  (1, "CTRL+S save"), (0, "CTRL+X quit"), (3, "CTRL+G help")]
        if self.editing_list():
            return [common[0], (9, "%s select" % ud), (8, "Enter open"),
                    (4, "CTRL+N new"), (5, "CTRL+R rename"),
                    (6, "CTRL+D delete"), (7, "ALT+%s move" % ud),
                    common[1], common[2], common[3]]
        editing = [(6, "ALT+%s move" % ud), (8, "CTRL+K cut"),
                   (9, "CTRL+U paste"), (7, "CTRL+F find"),
                   (11, "CTRL+Y copy out")]
        if not self.cur.plain:      # a plain list has neither of these
            editing = [(4, "CTRL+%s indent" % lr),
                       (5, "CTRL+%s fold" % ud)] + editing
        return ([common[0]] + editing +
                [common[1], (10, "CTRL+O save as"), common[2], common[3]])

    def draw_status(self):
        c = self.pair
        y = self.h - 2
        hints = self.hints()
        while len(hints) > 2 and len("  ".join(t for _, t in hints)) > self.w - 2:
            hints.remove(max(hints))
        self.put(y, 0, " " * self.w, c(8))
        self.put(y, 1, "  ".join(t for _, t in hints), c(8))

        pos = ""
        if self.cur is not None:
            pos = "%d/%d" % (self.cur.cursor + 1, len(self.cur.lines))
            self.put(self.h - 1, max(0, self.w - len(pos) - 1), pos, c(5))
        self.put(self.h - 1, 1, self.msg[:max(0, self.w - len(pos) - 3)], c(7))

    def place_cursor(self):
        try:
            if self.focus == "left" or self.cur is None or not self.rows:
                curses.curs_set(0)
                return
            curses.curs_set(1)
            cr = self.cursor_row(self.rows)
            y = self.ctop + (cr - self.top)
            if self.ctop <= y < self.ctop + self.cheight:
                self.scr.move(y, min(self.w - 1,
                                     self.rx + self.row_x(self.rows, cr)))
        except curses.error:
            pass

    # -- overlays ---------------------------------------------------------
    def prompt(self, label, initial="", strip=True):
        buf, pos = list(initial), len(initial)
        while True:
            self.draw()
            y = self.h - 1
            self.put(y, 0, " " * self.w)
            # the label stays put; only the value scrolls, so the caret is
            # always on screen without losing sight of the question
            avail = max(1, self.w - 2)
            lw = min(len(label), max(0, avail - 8))
            field_w = max(1, avail - lw)
            start = max(0, pos - field_w + 1)
            value = "".join(buf)[start:start + field_w]
            self.put(y, 1, label[:lw] + value, self.pair(7) | curses.A_BOLD)
            try:
                curses.curs_set(1)
                self.scr.move(y, 1 + lw + pos - start)
            except curses.error:
                pass
            self.scr.refresh()
            k = self.reader.get()
            if k in (None, "UNKNOWN", "RESIZE"):
                continue
            if k == "ENTER":
                text = "".join(buf)
                return text.strip() if strip else text
            if k in ("ESC", "C-c", "C-g"):
                return None
            if k == "BACKSPACE":
                if pos:
                    del buf[pos - 1]
                    pos -= 1
            elif k == "DELETE":
                if pos < len(buf):
                    del buf[pos]
            elif k == "LEFT":
                pos = max(0, pos - 1)
            elif k == "RIGHT":
                pos = min(len(buf), pos + 1)
            elif k in ("HOME", "C-a"):
                pos = 0
            elif k in ("END", "C-e"):
                pos = len(buf)
            elif k == "C-u":                 # clear, to retype a long path
                buf, pos = [], 0
            elif k == "C-k":
                del buf[pos:]
            elif len(k) == 1 and k.isprintable():
                buf.insert(pos, k)
                pos += 1

    def confirm(self, question):
        ans = self.prompt(question + " (y/N) ")
        return bool(ans) and ans.lower().startswith("y")

    def ask_key(self, question, keys):
        """Ask on the message line and act on one keystroke - no Enter.

        Unrecognised keys are ignored rather than treated as an answer, so a
        stray press cannot quit or discard anything.
        """
        while True:
            self.draw()
            y = self.h - 1
            self.put(y, 0, " " * self.w)
            self.put(y, 1, question[:self.w - 2],
                     self.pair(7) | curses.A_BOLD)
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.scr.refresh()
            k = self.reader.get()
            if k in ("ESC", "C-c", "C-g"):
                return None
            if k and len(k) == 1 and k.lower() in keys:
                return k.lower()

    def help_rows(self):
        """The focused panel's keys first, then the shared ones, then the rest."""
        here, there = ((HELP_LEFT, HELP_RIGHT) if self.editing_list()
                       else (HELP_RIGHT, HELP_LEFT))
        return here + [("", "")] + HELP_GLOBAL + [("", "")] + there

    def show_help(self):
        off = 0
        while True:
            self.draw()
            HELP = self.help_rows()
            width = min(self.w - 4, 74)
            height = min(self.h - 2, len(HELP) + 4)
            body = height - 3
            off = max(0, min(off, max(0, len(HELP) - body)))
            y0 = max(0, (self.h - height) // 2)
            x0 = max(0, (self.w - width) // 2)
            for i in range(height):
                self.put(y0 + i, x0, " " * width, self.pair(2))
            self.put(y0, x0 + 2, " keys: %s " % (
                "task lists" if self.editing_list() else "editor"),
                self.pair(2) | curses.A_BOLD)
            for i, (k, d) in enumerate(HELP[off:off + body]):
                y = y0 + 2 + i
                if not d:
                    self.put(y, x0 + 2, k[:width - 4],
                             self.pair(2) | curses.A_BOLD)
                else:
                    self.put(y, x0 + 2, k.ljust(24)[:24], self.pair(2))
                    self.put(y, x0 + 27, d[:width - 29], self.pair(2))
            more = " more below - Up/Down scrolls " if off + body < len(HELP) \
                else " any key closes "
            self.put(y0 + height - 1, x0 + 2, more,
                     self.pair(2) | curses.A_DIM)
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.scr.refresh()
            k = self.reader.get()
            if k in (None, "RESIZE"):
                continue
            if k in ("UP", "PGUP"):
                off -= 1 if k == "UP" else body
            elif k in ("DOWN", "PGDN"):
                off += 1 if k == "DOWN" else body
            else:
                return

    # -- left panel -------------------------------------------------------
    def key_left_panel(self, k):
        n = len(self.lists)
        if k == "UP":
            self.sel = max(0, self.sel - 1)
        elif k == "DOWN":
            self.sel = min(n - 1, self.sel + 1) if n else 0
        elif k in ("CTRL_UP", "HOME", "PGUP"):
            self.sel = 0
        elif k in ("CTRL_DOWN", "END", "PGDN"):
            self.sel = max(0, n - 1)
        elif k == "ENTER":
            if self.lists:
                self.focus = "right"
        elif k in ("C-r", "F2"):
            if self.lists:
                new = self.prompt("Rename list: ", self.cur.title)
                if new:
                    self.cur.title = new
                    self.dirty = True
        elif k == "C-n":
            new = self.prompt("New list: ", "")
            if new:
                kind = self.ask_key(
                    "s = structured   p = plain text   Esc = cancel", "sp")
                if kind is None:
                    self.msg = "cancelled"
                    return
                self.lists.insert(self.sel + 1 if self.lists else 0,
                                  TaskList(new, [Line()], plain=kind == "p"))
                self.sel = self.sel + 1 if n else 0
                self.dirty = True
                self.focus = "right"
                self.msg = "new %s list %r" % (
                    "plain text" if kind == "p" else "structured", new)
        elif k == "C-d":
            if self.lists and self.confirm("Delete list %r?" % self.cur.title):
                del self.lists[self.sel]
                self.sel = max(0, min(self.sel, len(self.lists) - 1))
                self.dirty = True
        elif k == "ALT_UP":
            if self.sel > 0:
                self.lists[self.sel - 1], self.lists[self.sel] = (
                    self.lists[self.sel], self.lists[self.sel - 1])
                self.sel -= 1
                self.dirty = True
        elif k == "ALT_DOWN":
            if self.sel + 1 < n:
                self.lists[self.sel + 1], self.lists[self.sel] = (
                    self.lists[self.sel], self.lists[self.sel + 1])
                self.sel += 1
                self.dirty = True
        if self.cur is not None:
            self.top = 0

    # -- editor -----------------------------------------------------------
    def key_editor(self, k):
        tl = self.cur
        if tl is None:
            self.msg = "no task list - press Ctrl+Left then Ctrl+N"
            return
        lines = tl.lines
        cur = lines[tl.cursor]
        vertical = k in ("UP", "DOWN", "PGUP", "PGDN")

        if k == "LEFT":
            if tl.col > 0:
                tl.col -= 1
            else:
                vis = visible_indices(lines)
                p = vis.index(tl.cursor)
                if p > 0:
                    tl.cursor = vis[p - 1]
                    tl.col = len(lines[tl.cursor].text)
        elif k == "RIGHT":
            if tl.col < len(cur.text):
                tl.col += 1
            else:
                vis = visible_indices(lines)
                p = vis.index(tl.cursor)
                if p + 1 < len(vis):
                    tl.cursor, tl.col = vis[p + 1], 0
        elif k in ("UP", "DOWN"):
            self.move_row(-1 if k == "UP" else 1)
        elif k in ("PGUP", "PGDN"):
            self.move_row((-1 if k == "PGUP" else 1) * (self.cheight - 1))
        elif k == "HOME":
            r = self.rows[self.cursor_row(self.rows)]
            tl.col = r.start if tl.col != r.start else 0
        elif k == "END":
            r = self.rows[self.cursor_row(self.rows)]
            tl.col = r.end if r.last else max(r.start, r.end - 1)
        elif k == "BACKSPACE":
            self.backspace()
        elif k == "DELETE":
            self.forward_delete()
        elif k == "ENTER":
            self.split_line()
        elif k == "CTRL_RIGHT":
            self.reindent(+1)
        elif k == "CTRL_LEFT":
            if not self.reindent(-1):
                self.msg = "already at the top level"
        elif k == "CTRL_UP":
            self.collapse()
        elif k == "CTRL_DOWN":
            self.expand()
        elif k == "ALT_UP":
            self.move_task(-1)
        elif k == "ALT_DOWN":
            self.move_task(+1)
        elif k == "C-k":
            self.cut()
        elif k == "C-u":
            self.paste()
        elif k == "C-a":
            tl.col = 0
        elif k == "C-e":
            tl.col = len(cur.text)
        elif k == " " and tl.col == 1 and cur.text.startswith(" "):
            # a second space at the start of a line: make it an indent level
            if self.reindent(+1):
                cur.text = cur.text[1:]
                tl.col = 0
            else:
                cur.text = " " + cur.text      # refused - keep them as text
                tl.col = 2
            self.dirty = True
        elif len(k) == 1 and k.isprintable():
            cur.text = cur.text[:tl.col] + k + cur.text[tl.col:]
            tl.col += 1
            self.dirty = True
        else:
            return
        if not vertical:
            self.goal_x = None
        tl.cursor = tidy_blanks(lines, tl.cursor)
        tl.normalise()

    def move_row(self, delta):
        tl, rows = self.cur, self.rows
        if not rows:
            return
        cr = self.cursor_row(rows)
        if self.goal_x is None:
            self.goal_x = self.row_x(rows, cr)
        target = max(0, min(len(rows) - 1, cr + delta))
        if target == cr:
            if delta < 0:
                tl.col = rows[cr].start if not rows[cr].first else 0
            else:
                tl.col = len(tl.lines[tl.cursor].text)
            return
        r = rows[target]
        want = max(0, self.goal_x - r.pad)
        span = r.end - r.start if r.last else max(0, r.end - r.start - 1)
        tl.cursor = r.line
        tl.col = r.start + min(want, span)

    def backspace(self):
        tl = self.cur
        lines = tl.lines
        cur = lines[tl.cursor]
        if tl.col > 0:
            cur.text = cur.text[:tl.col - 1] + cur.text[tl.col:]
            tl.col -= 1
            self.dirty = True
            return
        if cur.indent > 0:
            self.reindent(-1)
            return
        vis = visible_indices(lines)
        p = vis.index(tl.cursor)
        if p == 0:
            return
        prev = vis[p - 1]
        if has_children(lines, prev) and lines[prev].collapsed:
            lines[prev].collapsed = False
            self.msg = "expanded to merge"
            return
        if lines[tl.cursor].indent > lines[prev].indent and cur.text == "":
            del lines[tl.cursor]
            tl.cursor, tl.col = prev, len(lines[prev].text)
            self.dirty = True
            return
        if block_end(lines, tl.cursor) != tl.cursor + 1:
            self.msg = "cannot merge a task that has subtasks"
            return
        tl.col = len(lines[prev].text)
        lines[prev].text += cur.text
        del lines[tl.cursor]
        tl.cursor = prev
        self.dirty = True

    def forward_delete(self):
        tl = self.cur
        lines = tl.lines
        cur = lines[tl.cursor]
        if tl.col < len(cur.text):
            cur.text = cur.text[:tl.col] + cur.text[tl.col + 1:]
            self.dirty = True
            return
        vis = visible_indices(lines)
        p = vis.index(tl.cursor)
        if p + 1 >= len(vis):
            return
        nxt = vis[p + 1]
        if nxt != tl.cursor + 1 or has_children(lines, nxt):
            self.msg = "cannot merge a task that has subtasks"
            return
        cur.text += lines[nxt].text
        del lines[nxt]
        self.dirty = True

    def split_line(self):
        tl = self.cur
        lines = tl.lines
        cur = lines[tl.cursor]
        head, tail = cur.text[:tl.col], cur.text[tl.col:]
        kids = has_children(lines, tl.cursor)
        if tail == "" and kids and not cur.collapsed:
            at, indent = tl.cursor + 1, cur.indent + 1
        elif tail == "" and kids and cur.collapsed:
            at, indent = block_end(lines, tl.cursor), cur.indent
        else:
            at, indent = tl.cursor + 1, cur.indent
            cur.text = head
        lines.insert(at, Line(tail, indent))
        tl.cursor, tl.col = at, 0
        self.dirty = True

    def reindent(self, delta) -> bool:
        """Shift the line and its subtree one level. False if not allowed."""
        tl = self.cur
        if tl.plain:
            self.msg = "plain text list: no indenting"
            return False
        lines = tl.lines
        i = tl.cursor
        cur = lines[i]
        if delta > 0:
            vis = visible_indices(lines)
            p = vis.index(i)
            if p == 0:
                self.msg = "the first task cannot be indented"
                return False
            if cur.indent > lines[vis[p - 1]].indent:
                self.msg = "already as deep as the task above"
                return False
        elif cur.indent == 0:
            return False
        end = block_end(lines, i)
        for ln in lines[i:end]:
            ln.indent = max(0, ln.indent + delta)
        self.dirty = True
        return True

    def collapse(self):
        tl = self.cur
        if tl.plain:
            self.msg = "plain text list: no folding"
            return
        lines = tl.lines
        i = tl.cursor
        if has_children(lines, i) and not lines[i].collapsed:
            lines[i].collapsed = True
            self.dirty = True
            return
        p = parent_of(lines, i)
        if p is None:
            if has_children(lines, i):
                self.msg = "already collapsed"
            else:
                self.msg = "no subtasks to collapse"
            return
        lines[p].collapsed = True
        tl.cursor = p
        tl.col = min(tl.col, len(lines[p].text))
        self.dirty = True

    def expand(self):
        tl = self.cur
        if tl.plain:
            self.msg = "plain text list: no folding"
            return
        lines = tl.lines
        i = tl.cursor
        if lines[i].collapsed:
            lines[i].collapsed = False
            self.dirty = True
        elif has_children(lines, i):
            tl.cursor = i + 1
            tl.col = min(tl.col, len(lines[i + 1].text))
        else:
            self.msg = "no subtasks to expand"

    def move_task(self, delta):
        tl = self.cur
        lines = tl.lines
        i = tl.cursor
        lvl = lines[i].indent
        end = block_end(lines, i)
        if delta < 0:
            prev = None
            for j in range(i - 1, -1, -1):
                if lines[j].indent < lvl:
                    break
                if lines[j].indent == lvl:
                    prev = j
                    break
            if prev is None:
                self.msg = "already first among its siblings"
                return
            block, before = lines[i:end], lines[prev:i]
            lines[prev:end] = block + before
            tl.cursor = prev
        else:
            if end >= len(lines) or lines[end].indent < lvl:
                self.msg = "already last among its siblings"
                return
            nend = block_end(lines, end)
            block, after = lines[i:end], lines[end:nend]
            lines[i:nend] = after + block
            tl.cursor = i + len(after)
        self.dirty = True

    def cut(self):
        tl = self.cur
        lines = tl.lines
        i = tl.cursor
        # a task is cut with its subtasks, folded or not, exactly as moving
        # and indenting carry them - a cut never leaves subtasks orphaned
        end = block_end(lines, i)
        chunk = [l.copy() for l in lines[i:end]]
        if self.last_key == "C-k":
            self.clip.extend(chunk)
        else:
            self.clip = chunk
        del lines[i:end]
        if not lines:
            lines.append(Line())
        tl.cursor = min(i, len(lines) - 1)
        tl.cursor = nearest_visible(lines, tl.cursor)
        tl.col = 0
        tl.normalise()
        self.dirty = True
        self.msg = "cut %d line(s) - Ctrl+U pastes" % len(chunk)

    def paste(self):
        if not self.clip:
            self.msg = "clipboard is empty"
            return
        tl = self.cur
        lines = tl.lines
        i = tl.cursor
        cur = lines[i]
        # the clipboard keeps the indent it was cut at, so a subtask stays a
        # subtask: dropped on a task at the same depth it becomes its sibling,
        # dropped on a shallower one it becomes that task's subtask
        if cur.text == "" and not has_children(lines, i):
            at = i                           # a blank line is a slot to fill
            del lines[i]
        else:
            at = block_end(lines, i)         # after the task and its subtasks
        chunk = [l.copy() for l in self.clip]
        lines[at:at] = chunk
        reveal(lines, at)                    # never paste out of sight
        tl.cursor = at
        tl.col = len(lines[at].text)
        tl.normalise()                       # clamp if it would skip a level
        self.dirty = True
        self.msg = "pasted %d line(s)" % len(chunk)

    # -- main loop --------------------------------------------------------
    def do_save(self):
        if not self.path:
            return self.write_out()      # an unnamed buffer needs a name first
        try:
            save(self.path, self.lists)
        except OSError as exc:
            self.msg = "save failed: %s" % exc
            return False
        self.dirty = False
        self.msg = "saved %s" % self.path
        return True

    def find_next(self, needle, after):
        """First match at or after ``after``, wrapping. (line, col, wrapped)."""
        lines = self.cur.lines
        needle = needle.lower()
        start_line, start_col = after
        n = len(lines)
        for step in range(n + 1):
            i = (start_line + step) % n
            at = lines[i].text.lower().find(needle, start_col if not step
                                            else 0)
            if at >= 0:
                wrapped = step and (i < start_line or i == start_line)
                return i, at, bool(wrapped)
        return None

    def search(self):
        """Ctrl+F: find text in the open list, remembering the term."""
        if self.cur is None:
            self.msg = "nothing to search"
            return
        term = self.prompt("Search: ", self.needle, strip=False)
        if term is None:
            self.msg = "cancelled"
            return
        if not term:
            term = self.needle           # bare Enter repeats the last search
        if not term:
            self.msg = "nothing to search for"
            return
        self.needle = term
        tl = self.cur
        hit = self.find_next(term, (tl.cursor, tl.col + 1))
        if hit is None:
            self.msg = "not found: %s" % term
            return
        line, col, wrapped = hit
        tl.cursor, tl.col = line, col
        reveal(tl.lines, line)           # a match inside a fold opens it
        self.focus = "right"
        self.goal_x = None
        self.msg = "found %r%s - Ctrl+F again for the next" % (
            term, " (wrapped)" if wrapped else "")

    def write_out(self):
        """Nano's ^O: save under a name you can edit, then keep that name."""
        new = self.prompt("File name to write: ", self.path or "")
        if not new:
            self.msg = "cancelled"
            return False
        new = os.path.expanduser(new)
        if (os.path.exists(new) and
                (not self.path
                 or os.path.abspath(new) != os.path.abspath(self.path))):
            tail = " exists. Overwrite? (y/N) "
            if not self.confirm("%s exists. Overwrite?"
                                % self.short_path(new, self.w - len(tail) - 3)):
                self.msg = "cancelled"
                return False
        was = self.path
        self.path = new
        if not self.do_save():
            self.path = was              # keep editing the file that worked
            return False
        return True

    def quit(self):
        if self.dirty:
            ans = self.ask_key(
                "Save before quitting?   y = save    n = discard    "
                "Esc = carry on", "yn")
            if ans is None:
                return
            if ans == "y" and not self.do_save():
                return
        self.running = False

    def run(self):
        while self.running:
            self.draw()
            k = self.reader.get()
            if k in (None, "UNKNOWN"):
                continue
            if k == "RESIZE":
                self.top = 0
                continue
            self.msg = ""
            if k in ("TAB", "BTAB"):
                if not self.show_left:
                    self.toggle_left()       # nowhere to go: bring it back
                    self.focus = "left"
                elif self.focus == "right":
                    self.focus = "left"
                elif self.lists:
                    self.focus = "right"
            elif k in ("C-g", "F1"):
                self.show_help()
            elif k == "C-s":
                self.do_save()
            elif k == "C-o":
                self.write_out()
            elif k == "C-t":
                self.cycle_theme()
            elif k == "C-f":
                self.search()
            elif k == "C-h":
                self.toggle_left()
            elif k == "C-y":
                self.copy_node()
            elif k in ("C-x", "F10", "C-c"):
                self.quit()
            elif self.focus == "left":
                self.key_left_panel(k)
            else:
                self.key_editor(k)
            self.last_key = k


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------

def start(stdscr, path, lists, existed, theme):
    _init_named()
    curses.raw()                 # so Ctrl+S reaches us, not the tty
    curses.noecho()
    stdscr.keypad(True)
    if curses.has_colors():
        curses.start_color()
    App(stdscr, path, lists, existed, theme).run()


def main(argv=None):
    locale.setlocale(locale.LC_ALL, "")
    ap = argparse.ArgumentParser(description="two-panel curses task outliner")
    ap.add_argument("file", nargs="?",
                    help="task file; with none, start an unnamed empty "
                         "buffer and name it on the first save")
    ap.add_argument("--export", action="store_true",
                    help="print the tasks as indented text and exit")
    ap.add_argument("--theme", choices=THEME_NAMES, default=THEME_NAMES[0],
                    help="colour scheme (default: %s); Ctrl+T cycles them. "
                         "%s" % (THEME_NAMES[0], "; ".join(
                             "%s = %s" % (n, t.desc)
                             for n, t in THEMES.items())))
    args = ap.parse_args(argv)

    os.environ.setdefault("ESCDELAY", "25")
    if args.file is None:
        if args.export:
            print("--export needs a file to export", file=sys.stderr)
            return 2
        lists, existed = blank_store(), False
    else:
        try:
            lists, existed = load(args.file)
        except (OSError, ValueError) as exc:
            print("cannot read %s: %s" % (args.file, exc), file=sys.stderr)
            return 1
    if args.export:
        sys.stdout.write(as_text(lists))
        return 0
    curses.wrapper(start, args.file, lists, existed, args.theme)
    return 0


if __name__ == "__main__":
    sys.exit(main())
