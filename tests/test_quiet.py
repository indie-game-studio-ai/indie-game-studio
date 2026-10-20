import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

QUIET = Path(__file__).resolve().parent.parent / "plugins" / "game-studio" / "skills" / "game-studio" / "tools" / "quiet.py"


class QuietTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / "run.log"

    def tearDown(self):
        self.temp.cleanup()

    def quiet(self, command, *args):
        return subprocess.run(
            [sys.executable, str(QUIET), "--log", str(self.log), *args, command],
            capture_output=True, text=True,
        )

    def test_success_prints_a_status_line_and_keeps_the_full_log(self):
        noisy = f"{sys.executable} -c \"[print('line', n) for n in range(500)]; print('12 passed')\""
        result = self.quiet(noisy)
        self.assertEqual(result.returncode, 0)
        self.assertIn("exit 0", result.stdout)
        self.assertIn("12 passed", result.stdout)
        self.assertLess(len(result.stdout.splitlines()), 6)
        self.assertEqual(len(self.log.read_text().splitlines()), 502)

    def test_failure_prints_error_lines_and_keeps_the_exit_code(self):
        failing = (
            f"{sys.executable} -c \"[print('noise', n) for n in range(300)]; "
            "print('AssertionError: expected 3 lives, got 2'); import sys; sys.exit(3)\""
        )
        result = self.quiet(failing, "--tail", "2")
        self.assertEqual(result.returncode, 3)
        self.assertIn("exit 3", result.stdout)
        self.assertIn("AssertionError: expected 3 lives, got 2", result.stdout)
        self.assertLess(len(result.stdout.splitlines()), 10)


if __name__ == "__main__":
    unittest.main()
