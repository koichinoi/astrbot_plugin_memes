import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("memes_test_state", Path(__file__).resolve().parents[1] / "state.py")
state_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_module)
MemeState = state_module.MemeState


class MemeStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "nested" / "state.sqlite3"
        self.state = MemeState(self.path)

    def tearDown(self):
        self.state.close()
        self.directory.cleanup()

    def test_scope_blacklist_default_and_reenable(self):
        self.assertTrue(self.state.enabled("group:a", "meme"))
        self.state.set_enabled("group:a", "meme", False)
        self.assertFalse(self.state.enabled("group:a", "meme"))
        self.assertTrue(self.state.enabled("group:b", "meme"))
        self.assertTrue(self.state.enabled("group:a", "other"))
        self.state.set_enabled("group:a", "meme", True)
        self.assertTrue(self.state.enabled("group:a", "meme"))

    def test_mode_switch_retains_separate_white_and_black_memberships(self):
        self.state.set_enabled("group:a", "meme", False)
        self.state.set_global("meme", False)
        self.assertFalse(self.state.enabled("group:a", "meme"))
        self.assertFalse(self.state.enabled("group:b", "meme"))
        self.state.set_enabled("group:b", "meme", True)
        self.assertTrue(self.state.enabled("group:b", "meme"))
        self.state.set_global("meme", True)
        self.assertFalse(self.state.enabled("group:a", "meme"))
        self.assertTrue(self.state.enabled("group:b", "meme"))
        self.state.set_global("meme", False)
        self.assertFalse(self.state.enabled("group:a", "meme"))
        self.assertTrue(self.state.enabled("group:b", "meme"))
        self.state.set_enabled("group:b", "meme", False)
        self.assertFalse(self.state.enabled("group:b", "meme"))

    def test_restart_preserves_modes_rules_and_statistics(self):
        self.state.set_enabled("group:a", "black", False)
        self.state.set_global("white", False)
        self.state.set_enabled("group:b", "white", True)
        self.state.record("group:b", "user:1", "white")
        self.state.close()
        self.state = MemeState(self.path)
        self.assertFalse(self.state.enabled("group:a", "black"))
        self.assertFalse(self.state.enabled("group:a", "white"))
        self.assertTrue(self.state.enabled("group:b", "white"))
        self.assertEqual(self.state.statistics(), [("white", 1)])

    def test_statistics_scope_user_time_key_and_limit(self):
        entries = [("a", "1", "cat", 10), ("a", "1", "cat", 20), ("a", "2", "dog", 30), ("b", "1", "dog", 40), ("b", "2", "cat", 50)]
        for scope, user, key, timestamp in entries:
            with patch.object(state_module.time, "time", return_value=timestamp):
                self.state.record(scope, user, key)
        self.assertEqual(self.state.statistics(), [("cat", 3), ("dog", 2)])
        self.assertEqual(self.state.statistics(scope="a"), [("cat", 2), ("dog", 1)])
        self.assertEqual(self.state.statistics(scope="b"), [("cat", 1), ("dog", 1)])
        self.assertEqual(self.state.statistics(user="1"), [("cat", 2), ("dog", 1)])
        self.assertEqual(self.state.statistics(scope="b", user="1"), [("dog", 1)])
        self.assertEqual(self.state.statistics(since=30), [("dog", 2), ("cat", 1)])
        self.assertEqual(self.state.statistics(key="dog", limit=1), [("dog", 2)])
        self.assertEqual(self.state.statistics(limit=0), [])
        self.assertEqual(self.state.statistics(limit=-1), [])
        self.assertEqual(self.state.statistics(since=100), [])

    def test_sql_like_identifiers_are_stored_literally(self):
        key = "meme'); DROP TABLE generation_records; --"
        self.state.set_enabled("", key, False)
        self.assertFalse(self.state.enabled("", key))
        self.state.record("", "user'", key)
        self.state.record("not-empty", "user'", key)
        self.assertEqual(self.state.statistics(scope="", user="user'", key=key), [(key, 1)])


if __name__ == "__main__":
    unittest.main()
