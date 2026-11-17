import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import bootstrap_compose


class SipSettingsTests(unittest.TestCase):
    def test_domain_is_derived_and_existing_credentials_are_preserved(self):
        result = bootstrap_compose.sip_settings({
            "FASTCALL_SIP_ENABLED": "true",
            "FREESWITCH_EXTERNAL_IP": "192.168.1.104",
            "FREESWITCH_LOCAL_USER": "8615908280801",
            "FREESWITCH_LOCAL_PASSWORD": "existing-password",
        })
        self.assertEqual(result["FREESWITCH_DOMAIN"], "192.168.1.104")
        self.assertEqual(result["FREESWITCH_LOCAL_PASSWORD"], "existing-password")
        self.assertEqual(result["FREESWITCH_WSS_PORT"], "7443")

    def test_generated_sip_password_can_be_reused_on_the_next_bootstrap(self):
        source = {
            "FASTCALL_SIP_ENABLED": "true",
            "FREESWITCH_EXTERNAL_IP": "192.168.1.104",
        }
        generated = bootstrap_compose.sip_settings(source)["FREESWITCH_LOCAL_PASSWORD"]
        self.assertGreaterEqual(len(generated), 12)
        reused = bootstrap_compose.sip_settings({
            **source,
            "FREESWITCH_LOCAL_PASSWORD": generated,
        })
        self.assertEqual(reused["FREESWITCH_LOCAL_PASSWORD"], generated)

    def test_sip_requires_an_external_ip(self):
        with self.assertRaisesRegex(RuntimeError, "FREESWITCH_EXTERNAL_IP"):
            bootstrap_compose.sip_settings({"FASTCALL_SIP_ENABLED": "true"})

    def test_sip_enabled_must_be_a_boolean(self):
        with self.assertRaisesRegex(RuntimeError, "FASTCALL_SIP_ENABLED"):
            bootstrap_compose.sip_settings({"FASTCALL_SIP_ENABLED": "perhaps"})

    def test_local_user_and_password_are_validated(self):
        source = {
            "FASTCALL_SIP_ENABLED": "true",
            "FREESWITCH_EXTERNAL_IP": "192.168.1.104",
        }
        with self.assertRaisesRegex(RuntimeError, "FREESWITCH_LOCAL_USER"):
            bootstrap_compose.sip_settings({**source, "FREESWITCH_LOCAL_USER": "1001"})
        with self.assertRaisesRegex(RuntimeError, "FREESWITCH_LOCAL_PASSWORD"):
            bootstrap_compose.sip_settings({**source, "FREESWITCH_LOCAL_PASSWORD": "1001"})

    def test_gateway_name_and_host_must_be_set_together(self):
        source = {
            "FASTCALL_SIP_ENABLED": "true",
            "FREESWITCH_EXTERNAL_IP": "192.168.1.104",
        }
        for gateway, host in (("carrier", ""), ("", "sip.example.com")):
            with self.subTest(gateway=gateway, host=host):
                with self.assertRaisesRegex(RuntimeError, "must be set together"):
                    bootstrap_compose.sip_settings({
                        **source,
                        "FREESWITCH_SIP_GATEWAY": gateway,
                        "FREESWITCH_SIP_HOST": host,
                    })
        bootstrap_compose.sip_settings({
            **source,
            "FREESWITCH_SIP_GATEWAY": "carrier",
            "FREESWITCH_SIP_HOST": "sip.example.com",
        })


class BootstrapMainTests(unittest.TestCase):
    def test_main_writes_wss_registration_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "deploy/.env.compose"
            env_file.parent.mkdir()
            env_file.write_text(
                "FASTCALL_DATABASE_URL=mysql+pymysql://fastcall:password@db:3306/fastcall\n"
                "AGENT_DATABASE_URL=mysql+pymysql://agent:password@db:3306/agents\n"
                "FASTCALL_SIP_ENABLED=true\n"
                "FREESWITCH_EXTERNAL_IP=192.168.1.104\n"
                "FREESWITCH_LOCAL_PASSWORD=\n"
            )
            with patch.object(bootstrap_compose, "ROOT", root), patch.object(
                bootstrap_compose, "ENV_FILE", env_file
            ), patch.object(bootstrap_compose, "ensure_tls") as ensure_tls, patch.object(
                bootstrap_compose.subprocess, "run"
            ) as run, patch.dict(os.environ, {}, clear=True):
                bootstrap_compose.main()
            values = bootstrap_compose.read_env(env_file)
            self.assertEqual(values["FREESWITCH_DOMAIN"], "192.168.1.104")
            self.assertEqual(values["FREESWITCH_LOCAL_USER"], "8615908280801")
            self.assertGreaterEqual(len(values["FREESWITCH_LOCAL_PASSWORD"]), 12)
            self.assertEqual(values["FREESWITCH_WSS_PORT"], "7443")
            ensure_tls.assert_called_once_with("192.168.1.104")
            command = run.call_args.args[0]
            self.assertIn("docker", command)
            self.assertIn("up", command)


class EnsureTlsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.tls_directory = Path(self.temporary_directory.name)
        self.patch = patch.object(bootstrap_compose, "TLS_DIR", self.tls_directory)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temporary_directory.cleanup()

    def test_wss_certificate_has_ip_san_and_is_reused(self):
        bootstrap_compose.ensure_tls("192.168.1.104")
        for name in ("ca.crt", "ca.key", "wss.crt", "wss.key", "wss.pem"):
            self.assertTrue((self.tls_directory / name).is_file(), name)
        certificate = subprocess.check_output([
            "openssl", "x509", "-in", str(self.tls_directory / "wss.crt"),
            "-noout", "-text",
        ], text=True)
        self.assertIn("IP Address:192.168.1.104", certificate)
        subprocess.run([
            "openssl", "verify", "-CAfile", str(self.tls_directory / "ca.crt"),
            str(self.tls_directory / "wss.crt"),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for name in ("ca.key", "wss.key", "wss.pem"):
            mode = stat.S_IMODE(os.stat(self.tls_directory / name).st_mode)
            self.assertEqual(mode, 0o600)
        expected_pem = (
            (self.tls_directory / "wss.crt").read_bytes()
            + (self.tls_directory / "wss.key").read_bytes()
        )
        self.assertEqual((self.tls_directory / "wss.pem").read_bytes(), expected_pem)
        before = (self.tls_directory / "wss.crt").read_bytes()
        (self.tls_directory / "wss.pem").write_text("stale bundle")
        bootstrap_compose.ensure_tls("192.168.1.104")
        self.assertEqual((self.tls_directory / "wss.crt").read_bytes(), before)
        self.assertEqual((self.tls_directory / "wss.pem").read_bytes(), expected_pem)

    def test_wss_certificate_has_dns_san(self):
        bootstrap_compose.ensure_tls("sip.example.com")
        certificate = subprocess.check_output([
            "openssl", "x509", "-in", str(self.tls_directory / "wss.crt"),
            "-noout", "-text",
        ], text=True)
        self.assertIn("DNS:sip.example.com", certificate)

    def test_wildcard_certificate_matches_exactly_one_dns_label(self):
        names = ["*.example.com"]
        self.assertTrue(
            bootstrap_compose.certificate_matches_domain(names, "sip.example.com")
        )
        self.assertFalse(
            bootstrap_compose.certificate_matches_domain(names, "deep.sip.example.com")
        )
        self.assertFalse(
            bootstrap_compose.certificate_matches_domain(names, "sip.example.net")
        )

    def test_certificate_and_private_key_must_match(self):
        bootstrap_compose.ensure_tls("192.168.1.104")
        subprocess.run([
            "openssl", "genrsa", "-out", str(self.tls_directory / "wss.key"), "2048",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            bootstrap_compose.ensure_tls("192.168.1.104")

    def test_existing_certificate_must_match_the_current_domain(self):
        bootstrap_compose.ensure_tls("192.168.1.104")
        before = (self.tls_directory / "wss.crt").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            bootstrap_compose.ensure_tls("192.168.1.105")
        self.assertEqual((self.tls_directory / "wss.crt").read_bytes(), before)

    def test_legacy_local_ca_requires_explicit_migration(self):
        bootstrap_compose.ensure_tls("192.168.1.104")
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(self.tls_directory / "legacy-ca.key"),
            "-out", str(self.tls_directory / "legacy-ca.crt"),
            "-subj", "/CN=Fastcall Local CA", "-days", "3650",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.replace(self.tls_directory / "legacy-ca.crt", self.tls_directory / "ca.crt")
        with self.assertRaisesRegex(RuntimeError, "lack required X.509 extensions"):
            bootstrap_compose.ensure_tls("192.168.1.104")

    def test_ipv6_wss_url_uses_brackets(self):
        self.assertEqual(
            bootstrap_compose.wss_url("2001:db8::1", "7443"),
            "wss://[2001:db8::1]:7443",
        )

    def test_incomplete_wss_files_are_not_overwritten(self):
        (self.tls_directory / "wss.crt").write_text("existing certificate")
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            bootstrap_compose.ensure_tls("192.168.1.104")
        self.assertEqual(
            (self.tls_directory / "wss.crt").read_text(), "existing certificate"
        )


if __name__ == "__main__":
    unittest.main()
