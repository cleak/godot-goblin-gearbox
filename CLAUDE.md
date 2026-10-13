@AGENTS.md

## Claude Code

- Windows: run the kit's commands with the PowerShell tool. Git Bash rewrites an argument
  that starts with `/`, such as a node path. Linux and macOS: the Bash tool.
- Shell variables do not carry from one tool call to the next. Set the session in the call
  that starts the game: `$env:GODOT_AGENT_SESSION = "<name>"` in pwsh,
  `export GODOT_AGENT_SESSION=<name>` in bash. Give `tools/devtools.py` `--session <name>`
  after the command: `devtools.py ping --session <name>`.
- Open a screenshot with Read: it shows the image.
- `.claude/settings.json` pre-approves `check.ps1`, `doctor.ps1`, the lints, the type check,
  both test runners, the headless `tools/*.gd` checks and every `tools/devtools.py` command.
  They run the project's scripts and tests; `-Fix` and `--fix` rewrite `.gd` files and scene
  uids. Setup, installs, starting the game (with or without `xvfb-run`) and
  `devtools.py --project <folder>` still ask.
- MCP is optional; the CLI does the same. To turn it on, copy `tools/mcp/.mcp.json.example` to
  `.mcp.json` at the project root, set `GODOT_AGENT_SESSION` in it, and approve the server.
