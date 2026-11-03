# Extension Creator (extension-creator)

An Enconvo plugin carrying one skill, **repo-to-plugin**, that packages a
repo of agent skills (a Claude Code plugin with skills and agents, or a bare
skills repo) into an updatable Enconvo folder marketplace holding one native
Enconvo plugin (series convention `<name>-enconvo`), with one Enconvo Agent
per skill group that sees only its own skills.

## What it builds

For each upstream repo the skill generates, in a workspace of your choice:

- an adapter folder `<name>-enconvo/` with `build-config.json` (the single
  input file), `README.md` and a `marketplace/` folder;
- one plugin inside that marketplace carrying every skill flat at
  `skills/<name>` (`"flatten": true`, the series default);
- an optional Agent map (`"agents": {"enabled": true}`) that turns skill
  groups into real Enconvo Agents, each linked to only its own skills
  (`visibility: scoped` plus per-Agent workspace skill links);
- Claude Code `agents/*.md` files shipped byte-identical, so Enconvo loads
  them natively as helpers.

Upstream clones stay pristine: every Enconvo-specific file is generated, and
regeneration after a pull is the update path.

## The skill

`skills/repo-to-plugin/SKILL.md` — load it in an Enconvo chat (or run its
scripts directly) to package a new repo or sync an already-packaged one:

```
python3 skills/repo-to-plugin/scripts/new_plugin.py new \
  --repo <clone-or-url> --name <name> --workspace <project-root> --single
python3 skills/repo-to-plugin/scripts/new_plugin.py sync --apply \
  --config <name>-enconvo/build-config.json
```

The scripts carry their own regression suite:

```
python3 skills/repo-to-plugin/scripts/test_new_plugin.py
```

## Installing this plugin

Documented CLI (see [Installing Plugins](https://docs.enconvo.ai/extensions/installing-plugins)):

```
npx -p @enconvo/api enconvo plugin install Iteractive-Development/extension-creator
```

or from a local clone:

```
npx -p @enconvo/api enconvo plugin install /path/to/extension-creator
```

Environment note: on Macs where the `npx -p @enconvo/api` route stalls, the
same commands work through the local CLI at
`~/.config/enconvo/bin/enconvo`.

## Layout

```
package.json                        # Enconvo manifest (skill discovered by the
                                    # skills/<name>/SKILL.md folder scan)
skills/repo-to-plugin/SKILL.md      # the repo-to-plugin skill
skills/repo-to-plugin/references/   # agent system-prompt standard
skills/repo-to-plugin/scripts/      # new_plugin.py, agents_support.py,
                                    # test_new_plugin.py
assets/marketing-icon.png           # plugin icon
```

License: MIT. Author: Iteractive Development (https://iteractive.dev).
