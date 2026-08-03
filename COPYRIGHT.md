# Copyright and licence

jotboy — a two-panel terminal task outliner and text editor

Copyright (C) 2026 &lt;YOUR NAME&gt;

> **Fill in your name.** Every file below says `<YOUR NAME>`; replace it in
> `COPYRIGHT.md`, `jotboy.py` and `test_jotboy.py`. The FSF also suggests
> adding a contact address, electronic and paper.

## Licence

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

SPDX identifier: `GPL-3.0-or-later`

## Where the licence lives

| File | What it is |
| --- | --- |
| `COPYING` | the complete GNU GPL version 3, verbatim, as published by the FSF |
| `COPYRIGHT.md` | this file: who holds the copyright, and the notice |
| `jotboy.py`, `test_jotboy.py` | each carries the same notice as a header comment |

`COPYING` was taken unmodified from <https://www.gnu.org/licenses/gpl-3.0.txt>.
Do not edit it — the GPL is only the GPL if its text is verbatim. Its
SHA-256 is:

```
3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986
```

## Which version, and why "or later"

This is **GPL-3.0-or-later**: version 3 of the licence, or any later version,
at the recipient's option. That is the FSF's own recommendation, and it is
what the standard notice above says. If you would rather pin it to version 3
exactly, delete the words *"or (at your option) any later version"* from
`COPYRIGHT.md` and from both source headers, and change the SPDX identifier
to `GPL-3.0-only`.

If you wanted GPL **2** instead, this is the wrong text throughout — say so
and it can be redone; the notice, the SPDX tag and `COPYING` all differ.

## What this means in practice

* Anyone may use, study, modify and share jotboy, including commercially.
* Anyone distributing it — modified or not, as source or as a binary — must
  pass on these same freedoms, provide the corresponding source, and keep the
  copyright and licence notices intact.
* Distributing a **binary** (for instance the PyInstaller build from
  `jotboy.spec`) means shipping or offering the source that produced it,
  under the GPL, along with a copy of `COPYING`.
* There is no warranty. That is not boilerplate you can quietly drop: the
  disclaimer is part of the licence grant.

Contributions you accept from other people are theirs, not yours. Either keep
a list of contributors, or ask contributors to agree their work is under the
same licence — the usual, lightweight practice is a `Signed-off-by:` line in
commit messages ("Developer Certificate of Origin").

## Third-party code

jotboy itself has **no runtime dependencies** beyond the Python standard
library, so nothing else is redistributed with it and nothing else imposes
terms on it. Python and ncurses are used through their public interfaces at
run time only, and neither is bundled here.

The test suite alone uses `pytest` 9.1.1 (MIT) and `pyte` 0.8.2 (LGPL-3.0).
Both are development tools living in the venv — not shipped with jotboy and
not linked into it — so they place no obligation on a jotboy release. If you
ever bundle `pyte` into a distributed artefact, re-check its LGPL terms
first.
