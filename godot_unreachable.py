#!/usr/bin/env python3
"""Find the files in a Godot 4 project that the game can never reach.

Reachability, not reference counting: a file is alive only if there is a chain
of references to it from something the engine actually loads (the main scene,
the autoloads, what project.godot points at, GDExtensions). Two files that only
reference each other are an island, and they are dead together.

No Godot needed, no dependencies. Run it from anywhere:

    python3 godot_unreachable.py path/to/project
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from collections import deque
from dataclasses import dataclass, field

__version__ = "0.1.0"

# Folders that are never part of the game.
SKIP_DIRS = {".godot", ".git", ".import", "node_modules", "__pycache__"}

# Sidecars describe another file. They are never alive or dead on their own.
SIDECAR_EXT = (".import", ".uid")

# Files read as text to find references.
TEXT_EXT = {
    ".tscn", ".tres", ".gd", ".cs", ".gdshader", ".gdshaderinc", ".godot",
    ".cfg", ".json", ".atlas", ".fnt", ".gdextension", ".txt", ".csv", ".md",
}

# Files the engine loads by its own rules, without anything pointing at them.
IMPLICIT_ROOTS = ("res://default_bus_layout.tres",)

# Unreachable on purpose: test scenes, tools, previews. Listed apart so the real
# list is not read as noise and ignored.
DEFAULT_BENCH = ["test_*", "*_test.*", "tests/*", "test/*", "tools/*", "*preview*"]

# Quoted, so paths with spaces survive ("res://ui/Right on.webp"). An unquoted
# res:// is a comment or prose, not something the engine loads.
# Autoloads are written "*res://x.gd": the star means "make it a singleton".
RES_RE = re.compile(r"""(?<=["'])\*?(res://[^"'\n]*)(?=["'])""")
UID_RE = re.compile(r"uid://[a-z0-9]+")
CLASS_RE = re.compile(r"^\s*class_name\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
CS_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*Godot\.")
HEADER_UID_RE = re.compile(r'^\[gd_(?:scene|resource)[^\]]*\buid="(uid://[a-z0-9]+)"')
IMPORT_UID_RE = re.compile(r'^uid="(uid://[a-z0-9]+)"', re.M)
ATLAS_PAGE_RE = re.compile(r"^([^\s:]+\.(?:png|webp|jpg|jpeg|ktx2?))\s*$", re.M | re.I)
FNT_PAGE_RE = re.compile(r'\bfile="([^"]+)"')
INCLUDE_RE = re.compile(r'^\s*#include\s+"([^"]+)"', re.M)
EDITOR_PLUGINS_RE = re.compile(r"^\[editor_plugins\][^\[]*", re.M)
IMPORT_SELF_RE = re.compile(r"^(?:uid|path(?:\.\w+)?|source_file|dest_files|files)=.*$", re.M)
DEST_FILES_RE = re.compile(r"^dest_files=\[([^\]]*)\]", re.M)


@dataclass
class Project:
    root: str
    files: list[str] = field(default_factory=list)          # res:// paths
    sizes: dict[str, int] = field(default_factory=dict)
    uid_to_path: dict[str, str] = field(default_factory=dict)
    class_of: dict[str, str] = field(default_factory=dict)  # path -> class name
    edges: dict[str, set[str]] = field(default_factory=dict)
    dynamic: dict[str, set[str]] = field(default_factory=dict)  # prefix -> sources
    roots: set[str] = field(default_factory=set)
    editor_plugins: list[str] = field(default_factory=list)
    generated_from: dict[str, str] = field(default_factory=dict)  # output -> source

    def disk(self, res: str) -> str:
        return os.path.join(self.root, res[len("res://"):])


def to_res(root: str, path: str) -> str:
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    return "res://" + rel


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def collect(root: str) -> Project:
    p = Project(root=os.path.abspath(root))
    for dirpath, dirnames, filenames in os.walk(p.root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if name.endswith(SIDECAR_EXT):
                continue
            res = to_res(p.root, full)
            p.files.append(res)
            try:
                p.sizes[res] = os.path.getsize(full)
            except OSError:
                p.sizes[res] = 0
    return p


def map_uids(p: Project) -> None:
    """Godot 4 writes uids in three places: the .uid sidecar of scripts and
    shaders, the .import sidecar of assets, and the header of scenes and
    resources. Scenes increasingly point at each other only by uid."""
    for res in p.files:
        disk = p.disk(res)
        for sidecar in (disk + ".uid", disk + ".import"):
            if os.path.exists(sidecar):
                text = read_text(sidecar)
                m = UID_RE.search(text) if sidecar.endswith(".uid") else IMPORT_UID_RE.search(text)
                if m:
                    p.uid_to_path[m.group(1) if m.groups() else m.group(0)] = res
                if sidecar.endswith(".import"):
                    # A CSV of translations is reached through the .translation
                    # files it generates, never by its own path.
                    d = DEST_FILES_RE.search(text)
                    for dest in RES_RE.findall(d.group(1)) if d else ():
                        if not dest.startswith("res://.godot/"):
                            p.generated_from[dest] = res
        if res.endswith((".tscn", ".tres")):
            with open(disk, encoding="utf-8", errors="replace") as f:
                m = HEADER_UID_RE.match(f.readline())
            if m:
                p.uid_to_path[m.group(1)] = res


def map_classes(p: Project) -> None:
    """A script with class_name is used by its identifier, never by its path.
    Without this edge, every shared class reads as dead."""
    for res in p.files:
        if res.endswith(".gd"):
            m = CLASS_RE.search(read_text(p.disk(res)))
            if m:
                p.class_of[res] = m.group(1)
        elif res.endswith(".cs"):
            m = CS_CLASS_RE.search(read_text(p.disk(res)))
            if m:
                p.class_of[res] = m.group(1)


def dynamic_prefix(literal: str, after: str) -> str | None:
    """'res://sfx/%s.ogg', 'res://ui/' + name, 'res://lines/{0}.png': the path
    is built at runtime. Return the folder it is built in."""
    cut = min((i for i in (literal.find("%"), literal.find("{")) if i >= 0), default=-1)
    if cut >= 0:
        literal = literal[:cut]
    elif not (literal.endswith("/") or after.lstrip().startswith("+")):
        return None
    if "/" not in literal[len("res://"):] and not literal.endswith("/"):
        return "res://"
    return literal[: literal.rfind("/") + 1]


def resolve(p: Project, ref: str, fileset: set[str]) -> str | None:
    ref = ref.rstrip(".:")
    if ref in fileset:
        return ref
    if ref.startswith("uid://"):
        return p.uid_to_path.get(ref)
    return None


def scan_edges(p: Project) -> None:
    fileset = set(p.files)
    classes = {name: path for path, name in p.class_of.items()}
    class_re = re.compile(r"\b(" + "|".join(map(re.escape, classes)) + r")\b") if classes else None

    for res in p.files:
        ext = os.path.splitext(res)[1].lower()
        # Import settings reference files too: a font's fallbacks live in its
        # .import, not in the font. Its own bookkeeping lines are not references.
        imp = read_text(p.disk(res) + ".import") if os.path.exists(p.disk(res) + ".import") else ""
        imp = IMPORT_SELF_RE.sub("", imp)
        if ext not in TEXT_EXT and not imp:
            continue
        text = (read_text(p.disk(res)) if ext in TEXT_EXT else "") + "\n" + imp
        if res == "res://project.godot":
            m = EDITOR_PLUGINS_RE.search(text)
            if m:
                p.editor_plugins = RES_RE.findall(m.group(0))
                text = text[: m.start()] + text[m.end():]
        out: set[str] = set()

        for m in RES_RE.finditer(text):
            literal = m.group(1)
            prefix = dynamic_prefix(literal, text[m.end() + 1: m.end() + 4])
            if prefix is not None:
                p.dynamic.setdefault(prefix, set()).add(res)
                continue
            hit = resolve(p, literal, fileset)
            if hit:
                out.add(hit)
            elif os.path.isdir(p.disk(literal.rstrip("/"))):
                folder = literal.rstrip("/") + "/"
                if ext == ".gdextension":
                    # A macOS .framework is a folder the engine loads whole.
                    out.update(f for f in fileset if f.startswith(folder))
                else:
                    # const ROOT = "res://voice" and the file name joined later.
                    p.dynamic.setdefault(folder, set()).add(res)
        for m in UID_RE.finditer(text):
            hit = resolve(p, m.group(0), fileset)
            if hit:
                out.add(hit)
        if class_re and ext in (".gd", ".cs", ".tscn", ".tres"):
            for m in class_re.finditer(text):
                out.add(classes[m.group(1)])

        # Relative references: Spine atlas pages, BMFont pages, shader includes.
        here = res[: res.rfind("/") + 1]
        rel = []
        if ext == ".atlas":
            rel = ATLAS_PAGE_RE.findall(text)
        elif ext == ".fnt":
            rel = FNT_PAGE_RE.findall(text)
        elif ext in (".gdshader", ".gdshaderinc"):
            rel = [i for i in INCLUDE_RE.findall(text) if not i.startswith("res://")]
        for r in rel:
            hit = os.path.normpath(here[len("res://"):] + r).replace(os.sep, "/")
            hit = "res://" + hit.lstrip("./")
            if hit in fileset:
                out.add(hit)

        out.discard(res)
        p.edges[res] = out

    for dest, src in p.generated_from.items():
        p.edges.setdefault(dest, set()).add(src)


def find_roots(p: Project) -> None:
    fileset = set(p.files)
    if "res://project.godot" in fileset:
        p.roots.add("res://project.godot")
    for r in IMPLICIT_ROOTS:
        if r in fileset:
            p.roots.add(r)
    for res in p.files:
        if res.endswith(".gdextension"):
            p.roots.add(res)


def reach(p: Project, start: set[str]) -> set[str]:
    seen = set(start)
    queue = deque(start)
    dyn = sorted(p.dynamic)
    while queue:
        cur = queue.popleft()
        nxt = set(p.edges.get(cur, ()))
        for prefix in dyn:
            if cur in p.dynamic[prefix]:
                nxt.update(f for f in p.files if f.startswith(prefix) and prefix != "res://")
        for n in nxt:
            if n not in seen:
                seen.add(n)
                queue.append(n)
    return seen


def addon_dirs(p: Project) -> dict[str, list[str]]:
    """Addons, matched without case: 'Addons/' is common and comparing it raw
    lets a whole library into the scan as if it were game code."""
    out: dict[str, list[str]] = {}
    for res in p.files:
        parts = res[len("res://"):].split("/")
        if len(parts) >= 3 and parts[0].lower() == "addons":
            out.setdefault("res://" + parts[0] + "/" + parts[1] + "/", []).append(res)
    return out


def islands(p: Project, dead: list[str]) -> list[list[str]]:
    """Dead files that reference each other. A reference count calls every one
    of these alive; reachability is what kills them."""
    dset = set(dead)
    adj: dict[str, set[str]] = {d: set() for d in dead}
    for d in dead:
        for n in p.edges.get(d, ()):
            if n in dset:
                adj[d].add(n)
                adj[n].add(d)
    seen: set[str] = set()
    groups = []
    for d in dead:
        if d in seen or not adj[d]:
            continue
        comp, q = [], deque([d])
        seen.add(d)
        while q:
            c = q.popleft()
            comp.append(c)
            for n in adj[c]:
                if n not in seen:
                    seen.add(n)
                    q.append(n)
        groups.append(sorted(comp))
    return sorted(groups, key=len, reverse=True)


def matches(res: str, patterns: list[str]) -> bool:
    rel = res[len("res://"):]
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel.lower(), pat.lower()) or fnmatch.fnmatch(name.lower(), pat.lower())
               for pat in patterns)


def analyse(root: str, extra_roots: list[str], bench: list[str], keep: list[str]) -> dict:
    p = collect(root)
    if "res://project.godot" not in p.files:
        raise SystemExit(f"no project.godot in {root}")
    map_uids(p)
    map_classes(p)
    scan_edges(p)
    find_roots(p)
    fileset = set(p.files)
    for r in extra_roots:
        r = r if r.startswith("res://") else "res://" + r.lstrip("/")
        p.roots.update(f for f in p.files if f == r or fnmatch.fnmatch(f, r))

    alive = reach(p, p.roots)
    addons = addon_dirs(p)
    in_addon = {f for files in addons.values() for f in files}

    unreached = [f for f in p.files if f not in alive and f not in in_addon and not matches(f, keep)]
    benches = [f for f in unreached if matches(f, bench)]
    bench_reach = reach(p, set(benches)) - alive
    dead = [f for f in unreached if f not in bench_reach]
    bench_only = sorted(f for f in bench_reach if f not in benches and f not in in_addon)

    cited_by: dict[str, int] = {}
    for src, outs in p.edges.items():
        for o in outs:
            cited_by[o] = cited_by.get(o, 0) + 1

    unused_addons = sorted(a for a, files in addons.items() if not any(f in alive for f in files))
    dynamic = {pre: sorted(srcs) for pre, srcs in sorted(p.dynamic.items())
               if any(s in alive for s in srcs) and pre != "res://"}

    return {
        "version": __version__,
        "project": p.root,
        "scanned": len(p.files),
        "roots": sorted(p.roots),
        "dead": [{"path": f, "bytes": p.sizes[f], "cited_by": cited_by.get(f, 0)} for f in dead],
        "dead_bytes": sum(p.sizes[f] for f in dead),
        "islands": islands(p, dead),
        "bench": sorted(benches),
        "bench_only": bench_only,
        "unused_addons": unused_addons,
        "editor_plugins": p.editor_plugins,
        "dynamic": dynamic,
        "_fileset": fileset,
    }


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def report(r: dict) -> str:
    L = []
    L.append(f"scanned {r['scanned']} files from {len(r['roots'])} roots")
    L.append("")
    L.append(f"== unreachable: {len(r['dead'])} files, {human(r['dead_bytes'])} ==")
    for d in r["dead"]:
        note = f"  (cited by {d['cited_by']}, still unreachable)" if d["cited_by"] else ""
        L.append(f"  {d['path']}  {human(d['bytes'])}{note}")
    if not r["dead"]:
        L.append("  (none)")
    if r["islands"]:
        L.append("")
        L.append("== islands: dead files that only reference each other ==")
        for g in r["islands"]:
            L.append(f"  {len(g)} files: " + ", ".join(x[len('res://'):] for x in g))
    L.append("")
    L.append("== kept: paths built at runtime ==")
    for pre, srcs in r["dynamic"].items():
        L.append(f"  {pre}*  built in {', '.join(s[len('res://'):] for s in srcs)}")
    if not r["dynamic"]:
        L.append("  (none)")
    if r["unused_addons"]:
        L.append("")
        L.append("== addons nothing in the game reaches ==")
        for a in r["unused_addons"]:
            on = " (editor plugin enabled)" if any(e.startswith(a) for e in r["editor_plugins"]) else ""
            L.append(f"  {a}{on}")
    L.append("")
    L.append(f"== benches, unreachable on purpose: {len(r['bench'])} ==")
    for b in r["bench"]:
        L.append(f"  {b}")
    if r["bench_only"]:
        L.append("")
        L.append("== reached only from benches ==")
        for b in r["bench_only"]:
            L.append(f"  {b}")
    L.append("")
    L.append("Unreachable is a lead, not a verdict: delete on a branch, reimport cold, run the game.")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="godot-unreachable", description=__doc__.split("\n\n")[0])
    ap.add_argument("project", nargs="?", default=".", help="folder with project.godot")
    ap.add_argument("--root", action="append", default=[], metavar="PATH",
                    help="extra root, e.g. a scene loaded from a server config (glob ok, repeatable)")
    ap.add_argument("--bench", action="append", default=None, metavar="GLOB",
                    help=f"unreachable on purpose (default: {' '.join(DEFAULT_BENCH)})")
    ap.add_argument("--keep", action="append", default=[], metavar="GLOB",
                    help="never report these (README, LICENSE, CI files...)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fail", action="store_true", help="exit 1 when anything is unreachable (CI gate)")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    keep = a.keep + ["*.md", "LICENSE*", ".gitignore", ".gitattributes", "export_presets.cfg",
                     ".editorconfig", "*.yml", "*.yaml"]
    r = analyse(a.project, a.root, a.bench if a.bench is not None else DEFAULT_BENCH, keep)
    r.pop("_fileset")
    print(json.dumps(r, indent=2) if a.json else report(r))
    return 1 if a.fail and r["dead"] else 0


if __name__ == "__main__":
    sys.exit(main())
