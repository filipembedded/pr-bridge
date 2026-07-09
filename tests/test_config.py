import importlib
import unittest
from unittest import mock


class ConfigTests(unittest.TestCase):
    def setUp(self):
        # Import fresh so patched env/home is picked up per test.
        import pr_bridge.config as config

        self.config = importlib.reload(config)

    def _with_tmp_home(self, tmp_path_env):
        # Point config storage at a throwaway dir and clear provider env vars.
        return mock.patch.dict(
            "os.environ",
            {
                "APPDATA": tmp_path_env,
                "XDG_CONFIG_HOME": tmp_path_env,
            },
            clear=False,
        )

    def test_save_and_load_roundtrip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self._with_tmp_home(tmp):
                self.config.update_provider(
                    "bitbucket", {"email": "a@b.com", "api_token": "tok"}
                )
                creds = self.config.provider_credentials("bitbucket")
                self.assertEqual(creds["email"], "a@b.com")
                self.assertEqual(creds["api_token"], "tok")

    def test_update_provider_merges_and_ignores_blanks(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self._with_tmp_home(tmp):
                self.config.update_provider(
                    "bitbucket", {"email": "a@b.com", "api_token": "tok"}
                )
                # Update only the token; blank email must not wipe stored email.
                self.config.update_provider(
                    "bitbucket", {"email": "", "api_token": "tok2"}
                )
                creds = self.config.provider_credentials("bitbucket")
                self.assertEqual(creds["email"], "a@b.com")
                self.assertEqual(creds["api_token"], "tok2")

    def test_env_takes_precedence_over_stored(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self._with_tmp_home(tmp):
                self.config.update_provider(
                    "bitbucket", {"email": "stored@b.com", "api_token": "stored"}
                )
                with mock.patch.dict(
                    "os.environ",
                    {
                        "BITBUCKET_EMAIL": "env@b.com",
                        "BITBUCKET_API_TOKEN": "envtok",
                    },
                    clear=False,
                ):
                    auth = self.config.get_bitbucket_auth()
                    self.assertEqual(auth["email"], "env@b.com")
                    self.assertEqual(auth["api_token"], "envtok")

    def test_stored_used_when_env_absent(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # Replace the whole environ with a clean one (no BITBUCKET_* vars),
            # keeping only the config-dir hints.
            clean_env = {"APPDATA": tmp, "XDG_CONFIG_HOME": tmp}
            with mock.patch.dict("os.environ", clean_env, clear=True):
                self.config.update_provider(
                    "bitbucket", {"email": "stored@b.com", "api_token": "stored"}
                )
                auth = self.config.get_bitbucket_auth()
                self.assertEqual(auth["email"], "stored@b.com")
                self.assertEqual(auth["api_token"], "stored")
                self.assertIsNone(auth["access_token"])


if __name__ == "__main__":
    unittest.main()
