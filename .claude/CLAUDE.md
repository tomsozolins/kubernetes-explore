# Versioning

Any change to a file the plugin ships — `skills/`, `agents/`, `hooks/`, `bin/`,
or `.claude-plugin/` — requires bumping `version` in `.claude-plugin/plugin.json`
in the same branch. Claude Code only re-installs a plugin when the version
changes, so a change without a bump silently never reaches users. One bump per
branch/PR is enough — don't increment again for every additional commit.

Files that don't ship with the plugin (`README.md`, `tests/`, `.claude/`) don't
need a bump on their own.
