# Versioning

The plugin ships from `plugins/kubernetes-explore/`. Any change to a file it
ships — `skills/`, `agents/`, `hooks/`, `bin/`, or `.claude-plugin/` under that
directory — requires bumping `version` in
`plugins/kubernetes-explore/.claude-plugin/plugin.json` in the same branch.
Claude Code only re-installs a plugin when the version
changes, so a change without a bump silently never reaches users. One bump per
branch/PR is enough — don't increment again for every additional commit.

Files that don't ship with the plugin (`README.md`, `tests/`, `.claude/`) don't
need a bump on their own.
