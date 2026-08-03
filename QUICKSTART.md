# jotboy quickstart

A ten-minute tour, for Linux. `README.md` is the full reference; this is just
enough to be productive.

jotboy keeps **task lists** in the left panel and one list at a time in the
right panel, which is a text editor. Lines nest by indentation, so a task can
have subtasks that fold away.

## Requirements

Python 3 and a terminal. Nothing to install — jotboy uses only the standard
library, and `curses` is part of it on Linux.

```sh
python3 --version
```

Developed and tested on Python 3.13. It uses `dataclasses`, so 3.7 is the
theoretical floor, but nothing older than 3.13 has actually been tried.

`curses` ships inside the Python your distribution installs — on Debian and
Ubuntu it lives in `libpython3.x-stdlib`, so there is nothing to add. If
`import curses` fails, you are almost certainly on a Python you compiled
yourself without the ncurses headers present; install `libncurses-dev` and
rebuild it, or just use the distribution's `python3`.

## Run it

```sh
python3 jotboy.py                 # a new, empty, unnamed list
python3 jotboy.py mytasks.json    # open a file, or start one under that name
```

To run it as a command, mark it executable and put it on your `PATH`:

```sh
chmod +x jotboy.py
mkdir -p ~/.local/bin
ln -s "$PWD/jotboy.py" ~/.local/bin/jotboy
jotboy mytasks.json
```

jotboy has no default file and reads nothing you did not name. Start it with
no argument and you get an empty buffer; the first `Ctrl+S` asks where to put
it.

## Your first list

Start it, and type. You are already in the editor.

```
Plan the week ⏎
```

Now for subtasks, **type two spaces at the start of the line**. That is the
indent — there is no Tab key to press:

```
  book dentist ⏎
buy stamps ⏎
call plumber
```

You only type the two spaces **once**, when you first go a level deeper.
`Enter` keeps whatever indent you are on, so the next two lines need no
spaces. You get:

```
▾ Plan the week
    book dentist
    buy stamps
    call plumber
```

To come back out a level for the next top-level task, press `Enter` then
`Ctrl+←`:

```
▾ Plan the week
    book dentist
    buy stamps
    call plumber
  Fix the shed
```

`Ctrl+→` goes the other way if you indent too little. `Backspace` at the very
start of a line does the same as `Ctrl+←`, and once you are already at the
left edge it joins the line onto the one above.

## Save it

`Ctrl+S`. An unnamed buffer asks for a filename first; after that `Ctrl+S`
just writes. `Ctrl+O` saves under a different name, nano-style, and carries on
editing the new one.

`Ctrl+X` quits, asking `Save before quitting?` if you have unsaved changes —
one keystroke, `y`, `n` or `Esc`, no Enter needed.

## The things worth learning next

**Folding.** `Ctrl+↑` collapses the task the caret is in; it shows `▸` and a
count of what is hidden. `Ctrl+↓` opens it again. On a subtask, `Ctrl+↑` jumps
up to the parent and folds that, so you can fold your way out of a deep tree.

**Moving things.** `Alt+↑` and `Alt+↓` move the current task among its
siblings, and its subtasks always travel with it. A task can never jump out of
its parent.

**Cut and paste.** `Ctrl+K` cuts the task *and everything under it*; `Ctrl+U`
pastes it back at the indent it was cut at. That is how you move work between
lists: cut it, switch list, paste. Pressing `Ctrl+K` several times in a row
gathers the lines into one clipboard.

**Finding.** `Ctrl+F`, type, `Enter`. Press `Ctrl+F` `Enter` again to walk to
the next match — the term is remembered, and it opens a fold if the match is
hidden inside one.

**More than one list.** `Tab` moves to the left panel, then:

| | |
| --- | --- |
| `↑` `↓` | pick a list |
| `Enter` or `Tab` | go back to editing it |
| `Ctrl+N` | make a new list |
| `Ctrl+R` | rename it |
| `Ctrl+D` | delete it |
| `Alt+↑` `Alt+↓` | reorder the lists |

`Ctrl+N` asks what kind of list you want, one keystroke:

```
s = structured   p = plain text   Esc = cancel
```

**Structured** is everything above — indenting, folding, subtasks. **Plain
text** is just lines: no indenting, no folding, no bold. Use it for notes and
scratch text you do not want turned into a tree. Plain lists are marked `[p]`
in the left panel.

**Getting it out.** `Ctrl+Y` copies the whole open list to the system
clipboard as plain indented text, ready to paste into an email. On Linux that
needs one of `xclip`, `xsel` or `wl-copy` installed:

```sh
sudo apt install xclip          # X11
sudo apt install wl-clipboard   # Wayland
```

Without one, jotboy falls back to asking the terminal itself, which many
terminals refuse for security. There is also `--export`, which prints
everything as indented text:

```sh
jotboy mytasks.json --export > tasks.txt
```

## Make it yours

`Ctrl+T` cycles eight colour schemes; `--theme NAME` picks one at startup.

```sh
jotboy --theme amber mytasks.json
```

`green` `purple` `blue` `white` `amber` `amstrad` `c64` `mac`. jotboy paints
its own colours over whatever your terminal uses, so these look the same
everywhere.

`Ctrl+H` hides the list panel and gives the space to the editor. `Tab` brings
it back.

**`Ctrl+G` shows every key**, and it shows the keys for whichever panel you
are in. That is the one to remember.

## If something is wrong

**The screen says "terminal too small".** jotboy needs at least 8 rows and 40
columns. Resize the window.

**`F1` opens your terminal's help, not jotboy's.** xfce4-terminal takes `F1`
and `F10` for itself. Use `Ctrl+G` for help and `Ctrl+X` to quit — every
function key in jotboy is only ever an alias for a control key, so nothing is
out of reach.

**`Ctrl+H` deletes a character instead of hiding the panel.** Your terminal
sends `Ctrl+H` for Backspace, and jotboy gives Backspace priority. Use `Tab`
to hide and show the panel instead.

**`Alt+↑` / `Alt+↓` do nothing.** The Alt key has to send an Escape prefix
(the "Meta" setting). Most terminals do this by default; in `xterm` the
resource is `metaSendsEscape`. If your terminal cannot, `Ctrl+K` and `Ctrl+U`
will still move tasks around — cut and paste them instead.

**Colours look wrong or flat.** Check `echo $TERM` says `xterm-256color`. With
only eight colours each theme still works, just with fewer shades.

**`Ctrl+Y` says "no clipboard".** Install `xclip` or `wl-clipboard`, above.

**It will not save.** The message line says why — normally a permissions
problem, since jotboy creates missing directories for you. `Ctrl+O` lets you
type a different path; `Ctrl+U` clears the line first.

## Building a single binary

Optional, and needs `pyinstaller`:

```sh
pyinstaller jotboy.spec
./dist/jotboy mytasks.json
```

If you distribute that binary, the GPL asks you to make the source available
too — see `COPYRIGHT.md`.
