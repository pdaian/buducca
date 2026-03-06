import tempfile
import textwrap
import unittest
from pathlib import Path

from assistant_framework.collectors import CollectorManager
from assistant_framework.skills import SkillManager
from assistant_framework.workspace import Workspace


class LoadingTests(unittest.TestCase):
    def test_skill_manager_loads_python_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir()
            (skills_dir / "demo.py").write_text(
                textwrap.dedent(
                    """
                    NAME = "demo"
                    def run(workspace, args):
                        workspace.write_text("x.txt", "ok")
                        return "done"
                    """
                ),
                encoding="utf-8",
            )

            manager = SkillManager(skills_dir)
            skills = manager.load()
            ws = Workspace(Path(td) / "workspace")
            result = skills["demo"].run(ws, {})

            self.assertEqual(result, "done")
            self.assertEqual(ws.read_text("x.txt"), "ok")

    def test_skill_manager_loads_package_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            package = skills_dir / "demo_skill"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                textwrap.dedent(
                    """
                    NAME = "demo_skill"
                    def run(workspace, args):
                        return "package"
                    """
                ),
                encoding="utf-8",
            )

            manager = SkillManager(skills_dir)
            skills = manager.load()
            self.assertIn("demo_skill", skills)
            self.assertEqual(skills["demo_skill"].run(Workspace(Path(td) / "workspace"), {}), "package")


    def test_skill_manager_loads_requires_llm_response_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir()
            (skills_dir / "demo.py").write_text(
                textwrap.dedent(
                    """
                    NAME = "demo"
                    REQUIRES_LLM_RESPONSE = True
                    def run(workspace, args):
                        return "done"
                    """
                ),
                encoding="utf-8",
            )

            manager = SkillManager(skills_dir)
            skills = manager.load()

            self.assertTrue(skills["demo"].requires_llm_response)

    def test_skill_manager_reflects_deleted_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir()
            skill_file = skills_dir / "demo.py"
            skill_file.write_text("NAME='demo'\ndef run(workspace, args):\n    return 'ok'\n", encoding="utf-8")

            manager = SkillManager(skills_dir)
            self.assertIn("demo", manager.load())

            skill_file.unlink()
            self.assertNotIn("demo", manager.load())

    def test_collector_manager_loads_create_collector(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            collectors_dir = Path(td) / "collectors"
            collectors_dir.mkdir()
            (collectors_dir / "demo_collector.py").write_text(
                textwrap.dedent(
                    """
                    def create_collector(config):
                        def run(workspace):
                            workspace.write_text("collector.txt", config.get("value", "none"))
                        return {"name": "demo", "interval_seconds": 1, "run": run}
                    """
                ),
                encoding="utf-8",
            )

            manager = CollectorManager(collectors_dir, config={"demo_collector": {"value": "fresh"}})
            collectors = manager.load()

            ws = Workspace(Path(td) / "workspace")
            collectors[0].run(ws)
            self.assertEqual(ws.read_text("collector.txt"), "fresh")

    def test_collector_manager_reflects_deleted_package_collector(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            collectors_dir = Path(td) / "collectors"
            package = collectors_dir / "demo"
            package.mkdir(parents=True)
            init_file = package / "__init__.py"
            init_file.write_text(
                textwrap.dedent(
                    """
                    def create_collector(config):
                        def run(workspace):
                            workspace.write_text("collector.txt", config.get("value", "none"))
                        return {"name": "demo", "interval_seconds": 1, "run": run}
                    """
                ),
                encoding="utf-8",
            )

            manager = CollectorManager(collectors_dir, config={"demo": {"value": "fresh"}})
            self.assertEqual(len(manager.load()), 1)

            init_file.unlink()
            self.assertEqual(manager.load(), [])


if __name__ == "__main__":
    unittest.main()
