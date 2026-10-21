import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

ROOT = Path(__file__).resolve().parent.parent
ROLES = sorted(p.stem for p in (ROOT / "roles").glob("*.md") if not p.name.startswith("_"))


class BuildTest(unittest.TestCase):
    def test_generated_agents_are_up_to_date(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build.py"), "--check"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_role_exists_for_both_platforms(self):
        self.assertEqual(len(ROLES), 9)
        for role in ROLES:
            claude_agent = (ROOT / "plugins" / "game-studio" / "agents" / f"{role}.md").read_text(encoding="utf-8")
            self.assertIn(f"name: {role}\n", claude_agent)
            self.assertIn("effort: medium\n", claude_agent)
            self.assertIn("tools: Read, Write, Edit, Bash, Glob, Grep", claude_agent)
            self.assertNotIn("Agent", claude_agent.split("---", 2)[1].split("tools:", 1)[1].splitlines()[0])
            self.assertIn("## Common role rules", claude_agent)
            self.assertTrue((ROOT / "codex" / "agents" / f"studio-{role}.toml").exists())

    def test_only_the_architect_gets_web_tools(self):
        for role in ROLES:
            claude_agent = (ROOT / "plugins" / "game-studio" / "agents" / f"{role}.md").read_text(encoding="utf-8")
            self.assertEqual("WebSearch" in claude_agent.split("---", 2)[1], role == "architect", role)

    @unittest.skipIf(tomllib is None, "tomllib is available from Python 3.11")
    def test_codex_agents_are_valid_toml(self):
        for role in ROLES:
            with open(ROOT / "codex" / "agents" / f"studio-{role}.toml", "rb") as file:
                agent = tomllib.load(file)
            self.assertEqual(agent["name"], f"studio-{role}")
            self.assertTrue(agent["description"])
            self.assertEqual(agent["model_reasoning_effort"], "medium")
            self.assertIn("## Common role rules", agent["developer_instructions"])

    @unittest.skipIf(not hasattr(sys, "stdlib_module_names"), "sys.stdlib_module_names is available from Python 3.10")
    def test_no_tool_shadows_a_standard_module(self):
        # A tool runs with its own folder first on sys.path: a tool named like a standard
        # module would replace it for every other tool (wave.py broke sfx.py this way).
        tools = ROOT / "plugins" / "game-studio" / "skills" / "game-studio" / "tools"
        clashes = [path.name for path in tools.glob("*.py") if path.stem in sys.stdlib_module_names]
        self.assertEqual(clashes, [])

    def test_protocol_lists_every_role(self):
        protocol = (ROOT / "plugins" / "game-studio" / "skills" / "game-studio" / "references" / "protocol.md").read_text(encoding="utf-8")
        for role in ROLES:
            self.assertIn(f"`game-studio:{role}`", protocol)
            self.assertIn(f"`studio-{role}`", protocol)


class InstallCodexTest(unittest.TestCase):
    def run_installer(self, *args):
        return subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-codex.sh"), *args],
            capture_output=True, text=True,
        )

    def test_project_install_and_uninstall(self):
        with tempfile.TemporaryDirectory() as project:
            result = self.run_installer("--project", project)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            skill = Path(project, ".agents", "skills", "game-studio")
            self.assertTrue((skill / "SKILL.md").exists())
            self.assertTrue(Path(project, ".agents", "skills", "grill-me", "SKILL.md").exists())
            agents = sorted(p.name for p in Path(project, ".codex", "agents").iterdir())
            self.assertEqual(agents, [f"studio-{role}.toml" for role in ROLES])

            repeat = self.run_installer("--project", project)
            self.assertEqual(repeat.returncode, 0, repeat.stdout + repeat.stderr)

            self.run_installer("--project", project, "--uninstall")
            self.assertFalse(skill.exists())
            self.assertEqual(list(Path(project, ".codex", "agents").iterdir()), [])

    def test_does_not_replace_foreign_files_without_force(self):
        with tempfile.TemporaryDirectory() as project:
            foreign = Path(project, ".codex", "agents", "studio-qa.toml")
            foreign.parent.mkdir(parents=True)
            foreign.write_text("name = 'mine'\n")

            result = self.run_installer("--project", project)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(foreign.read_text(), "name = 'mine'\n")

            forced = self.run_installer("--project", project, "--force")
            self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
            self.assertTrue(foreign.is_symlink())


if __name__ == "__main__":
    unittest.main()
