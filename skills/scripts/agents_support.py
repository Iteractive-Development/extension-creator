"""Agent support for new_plugin.py: Claude Code agent files, skill scoping and
the Enconvo Agents that carry a plugin's skills.

How Enconvo scopes skills to one Agent (verified against its skill catalog):
  * a plugin's skills are global: every Agent gets every enabled one, and an
    Agent's workspace_skills.disabled list only filters skills in its OWN folder;
  * a skill whose SKILL.md says `visibility: scoped` is left out of the global
    list entirely;
  * an Agent sees any skill folder under <its home>/.enconvo/skills/, symlinks
    included.
So the builder marks agent-assigned skills scoped, and `sync_agents` links each
Agent's skills from the INSTALLED plugin folder. A plugin update then reaches
every Agent at once; re-running sync_agents only adds or drops links when the
skill set of an agent changed.
"""

import json
import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path

ACRONYMS = {"ai", "api", "ci", "cd", "cro", "qa", "seo", "sms", "ui", "ux", "ppc"}
SKIP_AGENT_FILES = {"readme.md", "license.md", "changelog.md"}
UNSHIPPED = (
    ("commands", "commands/ folder"),
    ("hooks", "hooks/ folder"),
    (".mcp.json", ".mcp.json"),
    ("mcp.json", "mcp.json"),
    ("settings.json", "settings.json"),
    ("output-styles", "output-styles/ folder"),
)
UNSHIPPED_KEYS = ("commands", "hooks", "mcpServers", "lspServers", "outputStyles", "userConfig", "channels")


# ---------------------------------------------------------------- frontmatter

def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _split_list(v):
    v = v.strip()
    if v.startswith("["):
        v = v[1:]
    if v.endswith("]"):
        v = v[:-1]
    return [_unquote(x) for x in v.split(",") if x.strip()]


def split_frontmatter(text):
    """(frontmatter lines, body) or (None, text) when the file has none."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def parse_frontmatter(text):
    """(fields, body). Reads what Claude Code agent and skill files use: plain
    and quoted scalars, inline lists, block lists, and folded or literal block
    text. Anything fancier is left as raw text; the builder only needs name,
    description and skills."""
    fm, body = split_frontmatter(text)
    if fm is None:
        return {}, text
    fields, i = {}, 0
    while i < len(fm):
        line = fm[i].rstrip("\r")
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        i += 1
        block = []
        while i < len(fm):
            nxt = fm[i].rstrip("\r")
            if nxt.strip() == "" or nxt[:1] in " \t" or (val == "" and nxt.startswith("- ")):
                block.append(nxt)
                i += 1
            else:
                break
        while block and not block[-1].strip():
            block.pop()
        stripped = [b.strip() for b in block]
        if val in (">", ">-", ">+", "|", "|-", "|+"):
            joiner = " " if val.startswith(">") else "\n"
            fields[key] = joiner.join(s for s in stripped if s)
        elif val == "" and any(s.startswith("- ") for s in stripped):
            fields[key] = [_unquote(s[2:]) for s in stripped if s.startswith("- ")]
        elif val.startswith("["):
            joined = val
            for s in stripped:
                if "]" in joined:
                    break
                joined += " " + s
            fields[key] = _split_list(joined)
        elif val == "":
            fields[key] = "\n".join(stripped)
        else:
            fields[key] = _unquote(" ".join([val] + [s for s in stripped if s]))
    return fields, body


def as_list(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return _split_list(v)
    return []


def inject_visibility(text, value="scoped"):
    """Set `visibility: <value>` in a SKILL.md's frontmatter. Returns
    (new_text, status) with status added, replaced, unchanged or no-frontmatter."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text, "no-frontmatter"
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text, "no-frontmatter"
    eol = "\r" if lines[0].endswith("\r") else ""
    for i in range(1, end):
        m = re.match(r"^visibility\s*:\s*(.*?)\s*$", lines[i].rstrip("\r"))
        if m:
            if _unquote(m.group(1)).lower() == value:
                return text, "unchanged"
            lines[i] = f"visibility: {value}{eol}"
            return "\n".join(lines), "replaced"
    lines.insert(end, f"visibility: {value}{eol}")
    return "\n".join(lines), "added"


# ------------------------------------------------------------- repo discovery

def collect_agents(repo, manifest):
    """Claude Code agent files of a repo: the .md files its manifest's `agents`
    field lists (which replaces the folder), else every agents/**/*.md. Returns
    a list of {rel, name, description, skills, body} (rel is repo-relative)."""
    repo = Path(repo)
    listed = manifest.get("agents")
    rels = []
    if isinstance(listed, str):
        listed = [listed]
    if isinstance(listed, list) and listed:
        for e in listed:
            p = (repo / str(e)).resolve()
            if p.is_dir():
                rels += sorted(q.relative_to(repo.resolve()) for q in p.rglob("*.md"))
            elif p.is_file():
                rels.append(p.relative_to(repo.resolve()))
            else:
                sys.exit(f"{repo.name}: manifest lists agent file that does not exist: {e}")
    else:
        rels = sorted(
            p.relative_to(repo) for p in repo.glob("agents/**/*.md")
            if p.name.lower() not in SKIP_AGENT_FILES
        )
    out = []
    for rel in rels:
        text = (repo / rel).read_text(errors="replace")
        fields, body = parse_frontmatter(text)
        name = str(fields.get("name") or Path(rel).stem).strip()
        out.append({
            "rel": Path(rel),
            "name": name,
            "description": str(fields.get("description") or "").strip(),
            "skills": as_list(fields.get("skills")),
            "body": body.strip(),
        })
    return out


def unshipped_parts(repo, manifest):
    """Plugin parts the builder does not carry, so nothing drops silently."""
    repo = Path(repo)
    found = [label for rel, label in UNSHIPPED if (repo / rel).exists()]
    found += [f"manifest {k}" for k in UNSHIPPED_KEYS if manifest.get(k)]
    return found


# ----------------------------------------------------------------- agent maps

def pretty(name, overrides=None):
    overrides = overrides or {}
    if name in overrides:
        return overrides[name]
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(w.upper() if w.lower() in ACRONYMS else w.capitalize() for w in words if w)


def load_prompt_files(adapter):
    """Hand-written Agent prompts: <adapter>/agent-prompts/<id>.md, a file with
    `title` and `description` frontmatter and the system prompt as its body.
    They replace the generated title, description and prompt of that Agent.
    Returns {id: {title, description, prompt}}."""
    out = {}
    if not adapter:
        return out
    folder = Path(adapter) / "agent-prompts"
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.md")):
        fields, body = parse_frontmatter(f.read_text(errors="replace"))
        body = body.strip()
        if not body:
            sys.exit(f"{f}: the prompt body is empty")
        out[f.stem] = {
            "title": str(fields.get("title") or "").strip(),
            "description": str(fields.get("description") or "").strip(),
            "prompt": body,
        }
    return out


def prompt_skill_mismatch(prompt, skills):
    """Skills named in backticks under `# Your skills` versus the skills the
    Agent owns. Returns (missing, extra)."""
    m = re.search(r"^#\s+Your skills\s*$(.*?)(?=^#\s|\Z)", prompt, re.S | re.M)
    if not m:
        return sorted(skills), []
    named = set(re.findall(r"^\s*[-*]\s+`([^`]+)`", m.group(1), re.M))
    return sorted(set(skills) - named), sorted(named - set(skills))


def make_agent_defs(config, explicit, shipped_agents, nesting, shipped_names):
    """The Enconvo Agents a build describes, as a list of dicts.
    Source, in priority order: the build-config groups (explicit: group ->
    skill names), the upstream agents' own `skills:` lists, else the skill
    folders' nesting upstream (nesting: group -> skill names)."""
    acfg = config.get("agents") or {}
    prefix = acfg.get("title_prefix") or str(config["prefix"]).capitalize()
    overrides = acfg.get("title_overrides") or {}
    raw_groups = config.get("groups") or {}
    names = set(shipped_names)
    written = load_prompt_files(config.get("adapter_dir"))
    defs = []

    def title_of(label):
        return f"{prefix} {pretty(label, overrides)}".strip()

    if explicit:
        source, items = "groups", [(g, sorted(set(v)), None) for g, v in explicit.items()]
    elif any(a["skills"] for a in shipped_agents):
        source = "agents"
        items = [(a["name"], a["skills"], a) for a in shipped_agents]
    elif nesting:
        source, items = "nesting", [(g, sorted(set(v)), None) for g, v in nesting.items()]
    else:
        return []
    seen_ids = set()
    for label, skills, upstream in items:
        unknown = sorted(set(skills) - names)
        if unknown:
            print(f"warning: agent '{label}' names skills this plugin does not ship: {', '.join(unknown)}")
        skills = sorted(set(skills) & names)
        if label in seen_ids:
            sys.exit(f"two agents are both called '{label}'")
        seen_ids.add(label)
        title = title_of(label)
        desc = ""
        if upstream:
            desc = upstream["description"]
        elif isinstance(raw_groups.get(label), dict):
            desc = raw_groups[label].get("description", "")
        if not desc:
            desc = f"{title}: {', '.join(skills)}." if skills else title
        if upstream and upstream["body"]:
            prompt = upstream["body"]
        else:
            prompt = (
                f"You are {title}. Your skills are: {', '.join(skills)}. "
                "Use them for the work they cover. When a request needs a skill you "
                "do not have, say so and name the kind of agent that should take it, "
                "rather than improvising."
            )
        hand = written.get(label)
        if hand:
            title = hand["title"] or title
            desc = hand["description"] or desc
            prompt = hand["prompt"]
            missing, extra = prompt_skill_mismatch(prompt, skills)
            if missing or extra:
                print(f"warning: agent-prompts/{label}.md and the agent map disagree: "
                      f"prompt lacks {missing or 'none'}, names unowned {extra or 'none'}")
        defs.append({"id": label, "title": title, "description": desc,
                     "prompt": prompt, "skills": skills, "source": source,
                     "written": bool(hand)})
    for fid in sorted(set(written) - seen_ids):
        print(f"warning: agent-prompts/{fid}.md matches no agent in this build (typo?)")
    taken = {}
    for d in defs:
        taken.setdefault(d["title"], []).append(d["id"])
    for t, ids in taken.items():
        if len(ids) > 1:
            sys.exit(f"agents {ids} would share the title '{t}'; set agents.title_overrides")
    return defs


# ------------------------------------------------------------------ Enconvo API

def api_base():
    return os.environ.get("ENCONVO_API", "http://localhost:54535/api/")


def extension_dir():
    return Path(os.environ.get("ENCONVO_EXTENSION_DIR", "~/.config/enconvo/extension")).expanduser()


def api_post(route, body):
    req = urllib.request.Request(
        api_base() + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=120) as r:
            raw = r.read().decode() or "null"
    except Exception as e:  # noqa: BLE001 - one clean message for any transport error
        sys.exit(f"Enconvo's local API is not reachable ({api_base()}{route}): {e}. Is Enconvo running?")
    return json.loads(raw)


def _link_target_ok(link, skills_root):
    try:
        return os.readlink(link).startswith(str(skills_root) + os.sep)
    except OSError:
        return False


def link_skills(workspace, skills_root, wanted, installed, say):
    """Make <workspace>/.enconvo/skills hold exactly the wanted skills of this
    plugin as symlinks into the installed plugin. Only links that point into
    the plugin's skills folder are ever changed or removed.
    Returns (added, removed, kept)."""
    sdir = Path(workspace) / ".enconvo" / "skills"
    sdir.mkdir(parents=True, exist_ok=True)
    added = removed = kept = 0
    for name in sorted(wanted):
        if name not in installed:
            say(f"  warning: skill '{name}' is not in the installed plugin yet (run enconvo plugin update)")
            continue
        link, target = sdir / name, skills_root / name
        if link.is_symlink():
            if os.readlink(link) == str(target):
                kept += 1
                continue
            if _link_target_ok(link, skills_root):
                link.unlink()
            else:
                say(f"  warning: {link} is a link to somewhere else; left alone")
                continue
        elif link.exists():
            say(f"  warning: {link} is a real folder, not a link; left alone")
            continue
        os.symlink(target, link)
        added += 1
    for entry in sorted(sdir.iterdir()):
        if entry.is_symlink() and _link_target_ok(entry, skills_root) and entry.name not in wanted:
            entry.unlink()
            removed += 1
    return added, removed, kept


def clear_legacy_disabled(key, say):
    """Agents made by an earlier script carry workspace_skills.disabled lists of
    plugin skill ids (extension:...). Enconvo never reads those for plugin
    skills, so they only mislead: drop them."""
    cur = api_post("config/get", {"preferenceKey": key, "includes": ["workspace_skills"]}) or {}
    ws = cur.get("workspace_skills") or {}
    disabled = ws.get("disabled") or []
    kept = [d for d in disabled if not str(d).startswith("extension:")]
    if len(kept) != len(disabled):
        api_post("config/set", {"preferenceKey": key, "keys": ["workspace_skills"],
                               "value": {**ws, "disabled": kept}})
        say(f"  cleared {len(disabled) - len(kept)} ineffective plugin ids from the disabled list")


def refresh_agent(key, ag, listed, plugin_dir, update, dry_run, say):
    """Compare a live Agent with its written title, description and prompt; with
    update, set what differs. Returns True when anything differed."""
    want_prompt = ag["prompt"].replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_dir))
    cur = api_post("config/get", {"preferenceKey": key, "includes": ["description", "prompt"]}) or {}
    diffs = []
    if (listed.get("title") or "") != ag["title"]:
        diffs.append("title")
    if (cur.get("description") or "") != ag["description"] or (listed.get("description") or "") != ag["description"]:
        diffs.append("description")
    if (cur.get("prompt") or "") != want_prompt:
        diffs.append("prompt")
    if not diffs:
        return False
    if not update or dry_run:
        say(f"  {'would update' if update else 'differs from the written file'}: {', '.join(diffs)}")
        return True
    if "title" in diffs:
        api_post("enconvo/update_agent_profile", {"agentId": key, "name": ag["title"]})
    updates = []
    if "description" in diffs:
        updates.append({"keys": ["description"], "value": ag["description"]})
    if "prompt" in diffs:
        updates.append({"keys": ["prompt"], "value": want_prompt})
    if updates:
        api_post("enconvo/update_command_config", {"updateCommandKey": key, "updates": updates})
    if "description" in diffs:
        api_post("search/upsert_command", {"commandKey": key, "updateFields": {"description": ag["description"]}})
    say(f"  updated: {', '.join(diffs)}")
    return True


def sync_agents(adapter, config, prune=False, dry_run=False, update=False, say=print):
    """Create, adopt and refresh the Agents of an adapter's agent-map.json and
    link each Agent's skills from the installed plugin. Idempotent: a second run
    changes nothing. An existing Agent's title, description and prompt are the
    user's: they are overwritten only with update=True (--update), which applies
    the hand-written agent-prompts/<id>.md files; without it differences are
    only reported. Everything else about an Agent is never touched."""
    adapter = Path(adapter)
    map_path = adapter / "agent-map.json"
    if not map_path.is_file():
        sys.exit(f"{map_path} not found: enable agents in build-config.json, then run build or sync")
    amap = json.loads(map_path.read_text())
    plugin = amap["plugin"]
    ext = extension_dir()
    skills_root = ext / plugin / "skills"
    if not skills_root.is_dir():
        sys.exit(
            f"{plugin} is not installed (no {skills_root}); run: enconvo plugin marketplace add "
            f"'{adapter / 'marketplace'}' && enconvo plugin install {plugin}@{amap['marketplace']}"
        )
    installed = {p.name for p in skills_root.iterdir() if (p / "SKILL.md").is_file()}
    state_path = adapter / "agents-state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"agents": {}}
    listing = api_post("agent/list", {"includePaused": True, "includeUnavailable": True}) or []
    by_key = {a.get("command_key"): a for a in listing}
    by_title = {}
    for a in listing:
        by_title.setdefault(a.get("title"), []).append(a.get("command_key"))

    summary = {"created": 0, "adopted": 0, "linked": 0, "unlinked": 0,
               "updated": 0, "drifted": 0, "orphans": []}
    for ag in amap["agents"]:
        say(f"{ag['title']}")
        key = state["agents"].get(ag["id"])
        fresh = False
        if key and key not in by_key:
            say("  its Agent was deleted; making a new one")
            key = None
        if not key:
            matches = by_title.get(ag["title"], [])
            if len(matches) > 1:
                sys.exit(f"{len(matches)} Agents are already titled '{ag['title']}'; rename the extras")
            if matches:
                key = matches[0]
                summary["adopted"] += 1
                say(f"  adopted the existing Agent {key}")
        if not key:
            if dry_run:
                say("  would create the Agent")
                continue
            prompt = ag["prompt"].replace("${CLAUDE_PLUGIN_ROOT}", str(ext / plugin))
            res = api_post("agent/create", {
                "agent_title": ag["title"], "agent_description": ag["description"],
                "agent_prompt": prompt, "agent_command_type": "agent",
            })
            if not isinstance(res, dict) or not res.get("commandKey"):
                sys.exit(f"creating '{ag['title']}' failed: {res}")
            key = res["commandKey"]
            fresh = True
            summary["created"] += 1
            say(f"  created {key}")
        state["agents"][ag["id"]] = key
        if not fresh and ag.get("written"):
            if refresh_agent(key, ag, by_key.get(key) or {}, ext / plugin, update, dry_run, say):
                summary["updated" if update and not dry_run else "drifted"] += 1
        cat = api_post("search/skill_catalog", {"agent_command_key": key})
        workspace = (cat or {}).get("workspace")
        if not workspace:
            sys.exit(f"no working folder reported for {key}")
        if dry_run:
            say(f"  would link {len(ag['skills'])} skills in {workspace}")
            continue
        added, removed, kept = link_skills(workspace, skills_root, ag["skills"], installed, say)
        summary["linked"] += added
        summary["unlinked"] += removed
        say(f"  skills: {len(ag['skills'])} ({added} linked, {removed} unlinked, {kept} already in place)")
        clear_legacy_disabled(key, say)

    wanted_ids = {ag["id"] for ag in amap["agents"]}
    for aid, key in sorted(state["agents"].items()):
        if aid in wanted_ids:
            continue
        summary["orphans"].append(aid)
        if prune and not dry_run:
            api_post("agent/delete", {"agentId": key})
            del state["agents"][aid]
            say(f"pruned '{aid}' ({key}); its working folder stays on disk")
        else:
            say(f"orphan: '{aid}' ({key}) is no longer in the agent map; --prune deletes it")
    if not dry_run:
        state_path.write_text(json.dumps(state, indent=2) + "\n")
    say(
        f"agents: {summary['created']} created, {summary['adopted']} adopted, "
        f"{summary['linked']} links added, {summary['unlinked']} removed, "
        f"{summary['updated']} updated, {summary['drifted']} differ from their written prompt file "
        f"(--update applies it), {len(summary['orphans'])} orphan(s)"
    )
    return summary
