import unittest

from app.core.cd_command import is_cd_command
from app.models import FavoriteCommand


class CdCommandTests(unittest.TestCase):
    def test_detects_cd_variants(self) -> None:
        for command in (
            "cd /tmp",
            "cd -- '/srv/my app'",
            "builtin cd ..",
            "command cd -",
            "PROJECT=demo cd /srv/demo",
        ):
            with self.subTest(command=command):
                self.assertTrue(is_cd_command(command))

    def test_does_not_match_unrelated_commands(self) -> None:
        for command in ("echo cd /tmp", "sudo cd /tmp", "cdrom", ""):
            with self.subTest(command=command):
                self.assertFalse(is_cd_command(command))

    def test_favorite_follow_tree_round_trip(self) -> None:
        favorite = FavoriteCommand(command="cd /srv/demo", follow_tree=True)
        restored = FavoriteCommand.from_dict(favorite.to_dict())
        self.assertTrue(restored.follow_tree)
        self.assertEqual(restored.command, "cd /srv/demo")


if __name__ == "__main__":
    unittest.main()
