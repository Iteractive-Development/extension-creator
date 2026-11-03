#!/usr/bin/env python3
"""Package one or more skills/plugin repos as an updatable Enconvo folder
marketplace.

Subcommands:
  new    Clone (or reuse) repo(s) and scaffold an adapter folder with a
         generated marketplace, build-config.json and README.md. Repeat --repo
         to combine several upstream repos into one adapter and marketplace.
  build  Rebuild the marketplace from an adapter folder's build-config.json.
  sync   Pull each upstream clone (--ff-only) and rebuild. --apply also runs
         the Enconvo plugin refresh/update and the agents step.
  agents Create or refresh the Enconvo Agents of an adapter (build-config
         "agents") and link each Agent's skills from the installed plugin.
         build-config.json is the single input file an adapter carries.

The series shape is ONE plugin (--single): every skill at skills/<name> when
build-config says "flatten". Agent split: build-config "groups" (explicit
skill lists), else the upstream Claude Code agents' own `skills:` lists, else
the upstream skill folder nesting. Claude Code agents (agents/*.md) ship inside
the plugin, where Enconvo loads them natively as helpers. With
"agents": {"enabled": true} each split also becomes a real Enconvo Agent that
sees only its own skills (skills it carries are marked `visibility: scoped`
and linked into the Agent's folder). Upstream files are copied byte-identical
except relinked paths and the scoped marker; every Enconvo-specific file is
generated. A repo without plugin.json gets a synthesized manifest (README
description, LICENSE detection, git tag version, remote owner as author).

Every build stages into .marketplace-new and swaps into place only on
success, so a failed build leaves the marketplace a running Enconvo points
at untouched.

See the skill's SKILL.md for the full operating flow.
"""

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents_support as ag  # noqa: E402

# Markdown inline links and images: [text](target) or ![alt](target);
# targets may be wrapped in <...> when they contain spaces, and may be
# percent-encoded
LINK_RE = re.compile(r"\]\((?:<([^)#>]+)>|([^)#\s]+))(?:#[^)]*)?\)")

DEFAULT_OWNER = {"name": "Iteractive Development", "url": "https://iteractive.dev"}
DEFAULT_CATEGORY = "development"


def run(args, cwd=None, check=True):
    out = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and out.returncode != 0:
        sys.exit(f"command failed: {' '.join(args)}\n{out.stderr.strip()}")
    return out.stdout.strip()


def load_json(path, what):
    p = Path(path)
    try:
        text = p.read_text()
    except FileNotFoundError:
        sys.exit(f"{what} not found: {p}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        sys.exit(f"{what} is not valid JSON ({p}): {e}")


def synth_manifest(repo):
    """No plugin.json upstream: derive a minimal manifest so attribution,
    description and versioning still work."""
    desc = ""
    for name in ("README.md", "README.markdown", "readme.md", "Readme.md"):
        p = repo / name
        if p.is_file():
            desc = next(
                (
                    l.strip()
                    for l in p.read_text(errors="replace").splitlines()
                    if l.strip()
                    and not l.lstrip().startswith(("#", "[", "<", "|", "!"))
                ),
                "",
            )
            break
    license = "Unknown"
    for lic_name in ("LICENSE", "LICENSE.txt", "LICENCE", "COPYING"):
        p = repo / lic_name
        if p.is_file():
            head = p.read_text(errors="replace")[:400].lower()
            for marker, spdx in (
                ("apache license", "Apache-2.0"),
                ("gnu lesser general public license", "LGPL-3.0"),
                ("gnu general public license", "GPL-3.0"),
                ("mozilla public license", "MPL-2.0"),
                ("permission is hereby granted, free of charge", "MIT"),
                ("bsd", "BSD-3-Clause"),
            ):
                if marker in head:
                    license = spdx
                    break
            break
    version = run(["git", "describe", "--tags", "--abbrev=0"], cwd=repo, check=False) or "1.0.0"
    manifest = {"name": repo.name, "version": version, "description": desc, "license": license}
    remote = run(["git", "-C", str(repo), "remote", "get-url", "origin"], check=False)
    if remote:
        manifest["repository"] = remote
        parts = remote.rstrip("/").removesuffix(".git").split("/")
        if len(parts) >= 2 and parts[-2]:
            manifest["author"] = {"name": parts[-2]}
    print(
        f"note: no plugin.json in {repo.name}; synthesized manifest "
        f"(v{version}, {license}, author {manifest.get('author', {}).get('name', 'unknown')})"
    )
    return manifest


ICON_FONT = {
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E),
    "D": (0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "J": (0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "N": (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04),
    "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F),
    "3": (0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    "?": (0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04),
}
ICON_PALETTE = (
    (63, 81, 181), (21, 101, 192), (0, 121, 107), (81, 45, 168),
    (183, 28, 28), (69, 90, 100), (230, 81, 0), (26, 35, 126),
    (46, 125, 50), (121, 85, 72),
)


def write_placeholder_icon(path, label):
    """Deterministic placeholder PNG (rounded square + initial) when a repo
    ships no icon; swap it any time via plugin_icon in build-config.json."""
    import hashlib
    import struct
    import zlib
    size = 256
    ch = next((c for c in label.upper() if c.isalnum()), "?")
    glyph = ICON_FONT.get(ch, ICON_FONT["?"])
    bg = ICON_PALETTE[int(hashlib.sha256(label.encode()).hexdigest(), 16)
                      % len(ICON_PALETTE)]
    scale = 30
    gw, gh = 5 * scale, 7 * scale
    x0, y0 = (size - gw) // 2, (size - gh) // 2
    rad = size // 5

    def inside(x, y):
        nx = min(max(x, rad), size - rad - 1)
        ny = min(max(y, rad), size - rad - 1)
        return (x - nx) ** 2 + (y - ny) ** 2 <= rad * rad

    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            if not inside(x, y):
                raw += b"\x00\x00\x00\x00"
                continue
            on = (
                y0 <= y < y0 + gh and x0 <= x < x0 + gw
                and (glyph[(y - y0) // scale] >> (4 - (x - x0) // scale)) & 1
            )
            raw += b"\xff\xff\xff\xff" if on else bytes(bg) + b"\xff"

    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    )


def find_manifest(repo):
    for rel in (".claude-plugin/plugin.json", "plugin.json"):
        p = repo / rel
        if p.is_file():
            try:
                return rel, json.loads(p.read_text())
            except json.JSONDecodeError as e:
                sys.exit(f"{rel} is not valid JSON: {e}")
    return None, synth_manifest(repo)


def discover_skills(repo):
    """All skills/<name>/SKILL.md (one or two levels deep). 'template'
    folders (bama's app scaffold) are kept out of the build but returned
    separately so the caller can report them."""
    found = sorted(
        str(p.relative_to(repo))
        for p in repo.glob("skills/**/SKILL.md")
        if p.parent.name != "skills"
    )
    usable = [f for f in found if "template" not in Path(f).parts]
    left_out = sorted({Path(f).parent.name for f in found if f not in usable})
    return usable, left_out


def collect_skills(repo, manifest):
    """Returns (entries, left_out). entries drive the plugins. left_out names
    skill folders on disk that will not ship - 'template' dirs, or folders the
    upstream manifest's skills list does not name - so nothing drops
    silently on a sync."""
    usable, template_out = discover_skills(repo)
    by_name = {Path(f).parent.name: f for f in usable}
    entries = manifest.get("skills")
    if isinstance(entries, list) and entries:
        listed = [str(e) for e in entries]
        listed_names = {Path(e.rstrip("/")).name for e in listed}
        return listed, sorted(set(template_out) | (set(by_name) - listed_names))
    if not usable:
        sys.exit("no skills list in the manifest and no skills/*/SKILL.md found")
    # "./skills/<name>" matches the format upstream manifests use (and the
    # runtime honors); no trailing slash
    return ["./" + str(Path(f).parent) for f in usable], template_out


def group_of(entry):
    parts = Path(entry.rstrip("/")).parts
    if len(parts) >= 3 and parts[-3] == "skills":
        return parts[-2]      # skills/<group>/<name>
    if len(parts) >= 2 and parts[-2] == "skills":
        return ""             # skills/<name> (flat)
    sys.exit(f"unexpected skill path: {entry}")


def default_desc(group, names):
    if group:
        return f"{group.capitalize()} skills: {', '.join(sorted(names)).replace('-', ' ')}."
    return f"Skills: {', '.join(sorted(names)).replace('-', ' ')}."


def covered(p, copied):
    return any(p == c or c in p.parents for c in copied)


def repos_of(config):
    """A config's repos list; pre-'repos' single-repo configs keep working."""
    if config.get("repos"):
        return config["repos"]
    if config.get("repo"):
        return [{
            "path": config["repo"],
            "url": config.get("repo_url", ""),
        }]
    return []


def check_config(config, path):
    """Clean errors for hand-edited or stale build-configs, before any build."""
    if not isinstance(config, dict):
        sys.exit(f"{path} must be a JSON object")
    for key in ("prefix", "marketplace_name"):
        if not config.get(key):
            sys.exit(f"{path} is missing '{key}'; rerun 'new' to regenerate it")
    if not repos_of(config):
        sys.exit(f"{path} names no repos")
    acfg = config.get("agents")
    if acfg is not None:
        if not isinstance(acfg, dict):
            sys.exit(f"{path}: 'agents' must be an object")
        unknown = sorted(set(acfg) - {"enabled", "scoped", "title_prefix", "title_overrides", "shared_skills"})
        if unknown:
            sys.exit(f"{path}: unknown 'agents' key(s): {', '.join(unknown)}")
        sh = acfg.get("shared_skills")
        if sh is not None and not (
            isinstance(sh, dict)
            and all(isinstance(k, str) and isinstance(v, list)
                    and all(isinstance(x, str) for x in v) for k, v in sh.items())
        ):
            sys.exit(f"{path}: 'agents.shared_skills' must map a skill name to a list of group ids")
        if acfg.get("enabled") and not config.get("plugin_name"):
            sys.exit(f"{path}: 'agents' needs single-plugin mode (a 'plugin_name'); rerun 'new' with --single")
    return config


def regroup_explicit(entries, groups_cfg, repo_name, shared=None):
    """groups-file entries carrying a "skills" list reassign a flat repo's
    skills to explicit groups. A group none of whose skills are in this repo
    belongs to another repo and is skipped; partial overlap is an error.

    A group entry with "catch_all": true absorbs skills the groups file
    does not list (e.g. added upstream after packaging), so syncs keep
    building; absorbed skills are reported so they get regrouped on purpose.
    Without a catch-all group, unlisted skills are an error.

    A skill listed in more than one group is an error unless `shared` (from
    agents.shared_skills) allows exactly that: a deliberate share, for a skill
    two Agents each need mid-flow (the physical layout stays exclusive; shares
    are a property of the agent map)."""
    by_name = {Path(e.rstrip("/")).name: e for e in entries}
    assigned, seen, seen_in = {}, set(), {}
    catch_all = [g for g, c in groups_cfg.items()
                 if isinstance(c, dict) and c.get("catch_all")]
    if len(catch_all) > 1:
        sys.exit(f'only one group may carry "catch_all": {catch_all}')
    for g, gcfg in groups_cfg.items():
        listed = gcfg.get("skills") if isinstance(gcfg, dict) else None
        if not listed:
            continue
        found = [n for n in listed if n in by_name]
        if not found:
            continue          # another repo's group
        if len(found) != len(listed):
            missing = sorted(set(listed) - set(found))
            sys.exit(f"explicit group '{g}' lists skills missing from {repo_name}: {missing}")
        for n in found:
            if n in seen:
                if g not in (shared or {}).get(n, []):
                    sys.exit(f"skill '{n}' is listed in more than one explicit group")
                print(f"note: skill '{n}' is shared by groups {seen_in[n] + [g]} (agents.shared_skills)")
            seen.add(n)
            seen_in.setdefault(n, []).append(g)
            assigned.setdefault(g, []).append(by_name[n])
    strayed = {}
    if seen:
        missing = sorted(set(by_name) - seen)
        if missing:
            if not catch_all:
                sys.exit(f"skills of {repo_name} not assigned to any explicit group: {missing}")
            g = catch_all[0]
            for n in missing:
                assigned.setdefault(g, []).append(by_name[n])
            strayed[g] = missing
        return assigned, set(assigned), strayed
    return None, set(), strayed


def inside_repo(target, repo):
    """True when target is a file inside the repo's own tree (not its .git).
    Links may reach repo-root folders such as tools/ that sit outside skills/;
    those files ship at their repo-relative path so the links still resolve."""
    try:
        rel = target.resolve().relative_to(repo.resolve())
    except ValueError:
        return False
    return ".git" not in rel.parts


def copy_entries(repo, entries, root):
    """Copy skill folders into a plugin root byte-identical, then copy files
    the entries' own files link to across groups, preserving repo-relative
    paths so the links keep resolving. Never copies another skill's SKILL.md:
    every skills/<name>/SKILL.md registers as a skill.
    Returns (linked_file_count, skipped_link_texts)."""
    copied = set()  # resolved REPO-tree paths this plugin carries
    for entry in entries:
        shutil.copytree(repo / entry.rstrip("/"), root / entry.rstrip("/"))
        copied.add((repo / entry.rstrip("/")).resolve())
    queue = []
    for d in copied:
        if d.is_dir():
            queue.extend(p for p in sorted(d.rglob("*.md")))
    extra, skipped = 0, []
    root_resolved = root.resolve()
    while queue:
        repo_file = queue.pop()
        local = root_resolved / repo_file.relative_to(repo)
        text = local.read_text(errors="replace")
        for m in LINK_RE.finditer(text):
            ref = unquote(m.group(1) or m.group(2) or "")
            if re.match(r"^[a-z]+:", ref) or ref.startswith("$"):
                continue  # http(s):, mailto:, ${CLAUDE_*} templates
            target = (repo_file.parent / ref).resolve()
            if not inside_repo(target, repo) or not target.is_file():
                continue
            if covered(target, copied):
                continue
            if target.name == "SKILL.md":
                skipped.append(str(target.relative_to(repo)))
                continue
            rel = target.relative_to(repo)
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dst)
            copied.add(target)
            extra += 1
            if dst.suffix == ".md":
                queue.append(target)
    return extra, skipped


def flatten_moves(upstream, repo_name):
    """Flat single-plugin layout: every skill folder moves to skills/<name>,
    whatever folder it sat in upstream (e.g. skills/design/<name>). Two skills
    with one name in a repo would collide, so that is a clean error."""
    moves, entries, seen = {}, [], {}
    for e in upstream:
        src = Path(e.rstrip("/"))
        dst = Path("skills") / src.name
        if dst.as_posix() in seen:
            sys.exit(
                f"flatten: {repo_name} has two skills named '{src.name}' "
                f"({seen[dst.as_posix()]} and {src.as_posix()}); rename one "
                "upstream, or set \"flatten\": false to ship the grouped layout"
            )
        seen[dst.as_posix()] = src.as_posix()
        if src != dst:
            moves[src] = dst
        entries.append("./" + dst.as_posix())
    return moves, entries, set()


def regroup_single(upstream, groups_cfg, repo_name, flatten=False):
    """Single-plugin layout. flatten: every skill folder moves to skills/<name>
    (flatten_moves). Otherwise a groups file's explicit "skills" lists move a
    repo's flat skills/<name> into skills/<group>/<name> inside the one plugin.
    Returns (moves, entries, used_groups): moves maps each moved upstream path
    to its new one; entries is the manifest list with the new paths."""
    if flatten:
        return flatten_moves(upstream, repo_name)
    moves, entries, used = {}, list(upstream), set()
    if not groups_cfg:
        return moves, entries, used
    assigned, used, _ = regroup_explicit(upstream, groups_cfg, repo_name)
    if not assigned:
        return moves, entries, used
    new_of = {}
    for g, members in assigned.items():
        for e in members:
            src = Path(e.rstrip("/"))
            dst = Path("skills") / g / src.name
            if src != dst:
                moves[src] = dst
            new_of[e] = "./" + dst.as_posix()
    entries = [new_of.get(e, e) for e in upstream]
    return moves, entries, used


def relocate(moves):
    """Destination of a repo-relative path: anything at or under a moved skill
    folder goes with it; everything else keeps its path."""
    def dest(rel):
        for old, new in moves.items():
            if rel == old or old in rel.parents:
                return new / rel.relative_to(old)
        return rel
    return dest


def relink(text, rel, repo, dest):
    """Point each markdown link in text (a file at repo-relative rel) at its
    target's destination, written relative to the file's own destination, so
    links still resolve after a move. Links to things that did not move from a
    file that did not move are left exactly as written."""
    old_dir = rel.parent
    new_dir = dest(rel).parent

    def fix(m):
        angle = m.group(1) is not None
        g = 1 if angle else 2
        raw = m.group(g) or ""
        ref = unquote(raw)
        if re.match(r"^[a-z]+:", ref) or ref.startswith("$"):
            return m.group(0)
        target = ((repo / rel).parent / ref).resolve()
        if repo not in target.parents:
            return m.group(0)
        t_rel = target.relative_to(repo)
        new_t = dest(t_rel)
        if new_t == t_rel and new_dir == old_dir:
            return m.group(0)
        new_ref = posixpath.relpath(new_t.as_posix(), new_dir.as_posix())
        if not angle and (" " in new_ref or "%" in raw):
            new_ref = quote(new_ref, safe="/")
        s = m.group(0)
        start, end = m.start(g) - m.start(0), m.end(g) - m.start(0)
        return s[:start] + new_ref + s[end:]

    return LINK_RE.sub(fix, text)


ROOT_VAR_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s)\"'`\]]+)")


def remap_plugin_root(text, dest):
    """Upstream text may name a file as ${CLAUDE_PLUGIN_ROOT}/<repo-relative
    path>. When that file moved (flatten, grouping), point the variable's path
    at where it now lives; paths that did not move stay as written."""
    def fix(m):
        rel = Path(m.group(1))
        new = dest(rel)
        return m.group(0) if new == rel else "${CLAUDE_PLUGIN_ROOT}/" + new.as_posix()

    return ROOT_VAR_RE.sub(fix, text)


def relink_all(text, rel, repo, dest):
    return remap_plugin_root(relink(text, rel, repo, dest), dest)


def copy_relocated(repo, upstream, moves, root, extra_files=()):
    """copy_entries for single-plugin builds, where flat skills may move into
    groups (moves: upstream path -> new path). Every skill folder lands at its
    destination; markdown links that reach a moved file are rewritten to where
    the target now lives. Files whose links did not change stay byte-identical.
    Never carries another skill's SKILL.md. extra_files (repo-relative paths,
    such as Claude Code agent files) ship too, and their links are followed
    like a skill's. Returns (linked_file_count, skipped_link_texts)."""
    repo = repo.resolve()
    dest = relocate(moves)
    entry_dirs = [Path(e.rstrip("/")) for e in upstream]
    extra_files = {Path(p) for p in extra_files}
    carried = set(extra_files)
    for d in entry_dirs:
        carried.update(p.relative_to(repo) for p in (repo / d).rglob("*") if p.is_file())
    queue = [p for p in carried if p.suffix == ".md"]
    skipped = []
    while queue:
        rel = queue.pop()
        text = (repo / rel).read_text(errors="replace")
        for m in LINK_RE.finditer(text):
            ref = unquote(m.group(1) or m.group(2) or "")
            if re.match(r"^[a-z]+:", ref) or ref.startswith("$"):
                continue
            target = ((repo / rel).parent / ref).resolve()
            if not inside_repo(target, repo) or not target.is_file():
                continue
            t_rel = target.relative_to(repo)
            if t_rel in carried:
                continue
            if target.name == "SKILL.md":
                skipped.append(str(t_rel))
                continue
            carried.add(t_rel)
            if target.suffix == ".md":
                queue.append(t_rel)
    extra = 0
    for rel in sorted(carried):
        src = repo / rel
        out = root / dest(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        fixed = None
        if rel.suffix == ".md":
            text = src.read_text(errors="replace")
            new = relink_all(text, rel, repo, dest)
            if new != text:
                fixed = new
        if fixed is None:
            shutil.copy2(src, out)
        else:
            out.write_text(fixed)
        if rel not in extra_files and not any(rel == d or d in rel.parents for d in entry_dirs):
            extra += 1
    return extra, skipped


def find_license(repo):
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING"):
        p = repo / name
        if p.is_file():
            return p
    return None


def generate(config):
    if config.get("plugin_name"):
        return generate_single(config)
    adapter = Path(config["adapter_dir"]).resolve()
    mkt_dir = adapter / "marketplace"
    # Build into a staging dir and swap it in only on success: a failed build
    # (bad groups file, upstream skill removed, ...) leaves the marketplace a
    # running Enconvo points at untouched. Leftovers of an interrupted build
    # are cleared first.
    staging = adapter / ".marketplace-new"
    retired = adapter / ".marketplace-old"
    for d in (staging, retired):
        if d.exists():
            shutil.rmtree(d)
    prefix = config["prefix"]
    mkt_name = config["marketplace_name"]
    owner = config.get("owner", DEFAULT_OWNER)
    category = config.get("category", DEFAULT_CATEGORY)
    groups_cfg = config.get("groups", {})
    repos = repos_of(config)
    multi = len(repos) > 1

    plugins, report = [], []
    unshipped_all = {}
    versions, skills_by_plugin = {}, {}
    nested = False
    matched_groups, skill_owner = set(), {}
    for rcfg in repos:
        repo = Path(rcfg["path"]).resolve()
        repo_url = rcfg.get("url", "")
        _, manifest = find_manifest(repo)
        version = manifest.get("version", "1.0.0")
        sha = run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, check=False)
        build_version = f"{version}+{sha}" if sha else version
        versions[repo.name] = build_version
        icon_src = repo / ".claude-plugin/icon.png"
        if not icon_src.is_file():
            icon_src = repo / "icon.png"
        skills_root = (repo / "skills").resolve()

        entries, left_out = collect_skills(repo, manifest)
        if left_out:
            print(
                f"warning: {repo.name}: skill folder(s) on disk left out of "
                f"the build: {', '.join(left_out)}"
            )
        for e in entries:
            if not (repo / e.rstrip("/")).is_dir():
                sys.exit(
                    f"skill folder of {repo.name} is listed in its manifest "
                    f"but missing on disk: {e}"
                )
        grouped = {}
        unshipped = ag.unshipped_parts(repo, manifest)
        if unshipped:
            unshipped_all.setdefault(repo.name, []).extend(unshipped)
            print(
                f"warning: {repo.name}: not shipped by the builder (skills and "
                f"agents only): {', '.join(unshipped)} - recorded in the "
                "adapter README"
            )
        for e in entries:
            grouped.setdefault(group_of(e), []).append(e)
        assigned, explicit_set, strayed = regroup_explicit(
            entries, groups_cfg, repo.name
        )
        matched_groups.update(explicit_set)
        if assigned:
            grouped = assigned

        for group, group_entries in sorted(grouped.items()):
            explicit_group = group in explicit_set
            key = repo.name if (multi and not group and not explicit_group) else group
            matched_groups.add(key)
            gcfg = groups_cfg.get(key, {})
            names = [Path(e.rstrip("/")).name for e in group_entries]
            if explicit_group:
                name = gcfg.get("plugin") or f"{prefix}-{group}"
            else:
                base = repo.name if multi else prefix
                name = gcfg.get("plugin") or (base if not group else f"{base}-{group}")
            desc = gcfg.get("description")
            if not desc and multi:
                desc = manifest.get("description")
            if not desc:
                desc = default_desc(group, names)
            nested = nested or (bool(group) and not explicit_group)
            if any(p["name"] == name for p in plugins):
                sys.exit(
                    f"plugin name '{name}' produced twice in this marketplace "
                    "- set a distinct 'plugin' in the groups file"
                )
            for n in names:
                if n in skill_owner:
                    print(
                        f"warning: skill '{n}' ships in both "
                        f"{skill_owner[n]} and {name}"
                    )
                else:
                    skill_owner[n] = name
            root = staging / "plugins" / name
            (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)

            extra, skipped = copy_entries(repo, group_entries, root)

            icon_file = (
                icon_src if icon_src.is_file()
                else Path(config["plugin_icon"])
                if config.get("plugin_icon") and Path(config["plugin_icon"]).is_file()
                else None
            )
            has_icon = icon_file is not None
            if has_icon:
                shutil.copy2(icon_file, root / "icon.png")
            else:
                write_placeholder_icon(root / "icon.png", name)
                print(
                    f"note: {name}: no upstream icon; wrote a placeholder - "
                    "set plugin_icon in build-config.json to replace it"
                )
                has_icon = True

            keywords = sorted(
                (set(manifest.get("keywords", [])) | {prefix, group}) - {""}
            )
            plugin_manifest = {
                "name": name,
                "version": build_version,
                "description": desc,
                "author": manifest.get("author", owner),
                "repository": manifest.get("repository") or repo_url,
                "license": manifest.get("license", "MIT"),
                "keywords": [k for k in keywords if k],
                "skills": group_entries,
            }
            if has_icon:
                plugin_manifest["icon"] = "./icon.png"
            (root / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(plugin_manifest, indent=2) + "\n"
            )
            native_skills = []
            for e in group_entries:
                sf = root / e.rstrip("/") / "SKILL.md"
                if not sf.is_file():
                    continue
                fm, _b = ag.split_frontmatter(sf.read_text(errors="replace"))
                sd = ""
                for ln in fm:
                    if ln.startswith("description:"):
                        sd = ln.split(":", 1)[1].strip().strip('"')
                        break
                native_skills.append({"name": Path(e.rstrip("/")).name, "description": sd})
            native = {
                "$schema": "https://enconvo.com/schemas/extension.json",
                "name": name,
                "version": build_version,
                "title": name.replace("-", " ").replace("_", " ").title(),
                "description": desc,
                "author": (owner.get("name") if isinstance(owner, dict) else str(owner)),
                "license": plugin_manifest["license"],
                "keywords": plugin_manifest["keywords"],
                "skills": native_skills,
            }
            if isinstance(owner, dict) and owner.get("url"):
                native["homepage"] = owner["url"]
            if config.get("plugin_category"):
                native["categories"] = [config["plugin_category"]]
            if has_icon:
                (root / "assets").mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / "icon.png", root / "assets" / "icon.png")
                native["icon"] = "icon.png"
            (root / "package.json").write_text(json.dumps(native, indent=2) + "\n")
            plugins.append({
                "name": name,
                "source": f"./plugins/{name}",
                "description": desc,
                "author": plugin_manifest["author"],
                "category": category,
                "keywords": plugin_manifest["keywords"],
            })
            skills_by_plugin[name] = names
            line = (
                f"{name}: {len(group_entries)} "
                f"skill{'s' if len(group_entries) != 1 else ''}, +{extra} linked file(s)"
            )
            if strayed.get(group):
                line += f", catch-all absorbed {sorted(strayed[group])}"
            if skipped:
                line += f", foreign SKILL.md links left alone: {sorted(set(skipped))}"
            line += f" (v{build_version})"
            report.append(line)

    if config.get("marketplace_description"):
        mkt_desc = config["marketplace_description"]
    elif multi:
        names = ", ".join(Path(r["path"]).name for r in repos)
        mkt_desc = f"{prefix} skills from {names}, packaged as per-group Enconvo plugins."
    else:
        mkt_desc = (
            f"{prefix} split into per-group agent plugins. Built from "
            f"{config.get('repo_url') or manifest.get('repository', 'upstream')} "
            f"({manifest.get('license', 'MIT')})."
        )
    marketplace = {
        "name": mkt_name,
        "owner": owner,
        "description": mkt_desc,
        "plugins": plugins,
    }
    (staging / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (staging / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(marketplace, indent=2) + "\n"
    )
    if mkt_dir.exists():
        mkt_dir.rename(retired)
    staging.rename(mkt_dir)
    if retired.exists():
        shutil.rmtree(retired)
    unused = sorted(
        g for g, c in groups_cfg.items()
        if g not in matched_groups and isinstance(c, dict)
        and (c.get("description") or c.get("plugin") or c.get("skills"))
    )
    if unused:
        print(
            f"warning: groups-file keys matched no group in any repo "
            f"(typo?): {', '.join(unused)}"
        )
    return {
        "unshipped": unshipped_all,
        "mkt_dir": mkt_dir,
        "plugins": plugins,
        "report": report,
        "versions": versions,
        "skills_by_plugin": skills_by_plugin,
        "nested": nested,
    }


def generate_single(config):
    """Single-plugin mode (build-config 'plugin_name'): every repo's skills
    merge into ONE plugin, each skill kept at its upstream path. Series
    convention: folder, marketplace and plugin all carry the adapter's name
    (e.g. bama-enconvo). A single-repo plugin keeps the upstream author in
    its manifest; a multi-repo merge credits the series owner and copies each
    upstream's LICENSE file as LICENSE-<repo> so attribution still ships."""
    adapter = Path(config["adapter_dir"]).resolve()
    mkt_dir = adapter / "marketplace"
    staging = adapter / ".marketplace-new"
    retired = adapter / ".marketplace-old"
    for d in (staging, retired):
        if d.exists():
            shutil.rmtree(d)
    prefix = config["prefix"]
    mkt_name = config["marketplace_name"]
    owner = config.get("owner", DEFAULT_OWNER)
    category = config.get("category", DEFAULT_CATEGORY)
    plugin_name = config["plugin_name"]
    # flatten ships every skill at skills/<name>; the groups stay in the
    # build-config as the agent map (which skills each agent is meant to use).
    flatten = bool(config.get("flatten"))
    groups_cfg = {} if flatten else config.get("groups", {})
    matched_groups = set()
    repos = repos_of(config)
    multi = len(repos) > 1

    root = staging / "plugins" / plugin_name
    (root / ".claude-plugin").mkdir(parents=True)
    merged_entries, merged_names = [], []
    merged_keywords = {prefix}
    desc_bits, licenses = [], set()
    versions = {}
    extra_total, skipped_all = 0, []
    author, repository, version_stamp, icon_src = None, "", "1.0.0", None
    license_files = []
    nested = False
    seen_paths = set()
    unshipped_all = {}
    agents_cfg = config.get("agents") or {}
    agents_on = bool(agents_cfg.get("enabled"))
    scoped = agents_on and agents_cfg.get("scoped", True) is not False
    shipped_agents, agent_paths = [], {}
    explicit_map, nesting_map = {}, {}
    raw_groups = config.get("groups") or {}
    for idx, rcfg in enumerate(repos):
        repo = Path(rcfg["path"]).resolve()
        repo_url = rcfg.get("url", "")
        _, manifest = find_manifest(repo)
        version = manifest.get("version", "1.0.0")
        sha = run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, check=False)
        build_version = f"{version}+{sha}" if sha else version
        versions[repo.name] = build_version
        if idx == 0:
            version_stamp = build_version
            author = owner if multi else (manifest.get("author") or owner)
            repository = manifest.get("repository") or repo_url
        if icon_src is None:
            cand = repo / ".claude-plugin/icon.png"
            if not cand.is_file():
                cand = repo / "icon.png"
            if cand.is_file():
                icon_src = cand
        entries, left_out = collect_skills(repo, manifest)
        if left_out:
            print(
                f"warning: {repo.name}: skill folder(s) on disk left out of "
                f"the build: {', '.join(left_out)}"
            )
        upstream = entries
        moves, entries, used = regroup_single(upstream, groups_cfg, repo.name, flatten)
        matched_groups |= used
        # The groups are the agent map even when the plugin ships flat, so
        # they are validated here too (a skill in no group is an error unless
        # a catch_all group takes it).
        if raw_groups:
            assigned_raw, used_raw, strayed = regroup_explicit(
                upstream, raw_groups, repo.name,
                shared=(config.get("agents") or {}).get("shared_skills"))
            matched_groups |= used_raw
            for g, es in (assigned_raw or {}).items():
                explicit_map.setdefault(g, []).extend(Path(e.rstrip("/")).name for e in es)
            for g, names in strayed.items():
                print(f"warning: {repo.name}: skills not listed in any group went to catch-all '{g}': {', '.join(names)}")
        for e in upstream:
            parts = Path(e.rstrip("/")).parts
            if len(parts) >= 3 and parts[-3] == "skills":
                nesting_map.setdefault(parts[-2], []).append(parts[-1])
        for e in upstream:
            if not (repo / e.rstrip("/")).is_dir():
                sys.exit(
                    f"skill folder of {repo.name} is listed in its manifest "
                    f"but missing on disk: {e}"
                )
        for e in entries:
            rel = e.rstrip("/")
            if rel in seen_paths:
                sys.exit(
                    f"single-plugin build: skill path '{rel}' occurs in more "
                    f"than one repo or group ({repo.name}); keep per-group plugins "
                    "or resolve the duplicate upstream"
                )
            seen_paths.add(rel)
        dest = relocate(moves)
        agent_list = ag.collect_agents(repo, manifest)
        for a in agent_list:
            a["dest"] = dest(a["rel"])
            key = a["dest"].as_posix()
            if key in agent_paths:
                sys.exit(
                    f"single-plugin build: agent file '{key}' occurs in both "
                    f"{agent_paths[key]} and {repo.name}; rename one upstream"
                )
            agent_paths[key] = repo.name
        unshipped = ag.unshipped_parts(repo, manifest)
        if unshipped:
            unshipped_all.setdefault(repo.name, []).extend(unshipped)
            print(
                f"warning: {repo.name}: not shipped by the builder (skills and "
                f"agents only): {', '.join(unshipped)} - recorded in the "
                "adapter README"
            )
        extra, skipped = copy_relocated(
            repo, upstream, moves, root, [a["rel"] for a in agent_list]
        )
        shipped_agents.extend(agent_list)
        extra_total += extra
        skipped_all += skipped
        merged_entries.extend(entries)
        merged_names.extend(Path(e.rstrip("/")).name for e in entries)
        merged_keywords |= set(manifest.get("keywords", []))
        desc_bits.append(manifest.get("description") or repo.name)
        licenses.add(manifest.get("license", "MIT"))
        nested = nested or any(group_of(e) for e in entries)
        if multi:
            lic = find_license(repo)
            if lic:
                shutil.copy2(lic, root / f"LICENSE-{repo.name}")
                license_files.append(f"LICENSE-{repo.name}")
            else:
                print(f"warning: {repo.name}: no LICENSE file found to ship in the merged plugin")

    agent_defs = (
        ag.make_agent_defs(config, explicit_map, shipped_agents, nesting_map, merged_names)
        if agents_on else []
    )
    if agents_on and not agent_defs:
        sys.exit(
            "agents are enabled but the build has no agent split: add 'groups' "
            "to build-config.json, or use a repo whose agents/*.md name their "
            "skills or whose skills are nested in folders"
        )
    scoped_count = 0
    if scoped:
        entry_of = {Path(e.rstrip("/")).name: e for e in merged_entries}
        for n in sorted({n for d in agent_defs for n in d["skills"]}):
            f = root / entry_of[n].rstrip("/") / "SKILL.md"
            new_text, status = ag.inject_visibility(f.read_text(errors="replace"), "scoped")
            if status == "no-frontmatter":
                print(f"warning: {n}: SKILL.md has no frontmatter, so it cannot be scoped to its agent")
            elif status != "unchanged":
                f.write_text(new_text)
                scoped_count += 1

    if agents_on:
        have_upstream = {a["dest"].stem for a in shipped_agents}
        for d in agent_defs:
            if d["id"] in have_upstream:
                continue
            adest = Path("agents") / f"{d['id']}.md"
            (root / adest).parent.mkdir(parents=True, exist_ok=True)
            desc_line = d["description"].replace('"', "'")
            (root / adest).write_text(
                f"---\nname: {d['id']}\ndescription: \"{desc_line}\"\n---\n\n{d['prompt'].strip()}\n"
            )
            shipped_agents.append({"dest": adest, "title": d["title"]})

    desc = config.get("plugin_description") or (
        desc_bits[0] if len(desc_bits) == 1 else " ; ".join(desc_bits)
    )
    keywords = sorted(merged_keywords - {""})
    plugin_manifest = {
        "name": plugin_name,
        "version": version_stamp,
        "description": desc,
        "author": author,
        "repository": repository,
        "license": "MIT" if licenses == {"MIT"} else ", ".join(sorted(licenses)),
        "keywords": keywords,
        "skills": merged_entries,
    }
    if shipped_agents:
        plugin_manifest["agents"] = ["./" + a["dest"].as_posix() for a in shipped_agents]
    if icon_src is None and config.get("plugin_icon"):
        icon_src = Path(config["plugin_icon"])
    if icon_src is not None and icon_src.is_file():
        shutil.copy2(icon_src, root / "icon.png")
    if not (root / "icon.png").is_file():
        write_placeholder_icon(root / "icon.png", config.get("plugin_title") or plugin_name)
        print(
            f"note: {plugin_name}: no upstream icon; wrote a placeholder - "
            "set plugin_icon in build-config.json to replace it"
        )
    plugin_manifest["icon"] = "./icon.png"
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(plugin_manifest, indent=2) + "\n"
    )
    skill_list = []
    for e in merged_entries:
        sf = root / e.rstrip("/") / "SKILL.md"
        if not sf.is_file():
            continue
        fm, _body = ag.split_frontmatter(sf.read_text(errors="replace"))
        sd = ""
        for line in fm:
            if line.startswith("description:"):
                sd = line.split(":", 1)[1].strip().strip('"')
                break
        skill_list.append({"name": Path(e.rstrip("/")).name, "description": sd})
    native = {
        "$schema": "https://enconvo.com/schemas/extension.json",
        "name": plugin_name,
        "version": version_stamp,
        "title": config.get("plugin_title") or prefix.title(),
        "description": desc,
        "author": owner.get("name") or "Iteractive Development",
        "license": plugin_manifest["license"],
        "keywords": keywords,
        "skills": skill_list,
    }
    if owner.get("url"):
        native["homepage"] = owner["url"]
    if config.get("plugin_category"):
        native["categories"] = [config["plugin_category"]]
    if (root / "icon.png").is_file():
        (root / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / "icon.png", root / "assets" / "icon.png")
        native["icon"] = "icon.png"
    (root / "package.json").write_text(json.dumps(native, indent=2) + "\n")
    plugins = [{
        "name": plugin_name,
        "source": f"./plugins/{plugin_name}",
        "description": desc,
        "author": author,
        "category": category,
        "keywords": keywords,
    }]
    if config.get("marketplace_description"):
        mkt_desc = config["marketplace_description"]
    else:
        names = ", ".join(Path(r["path"]).name for r in repos)
        mkt_desc = f"{prefix} skills from {names}, packaged as one updatable Enconvo plugin."
    marketplace = {
        "name": mkt_name,
        "owner": owner,
        "description": mkt_desc,
        "plugins": plugins,
    }
    (staging / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (staging / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(marketplace, indent=2) + "\n"
    )
    if mkt_dir.exists():
        mkt_dir.rename(retired)
    staging.rename(mkt_dir)
    if retired.exists():
        shutil.rmtree(retired)
    map_path = adapter / "agent-map.json"
    if agents_on:
        map_path.write_text(json.dumps(
            {"plugin": plugin_name, "marketplace": mkt_name, "agents": agent_defs}, indent=2
        ) + "\n")
    else:
        map_path.unlink(missing_ok=True)
    unused = sorted(
        g for g, c in groups_cfg.items()
        if g not in matched_groups and isinstance(c, dict)
        and (c.get("description") or c.get("skills"))
    )
    if unused:
        print(
            f"warning: groups-file keys matched no group in any repo "
            f"(typo?): {', '.join(unused)}"
        )
    line = (
        f"{plugin_name}: {len(merged_entries)} skills, +{extra_total} linked file(s)"
    )
    if shipped_agents:
        line += f", {len(shipped_agents)} agent file(s)"
    if agents_on:
        line += f", {len(agent_defs)} Enconvo agent(s) ({scoped_count} skill(s) newly scoped)"
    if skipped_all:
        line += f", foreign SKILL.md links left alone: {sorted(set(skipped_all))}"
    line += f" (v{version_stamp})"
    if multi:
        line += f", LICENSE files copied: {sorted(license_files)}"
    return {
        "unshipped": unshipped_all,
        "mkt_dir": mkt_dir,
        "plugins": plugins,
        "report": [line],
        "versions": versions,
        "skills_by_plugin": {plugin_name: merged_names},
        "nested": nested,
        "agents": agent_defs,
    }


def write_support_files(config, result):
    adapter = Path(config["adapter_dir"])
    self_exe = Path(__file__).resolve()
    repos = repos_of(config)
    sources = ", ".join(r.get("url") or Path(r["path"]).name for r in repos)
    (adapter / "sync.sh").unlink(missing_ok=True)  # legacy updaters: the
    (adapter / "sync.py").unlink(missing_ok=True)  # `sync` subcommand replaced them
    lines = ["| Plugin | Skills |", "| --- | --- |"]
    for p in result["plugins"]:
        lines.append(
            f"| `{p['name']}` | {', '.join(sorted(result['skills_by_plugin'][p['name']]))} |"
        )
    validator_note = (
        "`enconvo plugin validate` reports `plugin_runtime_surface_missing` on"
        " this nested skills/<group>/<name> layout; set \"flatten\": true in"
        " build-config.json to ship skills/<name> instead."
        if result["nested"]
        else ""
    )
    agents_md = ""
    if result.get("agents"):
        rows = ["| Agent | Skills |", "| --- | --- |"] + [
            f"| {a['title']} | {', '.join(a['skills'])} |" for a in result["agents"]
        ]
        agents_md = (
            "## Agents\n\n" + "\n".join(rows) + "\n\n"
            "Each Agent sees only its own skills: they are marked `visibility: scoped`"
            " in the plugin (hidden from every other Agent) and linked into the"
            " Agent's folder from the installed plugin. After installing or updating"
            f" the plugin run:\n\n    python3 {self_exe} agents --config {adapter / 'build-config.json'}\n\n"
            "`sync --apply` does the update and this step together. The step is"
            " idempotent and never overwrites an Agent's title, prompt or settings.\n"
        )
    single = bool(config.get("plugin_name"))
    packaging_line = (
        "all skills packaged as one updatable plugin" if single
        else "skills split into per-group plugins"
    )
    attribution = (
        "Attribution: upstream authors and licenses are kept in every manifest."
        if not single or len(repos) == 1
        else "Attribution: merged multi-repo build - the manifest is credited to "
             f"{config.get('owner', DEFAULT_OWNER)['name']} and each upstream's "
             "LICENSE file ships as LICENSE-<repo>; sources are named in the "
             "manifest description."
    )
    (adapter / "README.md").write_text(f'''# {config['prefix']}-enconvo

Enconvo packaging for {sources}: {packaging_line}.
Generated by the repo-to-plugin project skill; upstream clones stay pristine
- never edit them.

{chr(10).join(lines)}

{agents_md}
## Update flow

    python3 {self_exe} sync --apply --config {adapter / 'build-config.json'}

pulls upstream, rebuilds, runs the `enconvo plugin` refresh/update and refreshes
the Agents. Without --apply it prints those commands instead (Plugins > ... >
Check for updates works too). Versions stamp as `<version>+<commit>`.

## Install (per Mac)

    enconvo plugin marketplace add {adapter / 'marketplace'}
{chr(10).join('    enconvo plugin install ' + p['name'] + '@' + config['marketplace_name'] for p in result['plugins'])}

{validator_note}

{attribution}
''')
    unshipped = result.get("unshipped") or {}
    if unshipped:
        readme = adapter / "README.md"
        body = readme.read_text().rstrip() + "\n\n## Upstream parts not shipped\n\n"
        for rn, parts in sorted(unshipped.items()):
            body += (
                f"- {rn}: {', '.join(parts)} - no loadable Enconvo "
                "equivalent; the plugin ships skills and agents only.\n"
            )
        readme.write_text(body)


def cmd_new(args):
    workspace = Path(args.workspace).resolve() if args.workspace else Path.cwd()
    single = len(args.repo) == 1
    repos_cfg = []
    first_local_name = ""
    for repo_arg in args.repo:
        if "://" in repo_arg or repo_arg.endswith(".git"):
            if not args.name:
                sys.exit("--name is required when --repo is a URL")
            # single URL keeps the old semantics (clone into --name); several
            # URLs each clone into their repo-derived folder name
            rname = args.name if single else re.sub(r"\.git$", "", repo_arg.rstrip("/").rsplit("/", 1)[-1])
            repo = workspace / rname
            if (repo / ".git").exists():
                origin = run(["git", "-C", str(repo), "remote", "get-url", "origin"], check=False)
                if origin and origin != repo_arg:
                    sys.exit(f"{repo} already exists but tracks {origin}, not {repo_arg}")
            else:
                run(["git", "clone", "--depth", "1", repo_arg, str(repo)])
            repo_url = repo_arg
        else:
            repo = Path(repo_arg).resolve()
            repo_url = ""
            if (repo / ".git").exists():
                repo_url = run(["git", "-C", str(repo), "remote", "get-url", "origin"], check=False)
        if not repo.is_dir():
            sys.exit(f"repo not found: {repo}")
        if not first_local_name:
            first_local_name = repo.name
        repos_cfg.append({"path": str(repo), "url": repo_url})

    multi = len(repos_cfg) > 1
    if multi and not args.name:
        sys.exit("--name is required when combining multiple --repo")
    prefix = args.name or first_local_name
    adapter = workspace / f"{prefix}-enconvo"
    adapter.mkdir(parents=True, exist_ok=True)
    groups_cfg = load_json(args.groups_file, "groups file") if args.groups_file else {}
    if not isinstance(groups_cfg, dict):
        sys.exit("groups file must be a JSON object mapping group names to objects")
    config = {
        "repos": repos_cfg,
        "adapter_dir": str(adapter),
        "prefix": prefix,
        "marketplace_name": args.marketplace or f"{prefix}-enconvo",
        "category": args.category,
        "marketplace_description": args.marketplace_description or "",
        "owner": {"name": args.owner_name, "url": args.owner_url} if args.owner_name else dict(DEFAULT_OWNER),
        "groups": groups_cfg,
    }
    if args.single:
        config["plugin_name"] = f"{prefix}-enconvo"
        config["flatten"] = not args.no_flatten
    if args.agents:
        if not args.single:
            sys.exit("--agents needs --single")
        config["agents"] = {"enabled": True, "title_prefix": args.agents_title or prefix.capitalize()}
    (adapter / "build-config.json").write_text(json.dumps(config, indent=2) + "\n")
    result = generate(config)
    write_support_files(config, result)
    print("\n".join(result["report"]))
    if len(result["versions"]) == 1:
        print(f"build version: {next(iter(result['versions'].values()))}")
    else:
        print("build versions: " + ", ".join(f"{k} {v}" for k, v in result["versions"].items()))
    print(f"marketplace: {result['mkt_dir']}")
    print("\nNext:")
    print(f"  enconvo plugin validate '{result['mkt_dir']}'")
    print(f"  enconvo plugin marketplace add '{result['mkt_dir']}'")
    for p in result["plugins"]:
        print(f"  enconvo plugin install {p['name']}@{config['marketplace_name']}")


def rebuild(config):
    result = generate(config)
    write_support_files(config, result)
    print("\n".join(result["report"]))
    print("build versions: " + ", ".join(f"{k} {v}" for k, v in result["versions"].items()))
    print(f"marketplace: {result['mkt_dir']}")
    return result


def cmd_build(args):
    config_path = Path(args.config).resolve()
    config = check_config(load_json(config_path, "build config"), config_path)
    config["adapter_dir"] = str(config_path.parent)
    rebuild(config)


def enconvo_cli():
    # Documented route first (Installing Plugins: `npx -p @enconvo/api
    # enconvo ...`, or bare `enconvo` inside a project that depends on
    # @enconvo/api); ~/.config/enconvo/bin/enconvo is an environment-specific
    # fallback for Macs where the npx route stalls.
    for cand in (os.environ.get("ENCONVO_CLI"), shutil.which("enconvo"),
                 str(Path("~/.config/enconvo/bin/enconvo").expanduser())):
        if cand and Path(cand).is_file():
            return cand
    sys.exit("enconvo CLI not found (set ENCONVO_CLI, run through the "
             "documented `npx -p @enconvo/api enconvo` route, or add it to PATH)")


def cmd_agents(args):
    config_path = Path(args.config).resolve()
    config = check_config(load_json(config_path, "build config"), config_path)
    if not (config.get("agents") or {}).get("enabled"):
        sys.exit(f"{config_path} has no \"agents\": {{\"enabled\": true}}")
    ag.sync_agents(config_path.parent, config, prune=args.prune, dry_run=args.dry_run, update=args.update)


def cmd_sync(args):
    config_path = Path(args.config).resolve()
    config = check_config(load_json(config_path, "build config"), config_path)
    config["adapter_dir"] = str(config_path.parent)
    for rcfg in repos_of(config):
        repo = Path(rcfg["path"]).resolve()
        print(f"pulling {repo.name}...")
        out = run(["git", "-C", str(repo), "pull", "--ff-only"])
        for line in out.splitlines():
            if line.strip():
                print(f"  {line.strip()}")
    result = rebuild(config)
    agents_on = bool((config.get("agents") or {}).get("enabled"))
    if args.apply:
        cli = enconvo_cli()
        print("\napplying: plugin refresh and update")
        print(run([cli, "plugin", "marketplace", "refresh"]) or "  refreshed")
        for p in result["plugins"]:
            print(run([cli, "plugin", "update", f"{p['name']}@{config['marketplace_name']}"]))
        if agents_on:
            print("\napplying: agents")
            ag.sync_agents(config_path.parent, config)
        return
    print(
        "\nMarketplace rebuilt. To pick the new skills up in Enconvo:\n\n"
        "  enconvo plugin marketplace refresh\n"
        + "\n".join(
            f"  enconvo plugin update {p['name']}@{config['marketplace_name']}"
            for p in result["plugins"]
        )
        + (f"\n  python3 {Path(__file__).resolve()} agents --config {config_path}" if agents_on else "")
        + "\n\n(Or rerun with --apply. Plugins page > ... > Check for updates works"
        "\ntoo. A folder marketplace is read where it is, so Enconvo notices"
        "\nchanged files.)"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="scaffold a new adapter from repo(s)")
    n.add_argument("--repo", required=True, action="append",
                   help="repo URL or local clone path; repeat to combine several repos into one marketplace")
    n.add_argument("--name", help="adapter prefix (default: repo folder name; required for URLs and multi-repo)")
    n.add_argument("--workspace", help="workspace root (default: cwd)")
    n.add_argument("--marketplace", help="marketplace name (default: <prefix>-enconvo)")
    n.add_argument("--single", action="store_true",
                   help="package every repo's skills into one plugin named "
                        "<prefix>-enconvo (the series convention) instead of "
                        "one plugin per group")
    n.add_argument("--groups-file",
                   help='JSON: {group or repo-name: {plugin, description, skills}} overrides; '
                        'a "skills" list explicitly assigns flat-repo skills to groups, '
                        'multi-repo flat repos key by repo folder name')
    n.add_argument("--no-flatten", action="store_true",
                   help="with --single, keep upstream skill nesting (skills/<group>/<name>) instead of "
                        "shipping skills/<name>; needed when two groups share a skill name")
    n.add_argument("--agents", action="store_true",
                   help="also make one Enconvo Agent per agent split (needs --single and groups, "
                        "upstream agents/*.md with skills:, or nested skill folders)")
    n.add_argument("--agents-title", help="title prefix of those Agents (default: the adapter prefix)")
    n.add_argument("--category", default=DEFAULT_CATEGORY)
    n.add_argument("--marketplace-description",
                   help="override the generated marketplace.json description")
    n.add_argument("--owner-name")
    n.add_argument("--owner-url")
    n.set_defaults(func=cmd_new)
    b = sub.add_parser("build", help="rebuild from build-config.json")
    b.add_argument("--config", required=True)
    b.set_defaults(func=cmd_build)
    s = sub.add_parser("sync", help="pull upstream clones and rebuild from build-config.json")
    s.add_argument("--config", required=True)
    s.add_argument("--apply", action="store_true",
                   help="also run enconvo plugin marketplace refresh/update and the agents step")
    s.set_defaults(func=cmd_sync)
    a = sub.add_parser("agents", help="create or refresh the Enconvo Agents of an adapter")
    a.add_argument("--config", required=True)
    a.add_argument("--prune", action="store_true",
                   help="delete Agents whose split is gone from the build (working folders stay)")
    a.add_argument("--dry-run", action="store_true", help="report what would change; change nothing")
    a.add_argument("--update", action="store_true",
                   help="overwrite existing Agents' title, description and prompt with the "
                        "hand-written agent-prompts/<id>.md files")
    a.set_defaults(func=cmd_agents)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
