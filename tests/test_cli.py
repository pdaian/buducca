import unittest

from assistant_framework.cli import build_parser


class CLITests(unittest.TestCase):
    def test_compressors_command_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["compressors"])
        self.assertEqual(args.workspace, "workspace")
        self.assertEqual(args.compressors, "compressors")
        self.assertEqual(args.config, "compressors/config.json")


if __name__ == "__main__":
    unittest.main()
