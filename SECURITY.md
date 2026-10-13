# Security

Report a vulnerability through GitHub's private reporting on this repository (Security >
Report a vulnerability), not in a public issue.

In scope:

- `install.ps1`: what it writes into a project.
- `tools/install_godot.ps1`, `tools/install_gdunit4.py`: downloads and hash checks.
- The DevTools mailbox (`addons/agent_devtools/`) and `tools/mcp/server.py`.
- `.claude/settings.json`: a pre-approved command that runs more than it names.

Known and by design:

- The mailbox has no authentication. Anything that can write to the game's user data folder
  can drive a running debug build. `DevTools` does nothing in a release build.
- `devtools/allow_mutation=true` lets a client call any method of any node: code execution
  for whoever can write the user data folder. `run-method` refuses `DevTools` as its target;
  a method of another node reaches it, so that is not a boundary. The setting ships `false`.
- Only the Godot that `tools/install_godot.ps1` downloads is checked against a hash. One
  found through `GODOT_BIN`, `godot.search_roots` or `PATH` is run as it is.
- The gates run the project's own scripts and tests.
