"""DevTools command names: the addon's DevCommand constants == what tools/devtools.py sends."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "addons" / "agent_devtools"
DEV_COMMAND = ADDON / "dev_command.gd"
DEVTOOLS = ROOT / "tools" / "devtools.py"


def gd_commands() -> dict[str, str]:
    pattern = re.compile(r'^const (\w+): String = "([a-z_]+)"', re.MULTILINE)
    return {m.group(1): m.group(2) for m in pattern.finditer(DEV_COMMAND.read_text("utf-8"))}


def addon_sources() -> dict[str, str]:
    return {path.name: path.read_text("utf-8") for path in sorted(ADDON.glob("*.gd"))}


def sent_commands() -> set[str]:
    """Every literal action devtools.py passes to send_command."""
    sent: set[str] = set()
    for node in ast.walk(ast.parse(DEVTOOLS.read_text("utf-8"))):
        is_send = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "send_command"
            and len(node.args) >= 2
        )
        if is_send:
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sent.add(arg.value)
    return sent


class DevToolsCommandTests(unittest.TestCase):
    def test_the_constants_are_found(self) -> None:
        self.assertGreaterEqual(len(gd_commands()), 17)
        self.assertGreaterEqual(len(sent_commands()), 17)

    def test_every_sent_command_has_a_const(self) -> None:
        self.assertEqual(sorted(sent_commands() - set(gd_commands().values())), [])

    def test_every_const_is_sent_by_the_cli(self) -> None:
        self.assertEqual(sorted(set(gd_commands().values()) - sent_commands()), [])

    def test_const_names_match_values(self) -> None:
        for name, value in gd_commands().items():
            self.assertEqual(name, value.upper())

    def test_every_const_is_registered_by_the_addon(self) -> None:
        registrations = "\n".join(addon_sources().values())
        unregistered = [
            name
            for name in gd_commands()
            if not re.search(rf"DevCommand\.{name}\b\s*[:\]]", registrations)
        ]
        self.assertEqual(unregistered, [])

    def test_the_addon_registers_no_bare_command_string(self) -> None:
        bare = [
            r'_handlers(?:\[|\.(?:has|get|erase)\()\s*"',
            r'^\s*"[a-z_]+"\s*:\s*_cmd_',
        ]
        for name, source in addon_sources().items():
            for pattern in bare:
                self.assertIsNone(re.search(pattern, source, re.MULTILINE), f"{name}: {pattern}")

    def test_kit_files_declare_no_global_class_name(self) -> None:
        for name, source in addon_sources().items():
            self.assertIsNone(re.search(r"^\s*class_name\s", source, re.MULTILINE), name)


if __name__ == "__main__":
    unittest.main()
