# Claude Code skills

Skills are folders with a `SKILL.md` file (YAML front matter with `name` and `description`, then instructions) and
any supporting files. Claude Code loads them from `~/.claude/skills/<name>/` (all projects) or
`<project>/.claude/skills/<name>/` (one project).

Conventions for a skill in this folder:

- One folder per skill, named as the skill is named, with `SKILL.md` at its top and a short README for people.
- Install by copying or symlinking the folder into a skills directory, for example
  `ln -s "$PWD/claude-code/skills/<name>" ~/.claude/skills/<name>`.
- No secrets, account identifiers, or personal paths; read them from the environment at run time.

No skills yet.
