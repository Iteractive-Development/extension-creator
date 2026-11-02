#!/usr/bin/env python3
"""Regression suite for the repo-to-plugin builder: mirrors the adversarial
review's attacks. Runs entirely under /tmp/r2p-verify."""
import filecmp
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BUILDER = "/Users/bradhallett/Documents/Agent Plugins/.enconvo/skills/repo-to-plugin/scripts/new_plugin.py"
ROOT = Path("/tmp/r2p-verify")
FAILS = []


def sh(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def build(config):
    return sh("python3", BUILDER, "build", "--config", str(config))


def case(name, ok, evidence=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{evidence}]" if evidence else ""))
    if not ok:
        FAILS.append(name)


def skill(repo, group, name, text="does things", extra_files=None):
    d = repo / "skills" / group / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Use for {name} work.\n---\n{text}\n")
    for fn, content in (extra_files or {}).items():
        (d / fn).write_text(content)
    return d


def fresh():
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True)


# --- setup: one flat repo (listed manifest), one nested repo, groups file ---
fresh()
flat = ROOT / "flatrepo"
(flat / "skills").mkdir(parents=True)
(flat / ".claude-plugin").mkdir()
for n in ("alpha", "beta"):
    skill(flat, "", n)
(flat / ".claude-plugin/plugin.json").write_text(json.dumps({
    "name": "flatrepo", "version": "1.0.0", "license": "MIT",
    "skills": ["./skills/alpha", "./skills/beta"],
}))

nest = ROOT / "nestrepo"
(nest / "skills").mkdir(parents=True)
(nest / ".claude-plugin").mkdir(parents=True)
for g in ("g1", "g2"):
    for n in ("one", "two"):
        skill(nest, g, n)
(nest / ".claude-plugin/plugin.json").write_text(json.dumps({
    "name": "nestrepo", "version": "2.0.0", "license": "MIT",
    "author": {"name": "Nest Author"},
    "skills": ["./skills/g1/one", "./skills/g1/two", "./skills/g2/one", "./skills/g2/two"],
}))

groups = ROOT / "groups.json"
groups.write_text(json.dumps({
    "a-team": {"skills": ["alpha"]},
    "b-team": {"skills": ["beta"], "catch_all": True},
}))
r = sh("python3", BUILDER, "new", "--repo", str(flat), "--name", "t1", "--workspace", str(ROOT), "--groups-file", str(groups))
case("new: flat repo with explicit groups builds", r.returncode == 0 and "t1-a-team: 1 skill," in r.stdout, r.stdout.strip().splitlines()[0] if r.stdout else r.stderr[:80])
case("new: singular '1 skill' phrasing", "1 skill," in r.stdout)
mkt = ROOT / "t1-enconvo/marketplace"
case("new: catch_all group exists", (mkt / "plugins/t1-b-team/skills/beta/SKILL.md").is_file())

# --- 1. failed build must leave the marketplace intact ---
before = sorted(str(p.relative_to(mkt)) for p in mkt.rglob("*"))
(flat / "skills/alpha/SKILL.md").unlink()  # listed skill dir now empty-ish -> manifest lists dir that still exists
shutil.rmtree(flat / "skills/alpha")
r = build(ROOT / "t1-enconvo/build-config.json")
after = sorted(str(p.relative_to(mkt)) for p in mkt.rglob("*"))
case("missing listed skill: clean error", r.returncode == 1 and "missing on disk" in (r.stdout + r.stderr) and "Traceback" not in r.stderr, (r.stdout + r.stderr).strip().splitlines()[0])
case("missing listed skill: marketplace untouched", before == after)
case("missing listed skill: no staging leftovers", not (ROOT / "t1-enconvo/.marketplace-new").exists() and not (ROOT / "t1-enconvo/.marketplace-old").exists())
# restore
skill(flat, "", "alpha")

# --- 2. new on-disk skill not in manifest list -> warning, not silent ---
skill(flat, "", "gamma")
r = build(ROOT / "t1-enconvo/build-config.json")
case("unlisted on-disk skill: warns", r.returncode == 0 and "left out" in r.stdout and "gamma" in r.stdout, (r.stdout.strip().splitlines() or ["?"])[0])
case("unlisted on-disk skill: not shipped", not any("gamma" in str(p) for p in mkt.rglob("SKILL.md")))
shutil.rmtree(flat / "skills/gamma")

# --- 3. catch_all absorbs a newly added listed skill? (listed manifests win; add to disk+manifest) ---
(flat / ".claude-plugin/plugin.json").write_text(json.dumps({
    "name": "flatrepo", "version": "1.1.0", "license": "MIT",
    "skills": ["./skills/alpha", "./skills/beta", "./skills/delta"],
}))
skill(flat, "", "delta")
r = build(ROOT / "t1-enconvo/build-config.json")
case("new listed skill lands in its folder", (mkt / "plugins/t1-a-team/skills/alpha/SKILL.md").is_file(), "")

# --- 4. explicit-groups error keeps marketplace intact (no catch_all case) ---
groups.write_text(json.dumps({"a-team": {"skills": ["alpha", "beta"]}}))
sh("python3", BUILDER, "new", "--repo", str(flat), "--name", "t2", "--workspace", str(ROOT), "--groups-file", str(groups))
mkt2 = ROOT / "t2-enconvo/marketplace"
skill(flat, "", "epsilon")
(flat / ".claude-plugin/plugin.json").write_text(json.dumps({
    "name": "flatrepo", "version": "1.2.0", "license": "MIT",
    "skills": ["./skills/alpha", "./skills/beta", "./skills/epsilon"],
}))
before2 = sorted(str(p.relative_to(mkt2)) for p in mkt2.rglob("*"))
r = build(ROOT / "t2-enconvo/build-config.json")
after2 = sorted(str(p.relative_to(mkt2)) for p in mkt2.rglob("*"))
case("unassigned skill, no catch_all: clean error", r.returncode == 1 and "not assigned to any explicit group" in r.stdout + r.stderr)
case("unassigned skill, no catch_all: marketplace intact", before2 == after2)

# --- 5. bad inputs -> clean errors ---
bad = ROOT / "bad.json"
bad.write_text("{not json")
r = sh("python3", BUILDER, "new", "--repo", str(flat), "--name", "t3", "--workspace", str(ROOT), "--groups-file", str(bad))
case("invalid groups JSON: clean error", r.returncode == 1 and "not valid JSON" in r.stdout + r.stderr and "Traceback" not in r.stderr)
bad.write_text('["a", "b"]')
r = sh("python3", BUILDER, "new", "--repo", str(flat), "--name", "t3", "--workspace", str(ROOT), "--groups-file", str(bad))
case("groups JSON list: clean error", r.returncode == 1 and "JSON object" in r.stdout + r.stderr and "Traceback" not in r.stderr)
r = sh("python3", BUILDER, "build", "--config", str(ROOT / "nope.json"))
case("missing config: clean error", r.returncode == 1 and "not found" in r.stdout + r.stderr)
broken = ROOT / "broken-config.json"
broken.write_text(json.dumps({"prefix": "x"}))
r = sh("python3", BUILDER, "build", "--config", str(broken))
case("config missing keys: clean error", r.returncode == 1 and "marketplace_name" in r.stdout + r.stderr)

# --- 6. unknown groups key -> warning ---
(flat / ".claude-plugin/plugin.json").write_text(json.dumps({
    "name": "flatrepo", "version": "1.3.0", "license": "MIT",
    "skills": ["./skills/alpha", "./skills/beta", "./skills/epsilon"],
}))
groups.write_text(json.dumps({"a-team": {"skills": ["alpha", "beta", "epsilon"]}, "typo-key": {"description": "x"}}))
sh("python3", BUILDER, "new", "--repo", str(flat), "--name", "t4", "--workspace", str(ROOT), "--groups-file", str(groups))
r = build(ROOT / "t4-enconvo/build-config.json")
case("unknown groups key: warns", "matched no group" in r.stdout and "typo-key" in r.stdout)

# --- 7. template dirs are left out and reported ---
skill(flat, "", "scaffold", extra_files={})
(ROOT / flat / "skills/scaffold").exists()
tmpl_dir = flat / "skills" / "scaffold"
tmpl_dir.rename(flat / "skills" / "template")
r = build(ROOT / "t4-enconvo/build-config.json")
case("template dir: reported left out", "template" in r.stdout and "left out" in r.stdout)
shutil.rmtree(flat / "skills/template")

# --- 8. nested repo + determinism + marketplace swap ---
r = sh("python3", BUILDER, "new", "--repo", str(nest), "--name", "t5", "--workspace", str(ROOT))
case("nested repo builds two plugins", r.returncode == 0 and "t5-g1: 2 skills" in r.stdout and "t5-g2: 2 skills" in r.stdout)
mkt5 = ROOT / "t5-enconvo/marketplace"
snap1 = {str(p.relative_to(mkt5)): p.read_bytes() for p in mkt5.rglob("*") if p.is_file()}
r = build(ROOT / "t5-enconvo/build-config.json")
snap2 = {str(p.relative_to(mkt5)): p.read_bytes() for p in mkt5.rglob("*") if p.is_file()}
case("determinism: rebuild byte-identical", snap1 == snap2 and len(snap1) >= 7, f"{len(snap1)} files")
case("swap leaves no .marketplace-* dirs", not any(ROOT.joinpath("t5-enconvo", d).exists() for d in (".marketplace-new", ".marketplace-old")))

# --- 9. links with spaces / %20 copied cross-group ---
nest2 = ROOT / "nest2"
(nest2 / "skills").mkdir(parents=True)
shared = nest2 / "shared-notes.md"
shared.write_text("# shared\n")
skill(nest2, "g1", "one", text="see [notes](<../shared notes.md>) and [enc](../enc%20file.md)")
skill(nest2, "g2", "two")
(nest2 / "skills" / "g1" / "enc file.md").write_text("enc")
sh("python3", BUILDER, "new", "--repo", str(nest2), "--name", "t6", "--workspace", str(ROOT))
case("space/%20 links copied", (ROOT / "t6-enconvo/marketplace/plugins/t6-g1/skills/g1/enc file.md").is_file(), "+1 linked" in r.stdout)

# --- 10. duplicate skill names across repos warn ---
r = sh("python3", BUILDER, "new", "--repo", str(flat), "--repo", str(nest), "--name", "t7", "--workspace", str(ROOT))
case("duplicate skill names across repos: warns", r.returncode == 0 and "ships in both" in r.stdout)

# --- 11. single-plugin mode: one plugin, all skills at upstream paths ---
(flat / "LICENSE").write_text("MIT flat\n")
(nest / "LICENSE").write_text("MIT nest\n")
r = sh("python3", BUILDER, "new", "--repo", str(nest), "--name", "t8", "--workspace", str(ROOT), "--single", "--no-flatten")
case("single: builds one plugin", r.returncode == 0 and "t8-enconvo: 4 skills" in r.stdout, (r.stdout.strip().splitlines() or ["?"])[0])
sroot = ROOT / "t8-enconvo/marketplace"
pm = json.loads((sroot / "plugins/t8-enconvo/.claude-plugin/plugin.json").read_text())
case("single: manifest carries all upstream paths", sorted(pm["skills"]) == ["./skills/g1/one", "./skills/g1/two", "./skills/g2/one", "./skills/g2/two"])
mj = json.loads((sroot / ".claude-plugin/marketplace.json").read_text())
case("single: marketplace named after the plugin, one entry", mj["name"] == "t8-enconvo" and len(mj["plugins"]) == 1)
case("single: nested layout flagged in README", "plugin_runtime_surface_missing" in (ROOT / "t8-enconvo/README.md").read_text())
case("single: single-repo keeps the upstream author", pm["author"]["name"] == "Nest Author")

# --- 12. multi-repo single merge: owner author, LICENSE-<repo> files ---
r = sh("python3", BUILDER, "new", "--repo", str(flat), "--repo", str(nest), "--name", "t9", "--workspace", str(ROOT), "--single", "--no-flatten")
mroot = ROOT / "t9-enconvo/marketplace"
pm = json.loads((mroot / "plugins/t9-enconvo/.claude-plugin/plugin.json").read_text())
case("single merge: every skill in one plugin", r.returncode == 0 and len(pm["skills"]) == 7, f"{len(pm['skills'])} skills")
case("single merge: multi-repo credits the owner", pm["author"]["name"] == "Iteractive Development")
case("single merge: LICENSE-<repo> files copied", (mroot / "plugins/t9-enconvo/LICENSE-flatrepo").is_file() and (mroot / "plugins/t9-enconvo/LICENSE-nestrepo").is_file())

# --- 13. single-mode path collision: clean error ---
coll_a = ROOT / "colla"
(coll_a / "skills/overlap").mkdir(parents=True)
(coll_a / "skills/overlap/SKILL.md").write_text("---\nname: overlap\n---\n")
(coll_a / ".claude-plugin").mkdir()
(coll_a / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "colla", "version": "1.0.0", "skills": ["./skills/overlap"]}))
coll_b = ROOT / "collb"
(coll_b / "skills/overlap").mkdir(parents=True)
(coll_b / "skills/overlap/SKILL.md").write_text("---\nname: overlap\n---\n")
(coll_b / ".claude-plugin").mkdir()
(coll_b / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "collb", "version": "1.0.0", "skills": ["./skills/overlap"]}))
r = sh("python3", BUILDER, "new", "--repo", str(coll_a), "--repo", str(coll_b), "--name", "t10", "--workspace", str(ROOT), "--single", "--no-flatten")
case("single merge: path collision clean error", r.returncode == 1 and "more than one repo" in r.stdout + r.stderr and "Traceback" not in r.stderr)

# --- 14. single mode + groups: skills nest by group inside ONE plugin, cross-group links follow ---
gr = ROOT / "grouprepo"
(gr / "skills").mkdir(parents=True)
(gr / ".claude-plugin").mkdir()
skill(gr, "", "red", text="see [blue](../blue/SKILL.md) and [note](../shared.md)")
skill(gr, "", "blue", text="back to [red](../red/SKILL.md)")
(gr / "skills" / "shared.md").write_text("shared\n")
(gr / ".claude-plugin/plugin.json").write_text(json.dumps({
    "name": "grouprepo", "version": "1.0.0", "license": "MIT",
    "skills": ["./skills/red", "./skills/blue"],
}))
groups2 = ROOT / "groups2.json"
groups2.write_text(json.dumps({
    "ops": {"skills": ["red"]},
    "ui": {"skills": ["blue"]},
}))
r = sh("python3", BUILDER, "new", "--repo", str(gr), "--name", "t11", "--workspace", str(ROOT), "--single", "--no-flatten", "--groups-file", str(groups2))
pdir = ROOT / "t11-enconvo/marketplace/plugins/t11-enconvo"
pm = json.loads((pdir / ".claude-plugin/plugin.json").read_text()) if r.returncode == 0 else {"skills": []}
case("grouped single: one plugin, skills nest by group", r.returncode == 0 and (pdir / "skills/ops/red/SKILL.md").is_file() and (pdir / "skills/ui/blue/SKILL.md").is_file() and not (pdir / "skills/red").exists(), r.stdout.strip().splitlines()[0] if r.stdout else r.stderr[:120])
case("grouped single: manifest lists grouped paths", sorted(pm["skills"]) == ["./skills/ops/red", "./skills/ui/blue"], str(pm["skills"]))
red_text = (pdir / "skills/ops/red/SKILL.md").read_text() if (pdir / "skills/ops/red/SKILL.md").is_file() else ""
blue_text = (pdir / "skills/ui/blue/SKILL.md").read_text() if (pdir / "skills/ui/blue/SKILL.md").is_file() else ""
case("grouped single: cross-group links rewritten", "../../ui/blue/SKILL.md" in red_text and "../../shared.md" in red_text, red_text.strip().splitlines()[-1] if red_text else "no file")
case("grouped single: link back rewritten", "../../ops/red/SKILL.md" in blue_text, blue_text.strip().splitlines()[-1] if blue_text else "no file")
case("grouped single: rewritten links resolve", all(((pdir / "skills/ops/red") / p).resolve().exists() for p in ("../../ui/blue/SKILL.md", "../../shared.md")) and ((pdir / "skills/ui/blue") / "../../ops/red/SKILL.md").resolve().exists())
# --- 15. links into repo-root folders (tools/) ship at their repo-relative path; unlinked files do not ---
tr = ROOT / "toolsrepo"
(tr / "skills").mkdir(parents=True)
(tr / "tools/integrations").mkdir(parents=True)
(tr / ".claude-plugin").mkdir()
skill(tr, "", "seo", text="see [ahrefs](../../tools/integrations/ahrefs.md)")
(tr / "tools/integrations/ahrefs.md").write_text("ahrefs guide\n")
(tr / "tools/integrations/unused.md").write_text("not linked\n")
(tr / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "toolsrepo", "version": "1.0.0", "license": "MIT", "skills": ["./skills/seo"]}))
groups3 = ROOT / "groups3.json"
groups3.write_text(json.dumps({"growth": {"skills": ["seo"]}}))
r = sh("python3", BUILDER, "new", "--repo", str(tr), "--name", "t12", "--workspace", str(ROOT), "--single", "--no-flatten", "--groups-file", str(groups3))
pdir3 = ROOT / "t12-enconvo/marketplace/plugins/t12-enconvo"
seo_text = (pdir3 / "skills/growth/seo/SKILL.md").read_text() if (pdir3 / "skills/growth/seo/SKILL.md").is_file() else ""
case("repo-root link: target ships at repo-relative path", r.returncode == 0 and (pdir3 / "tools/integrations/ahrefs.md").is_file() and not (pdir3 / "tools/integrations/unused.md").exists(), r.stderr[:120] if r.returncode else "")
case("repo-root link: rewritten link resolves after grouping", "../../../tools/integrations/ahrefs.md" in seo_text and ((pdir3 / "skills/growth/seo") / "../../../tools/integrations/ahrefs.md").resolve().exists(), seo_text.strip().splitlines()[-1] if seo_text else "no file")
# --- 16. flatten: nested upstream skills ship at skills/<name>; links follow; the agent map is kept but not shipped ---
fl = ROOT / "flatsrc"
(fl / "skills/design").mkdir(parents=True)
(fl / "skills/plan").mkdir(parents=True)
(fl / ".claude-plugin").mkdir()
skill(fl, "design", "red", text="see [blue](../../plan/blue/SKILL.md)")
skill(fl, "plan", "blue", text="back to [red](../../design/red/SKILL.md) and [note](../../shared.md)")
(fl / "skills/shared.md").write_text("shared\n")
(fl / ".claude-plugin/plugin.json").write_text(json.dumps({
    "name": "flatsrc", "version": "1.0.0", "license": "MIT",
    "skills": ["./skills/design/red", "./skills/plan/blue"],
}))
r = sh("python3", BUILDER, "new", "--repo", str(fl), "--name", "t13", "--workspace", str(ROOT), "--single", "--no-flatten")
cfg13 = ROOT / "t13-enconvo/build-config.json"
c13 = json.loads(cfg13.read_text())
c13["flatten"] = True
c13["groups"] = {"design": {"skills": ["red"]}, "plan": {"skills": ["blue"]}}
cfg13.write_text(json.dumps(c13, indent=2))
r2 = build(cfg13)
p13 = ROOT / "t13-enconvo/marketplace/plugins/t13-enconvo"
case("flatten: nested skills ship at skills/<name>", r2.returncode == 0 and (p13 / "skills/red/SKILL.md").is_file() and (p13 / "skills/blue/SKILL.md").is_file() and not (p13 / "skills/design").exists() and not (p13 / "skills/plan").exists(), r2.stderr.strip()[:160])
m13 = json.loads((p13 / ".claude-plugin/plugin.json").read_text()) if r2.returncode == 0 else {"skills": []}
case("flatten: manifest lists flat paths", sorted(m13["skills"]) == ["./skills/blue", "./skills/red"], str(m13["skills"]))
red13 = (p13 / "skills/red/SKILL.md").read_text() if (p13 / "skills/red/SKILL.md").is_file() else ""
blue13 = (p13 / "skills/blue/SKILL.md").read_text() if (p13 / "skills/blue/SKILL.md").is_file() else ""
case("flatten: links rewritten and resolving", "../blue/SKILL.md" in red13 and "../shared.md" in blue13 and ((p13 / "skills/red") / "../blue/SKILL.md").resolve().exists() and ((p13 / "skills/blue") / "../shared.md").resolve().exists(), red13.strip().splitlines()[-1] if red13 else "no file")

# --- 17. flatten: two skills with one name is a clean error ---
fc = ROOT / "flatclash"
(fc / "skills/a/dup").mkdir(parents=True)
(fc / "skills/b/dup").mkdir(parents=True)
(fc / ".claude-plugin").mkdir()
skill(fc, "a", "dup")
skill(fc, "b", "dup")
(fc / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "flatclash", "version": "1.0.0", "license": "MIT", "skills": ["./skills/a/dup", "./skills/b/dup"]}))
sh("python3", BUILDER, "new", "--repo", str(fc), "--name", "t14", "--workspace", str(ROOT), "--single", "--no-flatten")
cfg14 = ROOT / "t14-enconvo/build-config.json"
c14 = json.loads(cfg14.read_text())
c14["flatten"] = True
cfg14.write_text(json.dumps(c14, indent=2))
r4 = build(cfg14)
case("flatten: name clash is a clean error", r4.returncode == 1 and "flatten:" in (r4.stdout + r4.stderr) and "Traceback" not in r4.stderr, (r4.stdout + r4.stderr).strip()[:160])

# =====================  agents: Claude Code agent/skill combo plugins  =====================
import http.server
import threading


class FakeEnconvo(http.server.BaseHTTPRequestHandler):
    agents = {}   # command_key -> {title, workspace}
    deleted = []
    counter = [0]
    disabled = {}
    base = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        route = self.path.split("/api/", 1)[1]
        cls = FakeEnconvo
        if route == "agent/list":
            out = [{"command_key": k, "title": v["title"], "description": v.get("description", "")} for k, v in cls.agents.items()]
        elif route == "agent/create":
            cls.counter[0] += 1
            key = f"agent|fake_{cls.counter[0]}"
            ws = cls.base / f"agent-{cls.counter[0]}"
            ws.mkdir(parents=True)
            cls.agents[key] = {"title": body["agent_title"], "workspace": str(ws), "prompt": body["agent_prompt"],
                               "description": body.get("agent_description", "")}
            out = {"success": True, "commandKey": key}
        elif route == "search/skill_catalog":
            out = {"workspace": cls.agents[body["agent_command_key"]]["workspace"]}
        elif route == "agent/delete":
            cls.deleted.append(body["agentId"])
            cls.agents.pop(body["agentId"], None)
            out = {"success": True}
        elif route == "config/get":
            ag_ = cls.agents.get(body["preferenceKey"], {})
            out = {"workspace_skills": {"disabled": cls.disabled.get(body["preferenceKey"], [])},
                   "prompt": ag_.get("prompt", ""), "description": ag_.get("description", "")}
        elif route == "enconvo/update_agent_profile":
            cls.agents[body["agentId"]]["title"] = body["name"]
            out = {"success": True}
        elif route == "enconvo/update_command_config":
            for u in body["updates"]:
                cls.agents[body["updateCommandKey"]][u["keys"][0]] = u["value"]
            out = {"success": True}
        elif route == "search/upsert_command":
            cls.agents[body["commandKey"]].update(body["updateFields"])
            out = {"success": True}
        elif route == "config/set":
            cls.disabled[body["preferenceKey"]] = body["value"]["disabled"]
            out = {"success": True}
        else:
            self.send_response(404); self.end_headers(); return
        raw = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(raw)


def fake_api(base):
    FakeEnconvo.agents, FakeEnconvo.deleted, FakeEnconvo.disabled = {}, [], {}
    FakeEnconvo.counter[0] = 0
    FakeEnconvo.base = base
    base.mkdir(parents=True, exist_ok=True)
    srv = http.server.HTTPServer(("127.0.0.1", 0), FakeEnconvo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/api/"


# --- 18. Claude Code plugin with agents/*.md naming skills ---
cc = ROOT / "ccrepo"
(cc / ".claude-plugin").mkdir(parents=True)
(cc / "agents").mkdir()
(cc / "tools").mkdir()
(cc / "tools" / "helper.md").write_text("# helper\n")
(cc / "LICENSE").write_text("MIT cc\n")
for n in ("audit", "fix", "report", "spare"):
    skill(cc, "", n, text="see [helper](../../tools/helper.md)" if n == "audit" else "x")
(cc / "agents" / "reviewer.md").write_text(
    "---\nname: reviewer\ndescription: \"Reviews: finds and fixes\"\ntools: Read, Grep\nskills:\n  - audit\n  - fix\n---\n"
    "You review code. Skill files: ${CLAUDE_PLUGIN_ROOT}/skills/audit/SKILL.md\n"
)
(cc / "agents" / "writer.md").write_text(
    "---\nname: writer\ndescription: Writes the report\nskills: [report]\n---\nYou write.\n"
)
(cc / ".claude-plugin/plugin.json").write_text(json.dumps({
    "name": "ccrepo", "version": "1.0.0", "license": "MIT",
    "skills": [f"./skills/{n}" for n in ("audit", "fix", "report", "spare")],
}))
sh("git", "-C", str(cc), "init", "-q"); sh("git", "-C", str(cc), "add", ".")
sh("git", "-C", str(cc), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i")
before = (cc / "skills/audit/SKILL.md").read_text()
r = sh("python3", BUILDER, "new", "--repo", str(cc), "--name", "t15", "--workspace", str(ROOT), "--single", "--agents")
case("agents: new --single --agents builds", r.returncode == 0 and "2 agent file(s)" in r.stdout and "2 Enconvo agent(s)" in r.stdout, (r.stdout + r.stderr).strip()[:200])
proot = ROOT / "t15-enconvo/marketplace/plugins/t15-enconvo"
pm = json.loads((proot / ".claude-plugin/plugin.json").read_text())
case("agents: manifest lists the agent files", sorted(pm.get("agents", [])) == ["./agents/reviewer.md", "./agents/writer.md"], str(pm.get("agents")))
case("agents: agent files ship byte-identical", (proot / "agents/reviewer.md").read_text() == (cc / "agents/reviewer.md").read_text())
amap = json.loads((ROOT / "t15-enconvo/agent-map.json").read_text())
byid = {a["id"]: a for a in amap["agents"]}
case("agents: map comes from the agents' skills lists", sorted(byid) == ["reviewer", "writer"] and byid["reviewer"]["skills"] == ["audit", "fix"] and byid["writer"]["skills"] == ["report"], str(amap)[:200])
case("agents: upstream agent's own prompt kept", byid["reviewer"]["prompt"].startswith("You review code") and byid["reviewer"]["description"] == "Reviews: finds and fixes")
case("agents: assigned skills scoped, unassigned not", "visibility: scoped" in (proot / "skills/audit/SKILL.md").read_text() and "visibility" not in (proot / "skills/spare/SKILL.md").read_text())
case("agents: upstream clone never mutated", (cc / "skills/audit/SKILL.md").read_text() == before)
case("agents: linked tools/ file still ships", (proot / "tools/helper.md").is_file())
r = sh("python3", BUILDER, "build", "--config", str(ROOT / "t15-enconvo/build-config.json"))
r2 = sh("python3", BUILDER, "build", "--config", str(ROOT / "t15-enconvo/build-config.json"))
case("agents: rebuild is stable (no double scoping)", r2.returncode == 0 and (proot / "skills/audit/SKILL.md").read_text().count("visibility") == 1)

# --- 19. validator accepts the agents manifest ---
cli = os.path.expanduser("~/.config/enconvo/bin/enconvo")
if os.path.exists(cli):
    v = sh(cli, "plugin", "validate", "--json", str(proot))
    txt = v.stdout + v.stderr
    bad = [c for c in ("plugin_agents_path_not_file", "plugin_component_frontmatter_missing", "plugin_component_description_missing", "default_agents_folder_ignored") if c in txt]
    try:
        vj = json.loads(v.stdout)
        errs = [i for i in vj.get("issues", []) if i.get("severity") == "error"]
    except ValueError:
        errs = ["unparseable validator output"]
    case("agents: validator accepts agent files", not bad and not errs, str(bad or errs)[:160])

# --- 20. ${CLAUDE_PLUGIN_ROOT} follows a flatten move ---
nr = ROOT / "rootvar"
(nr / ".claude-plugin").mkdir(parents=True); (nr / "agents").mkdir()
skill(nr, "grp", "deep")
(nr / "agents" / "a.md").write_text("---\nname: a\ndescription: d\nskills: [deep]\n---\nRead ${CLAUDE_PLUGIN_ROOT}/skills/grp/deep/SKILL.md and ${CLAUDE_PLUGIN_ROOT}/other/x.md\n")
(nr / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "rootvar", "version": "1.0.0", "license": "MIT", "skills": ["./skills/grp/deep"]}))
(nr / "LICENSE").write_text("MIT\n")
r = sh("python3", BUILDER, "new", "--repo", str(nr), "--name", "t16", "--workspace", str(ROOT), "--single", "--agents")
text = (ROOT / "t16-enconvo/marketplace/plugins/t16-enconvo/agents/a.md").read_text() if r.returncode == 0 else r.stderr
case("agents: plugin-root paths follow the flatten move", "${CLAUDE_PLUGIN_ROOT}/skills/deep/SKILL.md" in text and "${CLAUDE_PLUGIN_ROOT}/other/x.md" in text, text.strip()[-120:])
nu = ROOT / "nestuniq"
(nu / ".claude-plugin").mkdir(parents=True)
for g, ns in (("g1", ("a1", "a2")), ("g2", ("b1",))):
    for n in ns:
        skill(nu, g, n)
(nu / "LICENSE").write_text("MIT\n")
(nu / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "nestuniq", "version": "1.0.0", "license": "MIT", "skills": ["./skills/g1/a1", "./skills/g1/a2", "./skills/g2/b1"]}))
r = sh("python3", BUILDER, "new", "--repo", str(nu), "--name", "t19", "--workspace", str(ROOT), "--single", "--agents")
am = json.loads((ROOT / "t19-enconvo/agent-map.json").read_text()) if r.returncode == 0 else {"agents": []}
case("agents: nested upstream folders become the split", sorted((a["id"], tuple(a["skills"])) for a in am["agents"]) == [("g1", ("a1", "a2")), ("g2", ("b1",))], (r.stdout + r.stderr).strip()[:200])
case("agents: nested+flatten ships skills/<name>", (ROOT / "t19-enconvo/marketplace/plugins/t19-enconvo/skills/a1/SKILL.md").is_file())

# --- 21. groups file drives the map; catch_all absorbs strays; unknown agents key rejected ---
gr2 = ROOT / "grpagents"
(gr2 / ".claude-plugin").mkdir(parents=True)
for n in ("s1", "s2", "s3", "s4"):
    skill(gr2, "", n)
(gr2 / "LICENSE").write_text("MIT\n")
(gr2 / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "grpagents", "version": "1.0.0", "license": "MIT", "skills": [f"./skills/{n}" for n in ("s1", "s2", "s3", "s4")]}))
gf = ROOT / "gf.json"
gf.write_text(json.dumps({"alpha": {"skills": ["s1", "s2"], "description": "The alpha team"}, "rest": {"skills": ["s3"], "catch_all": True}}))
r = sh("python3", BUILDER, "new", "--repo", str(gr2), "--name", "t17", "--workspace", str(ROOT), "--single", "--agents", "--groups-file", str(gf))
amap = json.loads((ROOT / "t17-enconvo/agent-map.json").read_text()) if r.returncode == 0 else {"agents": []}
byid = {a["id"]: a for a in amap["agents"]}
case("agents: groups file is the map, catch_all takes strays", r.returncode == 0 and byid.get("alpha", {}).get("skills") == ["s1", "s2"] and byid.get("rest", {}).get("skills") == ["s3", "s4"], (r.stdout + r.stderr).strip()[:200])
case("agents: group description and title", byid.get("alpha", {}).get("description") == "The alpha team" and byid.get("alpha", {}).get("title") == "T17 Alpha", str(byid.get("alpha")))
cfg17 = ROOT / "t17-enconvo/build-config.json"
c17 = json.loads(cfg17.read_text()); c17["agents"]["bogus"] = 1; cfg17.write_text(json.dumps(c17))
r = build(cfg17)
case("agents: unknown config key is a clean error", r.returncode == 1 and "unknown 'agents' key" in r.stdout + r.stderr and "Traceback" not in r.stderr)
del c17["agents"]["bogus"]
c17["agents"]["scoped"] = False; cfg17.write_text(json.dumps(c17))
r = build(cfg17)
case("agents: scoped=false leaves skills global", r.returncode == 0 and "visibility" not in (ROOT / "t17-enconvo/marketplace/plugins/t17-enconvo/skills/s1/SKILL.md").read_text())

# --- 22. agents on without any split: clean error ---
ns = ROOT / "nosplit"
(ns / ".claude-plugin").mkdir(parents=True); skill(ns, "", "only"); (ns / "LICENSE").write_text("MIT\n")
(ns / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "nosplit", "version": "1.0.0", "license": "MIT", "skills": ["./skills/only"]}))
r = sh("python3", BUILDER, "new", "--repo", str(ns), "--name", "t18", "--workspace", str(ROOT), "--single", "--agents")
case("agents: no split is a clean error", r.returncode == 1 and "no agent split" in r.stdout + r.stderr and "Traceback" not in r.stderr, (r.stdout + r.stderr).strip()[:160])

# --- 23. sync_agents against a fake Enconvo: create, link, idempotent, update, prune ---
srv, api = fake_api(ROOT / "fake-agents")
ext = ROOT / "fake-ext"
env = {**os.environ, "ENCONVO_API": api, "ENCONVO_EXTENSION_DIR": str(ext)}
cfg15 = str(ROOT / "t15-enconvo/build-config.json")
r = sh("python3", BUILDER, "agents", "--config", cfg15, env=env)
case("agents step: refuses when the plugin is not installed", r.returncode == 1 and "not installed" in r.stdout + r.stderr and not FakeEnconvo.agents)
shutil.copytree(proot, ext / "t15-enconvo")
r = sh("python3", BUILDER, "agents", "--config", cfg15, env=env)
ws = {v["title"]: Path(v["workspace"]) for v in FakeEnconvo.agents.values()}
rev = ws.get("T15 Reviewer")
case("agents step: creates both Agents", r.returncode == 0 and sorted(ws) == ["T15 Reviewer", "T15 Writer"], (r.stdout + r.stderr).strip()[-200:])
links = sorted(p.name for p in (rev / ".enconvo/skills").iterdir()) if rev else []
case("agents step: each Agent links only its skills", links == ["audit", "fix"] and all(p.is_symlink() for p in (rev / ".enconvo/skills").iterdir()), str(links))
case("agents step: links point into the installed plugin", os.readlink(rev / ".enconvo/skills/audit") == str(ext / "t15-enconvo/skills/audit"))
r = sh("python3", BUILDER, "agents", "--config", cfg15, env=env)
case("agents step: second run changes nothing", r.returncode == 0 and "0 created" in r.stdout and "0 links added" in r.stdout and len(FakeEnconvo.agents) == 2, r.stdout.strip()[-120:])
# a deleted Agent is recreated; a user's own real folder is left alone
key = next(k for k, v in FakeEnconvo.agents.items() if v["title"] == "T15 Writer")
FakeEnconvo.agents.pop(key)
r = sh("python3", BUILDER, "agents", "--config", cfg15, env=env)
case("agents step: a deleted Agent is recreated", r.returncode == 0 and len(FakeEnconvo.agents) == 2 and "1 created" in r.stdout, r.stdout.strip()[-120:])
(rev / ".enconvo/skills/mine").mkdir()
(rev / ".enconvo/skills/mine/SKILL.md").write_text("---\nname: mine\n---\n")
# map changes: reviewer loses fix, gains spare
amp_path = ROOT / "t15-enconvo/agent-map.json"
m = json.loads(amp_path.read_text())
for a in m["agents"]:
    if a["id"] == "reviewer":
        a["skills"] = ["audit", "spare"]
amp_path.write_text(json.dumps(m))
r = sh("python3", BUILDER, "agents", "--config", cfg15, env=env)
now = sorted(p.name for p in (rev / ".enconvo/skills").iterdir())
case("agents step: skill set changes are followed; user folders kept", r.returncode == 0 and now == ["audit", "mine", "spare"], str(now))
# legacy ineffective disabled ids cleared
FakeEnconvo.disabled[next(k for k, v in FakeEnconvo.agents.items() if v["title"] == "T15 Reviewer")] = ["extension:t15-enconvo/fix", "user-own"]
r = sh("python3", BUILDER, "agents", "--config", cfg15, env=env)
left = next(iter(v for k, v in FakeEnconvo.disabled.items() if "user-own" in v), None)
case("agents step: legacy plugin ids dropped, user entries kept", left == ["user-own"], str(FakeEnconvo.disabled))
# prune only deletes with --prune
m = json.loads(amp_path.read_text()); m["agents"] = [a for a in m["agents"] if a["id"] != "writer"]; amp_path.write_text(json.dumps(m))
r = sh("python3", BUILDER, "agents", "--config", cfg15, env=env)
case("agents step: orphan reported, not deleted", "orphan: 'writer'" in r.stdout and not FakeEnconvo.deleted and len(FakeEnconvo.agents) == 2)
r = sh("python3", BUILDER, "agents", "--config", cfg15, "--prune", env=env)
case("agents step: --prune deletes the orphan", len(FakeEnconvo.deleted) == 1 and len(FakeEnconvo.agents) == 1, str(FakeEnconvo.deleted))
# adopting an existing Agent by title (an earlier script's Agent) and dry run
FakeEnconvo.agents["agent|legacy"] = {"title": "T17 Alpha", "workspace": str(ROOT / "fake-agents/legacy")}
(ROOT / "fake-agents/legacy").mkdir()
ext17 = ext / "t17-enconvo"
shutil.copytree(ROOT / "t17-enconvo/marketplace/plugins/t17-enconvo", ext17)
cfg17s = str(ROOT / "t17-enconvo/build-config.json")
c17["agents"]["scoped"] = True; Path(cfg17s).write_text(json.dumps(c17)); sh("python3", BUILDER, "build", "--config", cfg17s)
n_before = len(FakeEnconvo.agents)
r = sh("python3", BUILDER, "agents", "--config", cfg17s, "--dry-run", env=env)
case("agents step: --dry-run changes nothing", r.returncode == 0 and len(FakeEnconvo.agents) == n_before and not (ROOT / "fake-agents/legacy/.enconvo").exists() and not (ROOT / "t17-enconvo/agents-state.json").exists())
r = sh("python3", BUILDER, "agents", "--config", cfg17s, env=env)
case("agents step: adopts an existing Agent by title", r.returncode == 0 and "adopted" in r.stdout and (ROOT / "fake-agents/legacy/.enconvo/skills/s1").is_symlink() and len(FakeEnconvo.agents) == n_before + 1, r.stdout.strip()[-160:])

# --- 24. hand-written agent prompts: agent-prompts/<id>.md ---
ad17 = ROOT / "t17-enconvo"
(ad17 / "agent-prompts").mkdir(exist_ok=True)
GOOD = ("---\ntitle: Alpha Planner\ndescription: \"Plans alpha work: sets scope and returns a checked plan.\"\n---\n"
        "You are an alpha planner.\n\n# Your skills\n\n- `s1`: first.\n- `s2`: second.\n\n# Delivery\n\nReturn the plan.\n")
(ad17 / "agent-prompts" / "alpha.md").write_text(GOOD)
(ad17 / "agent-prompts" / "ghost.md").write_text("---\ntitle: Ghost\ndescription: x\n---\nYou are a ghost.\n")
r = sh("python3", BUILDER, "build", "--config", cfg17s)
am = {a["id"]: a for a in json.loads((ad17 / "agent-map.json").read_text())["agents"]}
case("prompts: written file replaces title, description and prompt in the map",
     r.returncode == 0 and am["alpha"]["title"] == "Alpha Planner" and am["alpha"]["description"].startswith("Plans alpha work")
     and am["alpha"]["prompt"].startswith("You are an alpha planner") and am["alpha"]["written"] is True, str(am["alpha"])[:200])
case("prompts: agents without a file keep generated text", am["rest"]["written"] is False and am["rest"]["title"] == "T17 Rest", str(am["rest"])[:160])
case("prompts: a file matching no agent is warned about", "agent-prompts/ghost.md matches no agent" in r.stdout, r.stdout[-200:])
(ad17 / "agent-prompts" / "ghost.md").unlink()
(ad17 / "agent-prompts" / "alpha.md").write_text(GOOD.replace("- `s2`: second.\n", "- `s9`: bogus.\n"))
r = sh("python3", BUILDER, "build", "--config", cfg17s)
case("prompts: a prompt that disagrees with the skill list is warned about", "disagree" in r.stdout and "s2" in r.stdout and "s9" in r.stdout, r.stdout[-260:])
(ad17 / "agent-prompts" / "alpha.md").write_text(GOOD)
sh("python3", BUILDER, "build", "--config", cfg17s)

# an existing Agent keeps its text unless --update; drift is reported
live = next(k for k, v in FakeEnconvo.agents.items() if v["workspace"].endswith("legacy"))
before = dict(FakeEnconvo.agents[live])
r = sh("python3", BUILDER, "agents", "--config", cfg17s, env=env)
case("prompts: without --update an existing Agent is not changed, drift reported",
     r.returncode == 0 and FakeEnconvo.agents[live]["title"] == before["title"] and FakeEnconvo.agents[live].get("prompt") == before.get("prompt")
     and "differs from the written file" in r.stdout, r.stdout.strip()[-260:])
r = sh("python3", BUILDER, "agents", "--config", cfg17s, "--update", "--dry-run", env=env)
case("prompts: --update --dry-run changes nothing", r.returncode == 0 and FakeEnconvo.agents[live]["title"] == before["title"] and "would update" in r.stdout, r.stdout.strip()[-200:])
r = sh("python3", BUILDER, "agents", "--config", cfg17s, "--update", env=env)
lv = FakeEnconvo.agents[live]
case("prompts: --update sets title, description and prompt",
     r.returncode == 0 and lv["title"] == "Alpha Planner" and lv["description"].startswith("Plans alpha work") and lv["prompt"].startswith("You are an alpha planner"),
     (r.stdout + r.stderr).strip()[-260:])
r = sh("python3", BUILDER, "agents", "--config", cfg17s, "--update", env=env)
case("prompts: a second --update is a no-op", r.returncode == 0 and "0 updated" in r.stdout and "0 differ" in r.stdout, r.stdout.strip()[-200:])
(ad17 / "agent-prompts" / "alpha.md").write_text("---\ntitle: Alpha Planner\ndescription: d\n---\n")
r = sh("python3", BUILDER, "build", "--config", cfg17s)
case("prompts: an empty prompt body is a clean error", r.returncode == 1 and "empty" in r.stdout + r.stderr and "Traceback" not in r.stderr, (r.stdout + r.stderr).strip()[-160:])
(ad17 / "agent-prompts" / "alpha.md").write_text(GOOD)
sh("python3", BUILDER, "build", "--config", cfg17s)
# a title clash created by written titles is caught
(ad17 / "agent-prompts" / "rest.md").write_text(GOOD.replace("alpha planner", "rest planner"))
r = sh("python3", BUILDER, "build", "--config", cfg17s)
case("prompts: two written agents sharing a title is a clean error", r.returncode == 1 and "share the title" in r.stdout + r.stderr and "Traceback" not in r.stderr, (r.stdout + r.stderr).strip()[-200:])
(ad17 / "agent-prompts" / "rest.md").unlink()
sh("python3", BUILDER, "build", "--config", cfg17s)


# --- 25. shared skills: one skill, two Agents (agents.shared_skills) ---
cfg17j = json.loads(Path(cfg17s).read_text())
cfg17j["groups"]["rest"]["skills"] = ["s1", "s3"]
Path(cfg17s).write_text(json.dumps(cfg17j))
r = sh("python3", BUILDER, "build", "--config", cfg17s)
case("shared: an unallowed overlap is a clean error",
     r.returncode == 1 and "more than one explicit group" in r.stdout + r.stderr and "Traceback" not in r.stderr,
     (r.stdout + r.stderr).strip()[-160:])
cfg17j["agents"]["shared_skills"] = {"s1": ["alpha", "rest"]}
Path(cfg17s).write_text(json.dumps(cfg17j))
r = sh("python3", BUILDER, "build", "--config", cfg17s)
am = {a["id"]: a for a in json.loads((ROOT / "t17-enconvo/agent-map.json").read_text())["agents"]}
case("shared: allowed share lands in both agents",
     r.returncode == 0 and am["alpha"]["skills"] == ["s1", "s2"] and am["rest"]["skills"] == ["s1", "s3", "s4"],
     str({k: v["skills"] for k, v in am.items()}))
case("shared: the share is reported", "is shared by groups" in r.stdout, r.stdout[-160:])
r = sh("python3", BUILDER, "agents", "--config", cfg17s, env=env)
st17 = json.loads((ROOT / "t17-enconvo/agents-state.json").read_text())["agents"]
s1_links = [str((Path(FakeEnconvo.agents[k]["workspace"]) / ".enconvo/skills/s1").is_symlink()) for k in st17.values()]
case("shared: both Agents link the shared skill", r.returncode == 0 and s1_links == ["True", "True"], str(s1_links))
cfg17j["agents"]["shared_skills"] = {"s1": "alpha"}
Path(cfg17s).write_text(json.dumps(cfg17j))
r = sh("python3", BUILDER, "build", "--config", cfg17s)
case("shared: a malformed shared_skills map is a clean error",
     r.returncode == 1 and "shared_skills" in r.stdout + r.stderr and "Traceback" not in r.stderr,
     (r.stdout + r.stderr).strip()[-160:])
cfg17j["agents"]["shared_skills"] = {"s1": ["alpha", "rest"]}
Path(cfg17s).write_text(json.dumps(cfg17j))
sh("python3", BUILDER, "build", "--config", cfg17s)


# --- 26. native packaging: package.json + agents/*.md ship in the plugin ---
r = sh("python3", BUILDER, "build", "--config", cfg17s)
stage = ROOT / "t17-enconvo/marketplace/plugins/t17-enconvo"
native = json.loads((stage / "package.json").read_text())
case("native: package.json declares the plugin natively",
     native.get("$schema") == "https://enconvo.com/schemas/extension.json"
     and native.get("name") == "t17-enconvo" and "version" in native and "title" in native,
     str({k: native.get(k) for k in ("$schema", "name", "version", "title")}))
names = sorted(x["name"] for x in native.get("skills", []))
case("native: the skills list matches the shipped skills", names == ["s1", "s2", "s3", "s4"], str(names))
agent_files = sorted(x.name for x in (stage / "agents").glob("*.md"))
case("native: one agents/<id>.md per agent def", agent_files == ["alpha.md", "rest.md"], str(agent_files))
alpha = (stage / "agents/alpha.md").read_text()
case("native: agent file carries name, description and prompt",
     alpha.startswith("---\nname: alpha\n") and "description:" in alpha.split("---")[1] and "You are" in alpha,
     alpha[:120])
claude = json.loads((stage / ".claude-plugin/plugin.json").read_text())
case("native: the claude manifest lists every agent file",
     sorted(claude.get("agents", [])) == ["./agents/alpha.md", "./agents/rest.md"],
     str(claude.get("agents")))


# --- 27. native packaging without agents: a fresh skills-only repo ---
import tempfile
ws27 = Path(tempfile.mkdtemp(prefix="r2p-na-"))
repo27 = json.loads(Path(cfg17s).read_text())["repos"][0]["path"]
r = sh("python3", BUILDER, "new", "--repo", repo27, "--name", "na",
       "--workspace", str(ws27), "--single")
r = sh("python3", BUILDER, "build", "--config", str(ws27 / "na-enconvo/build-config.json"))
stage27 = ws27 / "na-enconvo/marketplace/plugins/na-enconvo"
nat27 = json.loads((stage27 / "package.json").read_text())
case("native without agents: fresh repo builds a native skills-only plugin",
     nat27.get("$schema") == "https://enconvo.com/schemas/extension.json"
     and len(nat27.get("skills", [])) == 4 and "title" in nat27,
     str({k: nat27.get(k) for k in ("$schema", "name", "title")}))
case("native without agents: no agent files, map or manifest agents",
     not (stage27 / "agents").exists() and not (ws27 / "na-enconvo/agent-map.json").exists()
     and "agents" not in json.loads((stage27 / ".claude-plugin/plugin.json").read_text()),
     "agent artifacts present")
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("np_probe", BUILDER)
_npm = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_npm)
write_probe_icon = _npm.write_placeholder_icon

icon27 = (stage27 / "assets/icon.png").read_bytes()
case("icon: icon-less repo still ships a valid PNG icon",
     len(icon27) > 500 and icon27[:4] == bytes([0x89]) + b"PNG"
     and nat27.get("icon") == "icon.png",
     f"icon bytes {len(icon27)}, magic {icon27[:4]}")
case("icon: placeholder is deterministic",
     (lambda f: (write_probe_icon(f, "na-enconvo"), open(f, "rb").read() == icon27))(
         str(ws27 / "probe.png")),
     "regenerated placeholder differs")
shutil.rmtree(ws27)

# --- 28. unshipped upstream parts are recorded, not silent ---
import tempfile
ws28 = Path(tempfile.mkdtemp(prefix="r2p-us-"))
repo28 = ws28 / "src"
shutil.copytree(repo27, repo28)
(repo28 / "commands").mkdir()
(repo28 / "commands" / "deploy.md").write_text("---\ndescription: deploy\n---\nrun it")
(repo28 / "hooks").mkdir()
(repo28 / "hooks" / "h.json").write_text("{}")
(repo28 / ".mcp.json").write_text('{"mcpServers": {}}')
sh("python3", BUILDER, "new", "--repo", str(repo28), "--name", "us",
   "--workspace", str(ws28), "--single")
sh("python3", BUILDER, "build", "--config", str(ws28 / "us-enconvo/build-config.json"))
rd28 = (ws28 / "us-enconvo/README.md").read_text()
case("unshipped: adapter README records commands, hooks and mcp",
     "## Upstream parts not shipped" in rd28 and "commands/ folder" in rd28
     and "hooks/ folder" in rd28 and ".mcp.json" in rd28,
     "README section incomplete")
stg28 = ws28 / "us-enconvo/marketplace/plugins/us-enconvo"
case("unshipped: excluded parts stay out of the plugin",
     not (stg28 / "commands").exists() and not (stg28 / ".mcp.json").exists()
     and (stg28 / "assets/icon.png").is_file(),
     "excluded upstream parts shipped")
ws28b = Path(tempfile.mkdtemp(prefix="r2p-lg-"))
shutil.copytree(repo28, ws28b / "src")
sh("python3", BUILDER, "new", "--repo", str(ws28b / "src"), "--name", "usl",
   "--workspace", str(ws28b))
cfg28b = ws28b / "usl-enconvo/build-config.json"
cb = json.loads(cfg28b.read_text())
icon28 = ws28b / "custom.png"
write_probe_icon(str(icon28), "usl")
cb["plugin_icon"] = str(icon28)
cfg28b.write_text(json.dumps(cb))
sh("python3", BUILDER, "build", "--config", str(cfg28b))
rd28b = (ws28b / "usl-enconvo/README.md").read_text()
case("unshipped: per-group mode records parts too",
     "## Upstream parts not shipped" in rd28b and "commands/ folder" in rd28b,
     "per-group README lacks the section")
plug28b = next((ws28b / "usl-enconvo/marketplace/plugins").glob("*"))
case("icon: plugin_icon overrides the placeholder in per-group mode",
     (plug28b / "icon.png").read_bytes() == icon28.read_bytes(),
     "custom icon did not win")
shutil.rmtree(ws28b)
shutil.rmtree(ws28)

srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("all cases passed")
