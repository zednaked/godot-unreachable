# godot-unreachable

**Find the files in a Godot 4 project that your game can never load.**

Most unused-file finders ask *"does anything reference this file?"* That is the
wrong question. An old menu scene and its script reference each other, so a
reference count calls both of them alive, while nothing in the game ever opens
the menu. This tool asks a different question: *is there a chain of references
from something the engine actually loads (the main scene, the autoloads,
`project.godot`, GDExtensions) down to this file?* If there isn't, the file is
dead, and its friends are dead with it.

I wrote it after the same thing happened three times in a row: a UI bug that
wouldn't close, and the cause was never the code being debugged. It was a
whole screen that no script ever shows, still sitting in the project, still
being edited. A text search for the file name found it "in use" every time.

One Python file, standard library only, no Godot needed. It reads a 780-file
project in 0.16 s, so it fits in a pre-commit hook or a CI gate.

```
python3 godot_unreachable.py path/to/your/project
```

On the [example project](example/) in this repo:

```
scanned 29 files from 1 roots

== unreachable: 5 files, 59.0 KB ==
  res://art/unused_background.png  39.1 KB
  res://scenes/old_menu.tscn  192 B  (cited by 1, still unreachable)
  res://scripts/old_menu.gd  185 B  (cited by 1, still unreachable)
  res://spine/hero_v1.webp  19.5 KB
  res://ui/Left on.webp  3 B

== islands: dead files that only reference each other ==
  2 files: scenes/old_menu.tscn, scripts/old_menu.gd

== kept: paths built at runtime ==
  res://sfx/*  built in scripts/main.gd

== addons nothing in the game reaches ==
  res://Addons/fancy_lines/ (editor plugin enabled)

== benches, unreachable on purpose: 3 ==
  res://tests/fixtures/bag.tres
  res://tests/inventory_probe.gd
  res://tests/test_inventory.tscn

== reached only from benches ==
  res://scripts/debug_hitboxes.gd
```

## What it found in production projects

I ran it on eight production Godot 4 web projects (3,325 files in all), every
one of them already cleaned up by hand once:

| | |
|---|---|
| unreachable files | **190**, 11.1 MiB |
| of those, cited by another file | **8**: a reference count calls these alive |
| islands (dead files that only reference each other) | **3**, 11 files |
| addons nothing in the game reaches | **3** |
| per project | 0 to 154; the 154 is one project with a lot of art that was never wired in: character parts, mockups, work in progress |

**The check that matters.** On one of them I deleted every file it reported (13,
two islands among them) in a scratch copy, then compared the copy against the
original: cold reimport with zero errors, the three headless test suites at
`FAILURES: 0`, and the same errors in the runtime log before and after.

**The first version failed that check.** It reported a font as dead, the cold
import broke on it, and the cause was the case below about fallbacks: they live
in the `.import` file, which I wasn't reading. That's why the check exists.
Every rule in this tool is in there because something real broke without it.

## The traps, and what the tool does about each

These are the ones any home-made scan runs into. Each has a file in
[`example/`](example/) and a test that fails without the rule.

**Islands.** Two files that only cite each other. Reachability from the real
roots kills both; a reference count keeps both.

**Godot 4 references by uid.** Scenes increasingly point at each other with
`uid://…` and no path at all. The tool reads uids from all three places Godot
writes them: the `.uid` sidecar (scripts, shaders), the `.import` sidecar
(assets), and the header of `.tscn`/`.tres`.

**`class_name` is a reference.** A script with `class_name Inventory` is used
as `Inventory.new()`, never by its path. Without this edge every shared class
reads as dead.

**The font fallback lives in the `.import`.** A font's fallback fonts are import
settings, so they sit in `body.ttf.import`, not in any scene. Same for anything
else an importer points at.

**Translations are reached through what they generate.** `project.godot` lists
`text.en.translation`, never `text.csv`. The CSV is alive because its outputs are.

**Paths built at runtime.** `load("res://sfx/%s.ogg" % name)`,
`"res://ui/" + id`, `const VOICE = "res://voice"`: the file name is joined at
runtime, so nothing names the file. The tool keeps the whole folder and tells
you which script builds it, so you know where the guess came from.

**Relative references.** Spine `.atlas` files name their pages without a path,
BMFont `.fnt` files do the same, and shader `#include` can be relative.

**`Addons` with a capital A.** Common, and comparing the folder name raw lets a
whole library into the scan as if it were game code. In the scan this tool grew
out of, that alone was 35 false positives drowning 11 real ones. Addons are
reported per folder instead: *does anything in the game reach into it?* An
editor plugin that is enabled but never reached at runtime is flagged as such.

**Paths with spaces.** `"res://ui/Right on.webp"` is a real file name in real
projects.

**Benches are not garbage.** Test scenes, tools and previews are unreachable on
purpose. They're listed apart, so the real list isn't read as noise. A file that
*only* a test uses gets its own list too: it's not dead, but it doesn't ship.

## What it can't see

- **A path that never appears as a string literal.** A scene name read from a
  server config, or a JSON file of your own format. Add those with `--root`.
- **Files packed by your build script**, like sounds zipped for a CDN. Use
  `--keep`.
- **Unused code inside a live file.** This finds files, not functions.
- **Comments count.** A `res://` path inside a quoted string in a comment keeps
  the file alive. It errs toward alive, which is the safe side.

"Unreachable" is a lead, not a verdict. Delete on a branch, reimport cold, run
your tests.

## Options

```
--root PATH    extra root, glob ok, repeatable: res://levels/*.tscn
--keep GLOB    never report these: "assets/sfx/*"
--bench GLOB   replace the default bench patterns (test_*, tests/*, tools/*, *preview*)
--json         machine-readable output
--fail         exit 1 when anything is unreachable, for CI
```

As a CI gate:

```yaml
- run: python3 godot_unreachable.py . --fail
```

Tests: `python3 -m unittest -v`

## Why this matters on the web

If your export preset is set to *Export all resources*, every one of these files
is in the pack your players download. I wrote up what that costs, measured, in
[godot-web-build-budget](https://github.com/zednaked/godot-web-build-budget).

### If your project has the same problem

I do size audits and cleanups for Godot web builds, fixed scope and priced up
front: [work-with-me](https://github.com/zednaked/work-with-me).
**zednaked@gmail.com**

MIT licensed.
