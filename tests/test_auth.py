import importlib
import tempfile
import unittest
from unittest import mock


class AuthTests(unittest.TestCase):
    def setUp(self):
        import pr_bridge.auth as auth
        import pr_bridge.config as config

        self.config = importlib.reload(config)
        self.auth = importlib.reload(auth)

    def _with_tmp_home(self, tmp):
        return mock.patch.dict(
            "os.environ",
            {"APPDATA": tmp, "XDG_CONFIG_HOME": tmp},
            clear=False,
        )

    def test_auth_bitbucket_saves_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._with_tmp_home(tmp):
                with mock.patch("builtins.input", return_value="a@b.com"), \
                     mock.patch("getpass.getpass", return_value="tok123"):
                    self.auth.auth_bitbucket()
                creds = self.config.provider_credentials(self.config.BITBUCKET)
                self.assertEqual(creds["email"], "a@b.com")
                self.assertEqual(creds["api_token"], "tok123")

    def test_auth_bitbucket_requires_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._with_tmp_home(tmp):
                with mock.patch("builtins.input", return_value="a@b.com"), \
                     mock.patch("getpass.getpass", return_value=""):
                    with self.assertRaises(SystemExit):
                        self.auth.auth_bitbucket()
                self.assertEqual(
                    self.config.provider_credentials(self.config.BITBUCKET), {}
                )

    def test_auth_bitbucket_keeps_existing_email_as_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._with_tmp_home(tmp):
                self.config.update_provider(
                    self.config.BITBUCKET,
                    {"email": "old@b.com", "api_token": "old"},
                )
                # Pressing Enter on the email prompt keeps the stored default.
                with mock.patch("builtins.input", return_value=""), \
                     mock.patch("getpass.getpass", return_value="newtok"):
                    self.auth.auth_bitbucket()
                creds = self.config.provider_credentials(self.config.BITBUCKET)
                self.assertEqual(creds["email"], "old@b.com")
                self.assertEqual(creds["api_token"], "newtok")

    def test_auth_github_saves_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._with_tmp_home(tmp):
                with mock.patch("getpass.getpass", return_value="ghtok"):
                    self.auth.auth_github()
                creds = self.config.provider_credentials(self.config.GITHUB)
                self.assertEqual(creds["token"], "ghtok")

    def test_auth_github_blank_token_skips_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._with_tmp_home(tmp):
                with mock.patch("getpass.getpass", return_value=""):
                    self.auth.auth_github()
                self.assertEqual(
                    self.config.provider_credentials(self.config.GITHUB), {}
                )

    def test_prompt_aborts_on_keyboard_interrupt(self):
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            with self.assertRaises(SystemExit):
                self.auth._prompt("Email")


if __name__ == "__main__":
    unittest.main()
