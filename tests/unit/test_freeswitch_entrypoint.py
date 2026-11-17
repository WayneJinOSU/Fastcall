import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from deploy.freeswitch import entrypoint


class LocalUserTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.conf_directory = Path(self.temporary_directory.name)
        self.patches = [
            patch.object(entrypoint, "CONF_DIR", self.conf_directory),
            patch.object(
                entrypoint, "TEMPLATE_DIR",
                Path(entrypoint.__file__).parent / "templates",
            ),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temporary_directory.cleanup()

    def test_local_user_is_rendered_with_escaped_credentials(self):
        environment = {
            "FREESWITCH_LOCAL_USER": "8615908280801",
            "FREESWITCH_LOCAL_PASSWORD": 'password"<&12345',
        }
        with patch.dict(os.environ, environment, clear=False):
            entrypoint.configure_local_user()
        destination = self.conf_directory / "directory/default/fastcall-local-user.xml"
        root = ET.parse(destination).getroot()
        self.assertEqual(root.find("user").attrib["id"], "8615908280801")
        self.assertEqual(
            root.find("user/params/param").attrib["value"], 'password"<&12345'
        )
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_short_password_is_rejected(self):
        with patch.dict(os.environ, {
            "FREESWITCH_LOCAL_USER": "8615908280801",
            "FREESWITCH_LOCAL_PASSWORD": "1001",
        }, clear=False):
            with self.assertRaisesRegex(RuntimeError, "FREESWITCH_LOCAL_PASSWORD"):
                entrypoint.configure_local_user()

    def test_wss_files_are_copied_with_restricted_permissions(self):
        source = self.conf_directory / "mounted"
        source.mkdir()
        for name in ("wss.crt", "wss.key", "wss.pem"):
            (source / name).write_text(name)
            (source / name).chmod(0o600)
        entrypoint.prepare_tls_certificates(source)
        for name in ("wss.crt", "wss.key", "wss.pem"):
            destination = self.conf_directory / "certs" / name
            self.assertEqual(destination.read_text(), name)
            expected_mode = 0o644 if name == "wss.crt" else 0o600
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), expected_mode)

    def test_missing_wss_files_fail_before_startup(self):
        with self.assertRaisesRegex(RuntimeError, "Missing WSS certificate"):
            entrypoint.prepare_tls_certificates(self.conf_directory / "missing")

    def test_gateway_name_and_host_must_be_set_together(self):
        for gateway, host in (("carrier", ""), ("", "sip.example.com")):
            with self.subTest(gateway=gateway, host=host):
                with self.assertRaisesRegex(RuntimeError, "must be set together"):
                    entrypoint.validate_gateway_settings(gateway, host)
        entrypoint.validate_gateway_settings("carrier", "sip.example.com")
        entrypoint.validate_gateway_settings("", "")

    def test_drop_privileges_owns_configuration_logs_and_database(self):
        log_directory = self.conf_directory / "log"
        database_directory = self.conf_directory / "db"
        log_directory.mkdir()
        database_directory.mkdir()
        log_file = log_directory / "freeswitch.log"
        database_file = database_directory / "core.db"
        log_file.touch()
        database_file.touch()
        account = Mock(pw_uid=999, pw_gid=999, pw_name="freeswitch")
        with patch.object(entrypoint, "LOG_DIR", log_directory), patch.object(
            entrypoint, "DB_DIR", database_directory
        ), patch.object(entrypoint.os, "getuid", return_value=0), patch.object(
            entrypoint.pwd, "getpwnam", return_value=account
        ), patch.object(entrypoint.os, "chown") as chown, patch.object(
            entrypoint.os, "initgroups"
        ), patch.object(entrypoint.os, "setgid"), patch.object(entrypoint.os, "setuid"):
            entrypoint.drop_privileges()
        chown.assert_any_call(log_file, 999, 999, follow_symlinks=False)
        chown.assert_any_call(database_file, 999, 999, follow_symlinks=False)


if __name__ == "__main__":
    unittest.main()
