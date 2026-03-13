import tempfile
import textwrap
import unittest
from pathlib import Path

from assistant_framework.collectors import CollectorManager
from assistant_framework.skills import (
    SkillManager,
    build_skill_manifest,
    parse_args_schema_fields,
    read_skill_doc_section,
)
from assistant_framework.workspace import Workspace


class LoadingTests(unittest.TestCase):
    def test_parse_args_schema_fields_handles_json_style_schema(self) -> None:
        self.assertEqual(
            parse_args_schema_fields('{"query":"required","date":"optional/YYYY-MM-DD","max_items":20}'),
            [
                {"name": "query", "required": True, "schema": '"required"'},
                {"name": "date", "required": False, "schema": '"optional/YYYY-MM-DD"'},
                {"name": "max_items", "required": True, "schema": "20"},
            ],
        )

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

                    def register():
                        return {"name": NAME, "run": run}
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

                    def register():
                        return {"name": NAME, "run": run}
                    """
                ),
                encoding="utf-8",
            )

            manager = SkillManager(skills_dir)
            skills = manager.load()
            self.assertIn("demo_skill", skills)
            self.assertEqual(skills["demo_skill"].run(Workspace(Path(td) / "workspace"), {}), "package")

    def test_skill_manager_loads_package_skill_with_relative_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            package = skills_dir / "demo_skill"
            package.mkdir(parents=True)
            (package / "helpers.py").write_text(
                textwrap.dedent(
                    """
                    def render() -> str:
                        return "package"
                    """
                ),
                encoding="utf-8",
            )
            (package / "__init__.py").write_text(
                textwrap.dedent(
                    """
                    from .helpers import render

                    NAME = "demo_skill"

                    def run(workspace, args):
                        return render()

                    def register():
                        return {"name": NAME, "run": run}
                    """
                ),
                encoding="utf-8",
            )

            skills = SkillManager(skills_dir).load()

            self.assertEqual(skills["demo_skill"].run(Workspace(Path(td) / "workspace"), {}), "package")

    def test_skill_manager_reads_args_schema_from_skill_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            package = skills_dir / "demo_skill"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                textwrap.dedent(
                    '''
                    NAME = "demo_skill"
                    def run(workspace, args):
                        return "ok"

                    def register():
                        return {"name": NAME, "run": run}
                    '''
                ),
                encoding="utf-8",
            )
            (package / "README.md").write_text(
                textwrap.dedent(
                    '''
                    # Demo skill

                    ## Args schema
                    ```ts
                    { value?: string }
                    ```
                    '''
                ),
                encoding="utf-8",
            )

            skills = SkillManager(skills_dir).load()
            self.assertEqual(skills["demo_skill"].args_schema, "{ value?: string }")

    def test_skill_manager_loads_build_action(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir()
            (skills_dir / "demo.py").write_text(
                textwrap.dedent(
                    """
                    from assistant_framework.action_runtime import ActionEnvelope

                    NAME = "demo"

                    def build_action(args):
                        return ActionEnvelope(name="demo.write", args=args, reason="x", writes=["x.txt"], requires_approval=True)

                    def run(workspace, args):
                        return "ok"

                    def register():
                        return {"name": NAME, "run": run, "build_action": build_action}
                    """
                ),
                encoding="utf-8",
            )

            skills = SkillManager(skills_dir).load()
            action = skills["demo"].build_action({"a": 1})
            self.assertEqual(action.name, "demo.write")

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

                    def register():
                        return {"name": NAME, "run": run, "requires_llm_response": REQUIRES_LLM_RESPONSE}
                    """
                ),
                encoding="utf-8",
            )

            manager = SkillManager(skills_dir)
            skills = manager.load()

            self.assertTrue(skills["demo"].requires_llm_response)

    def test_read_skill_doc_section_ignores_code_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            readme_path = Path(td) / "README.md"
            readme_path.write_text(
                textwrap.dedent(
                    """
                    # Demo skill

                    ## What it does
                    Writes files.

                    ```bash
                    echo hidden
                    ```

                    Returns a summary.
                    """
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                read_skill_doc_section(readme_path, "What it does"),
                "Writes files.\nReturns a summary.",
            )

    def test_build_skill_manifest_exposes_prompt_and_help_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            package = skills_dir / "demo_skill"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                textwrap.dedent(
                    '''
                    NAME = "demo_skill"

                    def run(workspace, args):
                        return "ok"

                    def register():
                        return {
                            "name": NAME,
                            "description": "Demo description",
                            "run": run,
                            "requires_llm_response": True,
                            "args_schema": "{ value?: string }",
                        }
                    '''
                ),
                encoding="utf-8",
            )
            (package / "README.md").write_text(
                textwrap.dedent(
                    """
                    # Demo skill

                    ## What it does
                    Helps a human understand the skill.
                    """
                ),
                encoding="utf-8",
            )

            skill = SkillManager(skills_dir).load()["demo_skill"]
            manifest = build_skill_manifest(skill)

            self.assertEqual(manifest["prompt_surface"]["description"], "Demo description")
            self.assertEqual(manifest["prompt_surface"]["args_schema"], "{ value?: string }")
            self.assertEqual(
                manifest["prompt_surface"]["args_schema_fields"],
                [{"name": "value", "required": False, "schema": "string"}],
            )
            self.assertEqual(
                manifest["human_help_surface"]["what_it_does"],
                "Helps a human understand the skill.",
            )

    def test_skill_manager_reflects_deleted_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir()
            skill_file = skills_dir / "demo.py"
            skill_file.write_text(
                "NAME='demo'\n"
                "def run(workspace, args):\n    return 'ok'\n"
                "def register():\n    return {'name': NAME, 'run': run}\n",
                encoding="utf-8",
            )

            manager = SkillManager(skills_dir)
            self.assertIn("demo", manager.load())

            skill_file.unlink()
            self.assertNotIn("demo", manager.load())

    def test_collector_manager_loads_register_collector(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            collectors_dir = Path(td) / "collectors"
            collectors_dir.mkdir()
            (collectors_dir / "demo_collector.py").write_text(
                textwrap.dedent(
                    """
                    def register_collector(config):
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

    def test_collector_manager_load_manifests_only_includes_enabled_and_loadable_modules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            collectors_dir = Path(td) / "collectors"
            collectors_dir.mkdir()
            (collectors_dir / "good.py").write_text(
                textwrap.dedent(
                    """
                    NAME = "good"
                    DESCRIPTION = "Good collector"
                    GENERATED_FILES = ["good.recent"]
                    FILE_STRUCTURE = ["collectors/good.py", "collectors/good/README.md"]

                    def register_collector(config):
                        def run(workspace):
                            return None
                        return {
                            "name": "good",
                            "description": DESCRIPTION,
                            "interval_seconds": 5,
                            "generated_files": GENERATED_FILES,
                            "file_structure": FILE_STRUCTURE,
                            "run": run,
                        }
                    """
                ),
                encoding="utf-8",
            )
            (collectors_dir / "disabled.py").write_text(
                "def register_collector(config):\n    return {'name': 'disabled', 'run': lambda workspace: None}\n",
                encoding="utf-8",
            )
            (collectors_dir / "broken.py").write_text(
                "raise RuntimeError('boom')\n",
                encoding="utf-8",
            )

            manager = CollectorManager(
                collectors_dir,
                config={
                    "disabled": {"enabled": False},
                },
            )

            manifests = manager.load_manifests()

            self.assertEqual([manifest.name for manifest in manifests], ["good"])
            self.assertEqual(manifests[0].generated_files, ["good.recent"])
            self.assertEqual(manifests[0].description, "Good collector")

    def test_collector_manager_reflects_deleted_package_collector(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            collectors_dir = Path(td) / "collectors"
            package = collectors_dir / "demo"
            package.mkdir(parents=True)
            init_file = package / "__init__.py"
            init_file.write_text(
                textwrap.dedent(
                    """
                    def register_collector(config):
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
