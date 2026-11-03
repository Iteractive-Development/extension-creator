---
name: repo-to-plugin
description: Package a repo of agent skills (Claude Code plugin with skills and agents, or a skills repo) into an updatable Enconvo folder marketplace carrying one plugin (series convention <name>-enconvo), with one Enconvo Agent per skill group that sees only its own skills. Use when Brad gives a repo as "Plugin N" of his plugin series, asks to turn a repo into an Enconvo plugin or Agents, or asks to sync or update an already-packaged one.
metadata:
  short-description: Turn skills repos into native Enconvo plugins
---

# Repo to Enconvo plugin

Turn a skills repo into a local folder marketplace carrying ONE updatable
Enconvo plugin. Series convention (2026-10-29, superseding the earlier
one-plugin-per-agent shape and its `-agents` marketplace names at Brad's
direction - "jev agents is superseded by jev enconvo"): the clone
`<name>/`, the adapter folder `<name>-enconvo/`, the marketplace, and the
plugin inside it are all named `<name>-enconvo`. Pristine upstream clone +
generated packaging is the rule; regeneration after a pull is the update.

Shipped layout is flat: every skill sits at `skills/<name>` (build-config's
`"flatten": true`, the series default). The groups (or the upstream agents'
`skills:` lists, or the upstream folder nesting) are the **agent map**, and
with `"agents": {"enabled": true}` the builder turns it into real Enconvo
Agents. How scoping works in Enconvo (verified in its skill catalog):
plugin skills are global, so folders and an Agent's `disabled` list do NOT
scope them. A skill whose SKILL.md says `visibility: scoped` drops out of the
global list, and an Agent sees any skill folder in its own
`<agent home>/.enconvo/skills/`. So the builder (1) marks every agent-assigned
skill `visibility: scoped` in the shipped copy only, (2) writes
`agent-map.json`, and (3) the `agents` step creates or adopts each Agent and
symlinks its skills from the INSTALLED plugin folder. A plugin update reaches
every Agent at once, because the links point at the installed files.

Reference implementations: `../../../bama-enconvo`, `../../../cyber-enconvo`
(two upstream repos merged into one plugin), `../../../marketing-enconvo`,
`../../../jev-enconvo` (original work, hand-maintained, no builder).

## New plugin ("Plugin N: <repo>")

Work in the project root (`Agent Plugins`). The clone and the adapter folder
are siblings.

1. Clone pristine: `git clone --depth 1 <url> <name>` (reuse an existing
   clone; never edit it - every Enconvo-specific file is generated).
2. Read the layout: `.claude-plugin/plugin.json` (or `plugin.json`), its
   `skills` list, and the folder nesting. Nested upstream folders such as
   `skills/<stage>/<name>` are flattened to `skills/<name>` (names must be
   unique in the repo; a clash is a clean error). If the agent split is not
   obvious from the repo, propose the grouping in one line before building;
   grouping follows the repo's own nesting or README taxonomy (never model
   classification).
3. Scaffold and build:

       python3 <this skill>/scripts/new_plugin.py new --repo <clone-or-url> --name <name> --workspace <project root> --single

   - `--single` (the series default) writes `<name>-enconvo/` with
     build-config.json, README.md, and a marketplace whose one plugin
     `<name>-enconvo` carries every skill. A single-repo plugin keeps the
     upstream author in its manifest (attribution); a multi-repo merge
     (repeat `--repo`) credits the manifest to the series owner and copies
     each upstream's LICENSE file as `LICENSE-<repo>` - name the sources in
     `--marketplace-description` / build-config's `marketplace_description`
     and `plugin_description`.
   - Agent map: pass `--groups-file` with `{"<group>": {"description": "...",
     "skills": [...], "catch_all": true?}}`. The groups are embedded in
     build-config.json, so `sync --config` needs no flags. Keep the file at the
     project root as `<prefix>-groups.json`. `catch_all` on one group absorbs
     skills upstream adds later; without it, unlisted skills fail the build so
     the map stays deliberate. With `"flatten": true` (the series default) the
     plugin ships flat and the groups stay as the agent map. Without flatten,
     each group's skills nest as `skills/<group>/<name>` (validator error
     `plugin_runtime_surface_missing`; avoid it for store builds).
   - Agents: add `--agents` (needs `--single`; `--agents-title` sets the title
     prefix). The split comes from, in order: `groups` in build-config, the
     upstream `agents/*.md` files' `skills:` lists (each upstream agent becomes
     an Enconvo Agent with its own prompt and description), the upstream skill
     folder nesting. No split is a clean error. Titles are `<Prefix> <Group>`
     (acronyms upper-cased; `agents.title_overrides` for others, e.g.
     `{"appsec": "AppSec"}`); `agents.scoped: false` leaves skills global.
   - Claude Code agent files (`agents/*.md`, or the manifest's `agents` list)
     ship byte-identical and the manifest lists them, so Enconvo loads them
     natively as helpers (`@` menu, Agent mode). `${CLAUDE_PLUGIN_ROOT}/...`
     paths in them follow a flatten move. Parts the builder does not carry
     (commands, hooks, .mcp.json, settings, output-styles) are reported as a
     warning on every build, never dropped silently.
   - `--category`, `--marketplace`, `--owner-name`, `--owner-url` override
     the rest; owner defaults to Iteractive Development
     (https://iteractive.dev). Repeat `--repo` without `--single` to keep one
     plugin per repo in a shared marketplace.
4. Validate: `enconvo plugin validate` on the marketplace and on the plugin.
   The flat layout has no errors. Warnings `skill_manifest_missing` and
   `skill_file_ignored` appear for skill folders that have `evals/` or
   `references/` subfolders (the validator reads them as groups); they are
   warnings, not blockers, and they appear in both layouts. Fix any error
   before installing.
5. Install with the documented CLI:
   `npx -p @enconvo/api enconvo plugin marketplace add <marketplace abs path>`,
   then `npx -p @enconvo/api enconvo plugin install <name>-enconvo@<name>-enconvo`.
   Environment note (this Mac only): the `npx -p @enconvo/api` route has
   stalled here; the same commands work through the local CLI at
   `~/.config/enconvo/bin/enconvo`. Use the documented form first and fall
   back to the local bin only when it hangs.
6. Verify: `enconvo plugin marketplace list` shows the plugin `(installed)`,
   and `~/.config/enconvo/extension/<plugin>` holds its skill folders.
   Count SKILL.md against the upstream repo. `plugins/capabilities/search`
   with a skill's name confirms the runtime sees it.
7. Agents (when enabled): `python3 <this skill>/scripts/new_plugin.py agents --config <name>-enconvo/build-config.json`
   (`--dry-run` first on an existing setup). It creates each Agent, adopts an
   existing Agent with the same title, links its skills and never overwrites
   an existing Agent's title, description or prompt unless you pass `--update`,
   which applies the adapter's hand-written `agent-prompts/<id>.md` files
   (frontmatter `title` and `description`, body = system prompt). Without
   `--update`, differences are only reported. One skill can serve two Agents
   when both need it mid-flow: list it in both groups and allow it under
   `agents.shared_skills`; keep soft cross-references as handoffs (rule in
   `references/agent-prompt-standard.md`). Every build also emits an
   Enconvo-native `package.json` ($schema, title, category, skills list, icon
   at `assets/icon.png` — set `plugin_title`, `plugin_category` and optionally
   `plugin_icon` in build-config), which makes the plugin register as an
   Enconvo plugin rather than a Claude plugin, and one `agents/<id>.md` per
   group agent (the hand-written prompt), which ships as a helper in the
   composer's @ menu; `.claude-plugin/plugin.json` stays for Claude Code.
   Repos without agents work the same way: `new --single` without `--agents`
   builds a native skills-only plugin (no agent files, map or helper list).
   Icon-less repos are not a gap: the builder writes a deterministic placeholder
   PNG (rounded square + initial; write_placeholder_icon) so the store icon check
   always passes - set plugin_icon in build-config.json to replace it. Upstream
   parts Enconvo cannot load (commands/, hooks/, .mcp.json, settings.json,
   output-styles, manifest mcpServers etc.) stay out of the plugin and are
   recorded in an Upstream parts not shipped section of the adapter README,
   so nothing drops silently. Write those files to
   `references/agent-prompt-standard.md` (Enconvo naming, prompt shape, the
   prompt-master techniques to use and avoid, domain gates, final audit): read
   every owned SKILL.md first, list each owned skill once under `# Your skills`,
   and rebuild so the map carries them (`build` warns when a prompt and its
   skill list disagree or a file matches no agent). Run `agents --update --dry-run`
   first. Check one Agent's skills with
   `search/skill_catalog` (`agent_command_key`): it must list only its own
   skills as scope `agent`, and the main agent none of them.
8. Report to Brad: the skill count, the update command (`sync --config`
   one-liner), and that it is the updater. Then update the plugin-series
   Memory.

## Update an existing plugin

    python3 <this skill>/scripts/new_plugin.py sync --apply --config <name>-enconvo/build-config.json

Pulls the pristine clones (--ff-only), rebuilds, runs `enconvo plugin
marketplace refresh` and `enconvo plugin update`, then refreshes the Agents:
new skills link into their Agents, removed ones unlink, and an Agent whose
split disappeared is reported as an orphan (`agents --prune` deletes it; its
folder stays). Without `--apply` it only prints those commands. A second run
changes nothing. The adapter's only input is build-config.json; the builder
also writes `agent-map.json` (the plan) and `agents-state.json` (which Agent
belongs to which group) - both generated, never edit them.

## Rules that make it work

- A failed build (bad groups file, upstream skill removed, bad JSON, a flatten
  name clash) leaves the existing marketplace untouched: builds stage into
  `.marketplace-new` and swap in only on success. A failed `sync` never breaks
  installed plugins.
- Builds warn instead of staying silent when something won't ship: skill
  folders on disk left out of the build (template dirs, or not named in an
  upstream manifest's skills list), groups-file keys that matched no group
  (group mode), the same skill name shipping in two plugins. In single mode a
  skill path occurring in two repos is a hard error, not a silent merge. Read
  the output after a sync.
- After changing the builder, run its regression suite (all green before
  every commit of this skill):

       python3 <this skill>/scripts/test_new_plugin.py

- Enconvo loads skills from the manifest's `skills` paths, which the builder
  writes for every skill. Folder nesting is not a scope, and an Agent's
  `workspace_skills.disabled` list does not hide plugin skills; only
  `visibility: scoped` plus the Agent's own `.enconvo/skills/` links do. The
  agents step drops the old `extension:` ids from disabled lists (they never
  worked) and leaves every other entry. Links in an Agent's folder that point
  elsewhere, and real folders there, are never touched.
- Never copy another skill's `SKILL.md` into a plugin - it would register as
  a duplicate skill. The builder skips those links and reports them.
- The builder's link scan follows every relative markdown link into the
  upstream repo, not only `skills/`. A link into a repo-root folder such as
  `tools/` ships that file at its repo-relative path, and the link is rewritten
  when a skill moves, so links keep resolving. It reruns on every build, so
  upstream edits stay in step.
- Upstream files stay byte-identical except for relinked markdown links to
  moved skills and the `visibility: scoped` line on agent-assigned skills; all packaging lives in generated manifests. Regeneration
  therefore never conflicts with upstream merges.
- Known upstream breaks are not fixed by the builder (they would mean editing
  upstream text): marketing's sms `[link](link)` placeholder, ads'
  `conversion-tracking.md` link (no such file in ads/references upstream),
  and root-relative website paths (`/features/analytics`, `/alternatives/*`).
  Cyber's `electron.md` has a regex-like pseudo-link and bama's
  `plan/research` a `url` placeholder. Report these; do not patch them silently.
- Versions stamp as `<upstream version>+<commit>`; folder-change detection
  drives updates, the stamp only shows provenance.
- Skill names stay upstream's. Two plugins shipping the same skill name get
  `<plugin>:<name>` prefixes in chat - one plugin per adapter makes that
  impossible within a series.
- Odd shapes (e.g. `plugins/<cat>/...`) need a small builder extension first -
  extend `new_plugin.py` rather than hand-copying files, so updates stay one
  command.
