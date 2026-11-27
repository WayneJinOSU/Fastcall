from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy/freeswitch/Dockerfile"


class FreeSwitchImageTests(unittest.TestCase):
    def setUp(self):
        self.dockerfile = DOCKERFILE.read_text()

    def test_core_and_sofia_come_from_pinned_official_packages(self):
        self.assertIn("debian:bookworm-slim@sha256:", self.dockerfile)
        self.assertIn(
            'ARG FREESWITCH_PACKAGE_VERSION='
            '1.11.4-release-44405182291-795228ad43~bookworm~arm64-1~bookworm+1',
            self.dockerfile,
        )
        self.assertIn('"freeswitch=${FREESWITCH_PACKAGE_VERSION}"', self.dockerfile)
        self.assertIn(
            '"freeswitch-mod-sofia=${FREESWITCH_PACKAGE_VERSION}"',
            self.dockerfile,
        )
        self.assertIn(
            '"libfreeswitch-dev=${FREESWITCH_PACKAGE_VERSION}"',
            self.dockerfile,
        )
        for source_build in (
            "PCRE_VERSION", "SPANDSP_COMMIT", "SOFIA_SIP_COMMIT",
            "FREESWITCH_SHA256", "./configure", "git apply",
        ):
            self.assertNotIn(source_build, self.dockerfile)

    def test_module_is_unmodified_and_matches_the_runtime_abi(self):
        self.assertIn(
            "ARG MOD_AUDIO_STREAM_COMMIT="
            "a25fb1fe530ec6a612d321ff04f70be69b1a257c",
            self.dockerfile,
        )
        self.assertIn('test -z "$(git status --porcelain)"', self.dockerfile)
        self.assertIn("FROM ${DEBIAN_IMAGE} AS module-build", self.dockerfile)
        self.assertIn("COPY --from=module-build", self.dockerfile)
        self.assertIn("-DCMAKE_INSTALL_PREFIX=/usr", self.dockerfile)
        self.assertIn("-DCMAKE_INSTALL_LIBDIR=lib", self.dockerfile)
        self.assertIn("test -f /usr/lib/freeswitch/mod/mod_audio_stream.so", self.dockerfile)
        self.assertNotIn("-DENABLE_LOCAL=ON", self.dockerfile)

    def test_official_package_paths_and_build_manifest_are_verified(self):
        for path in (
            "/etc/freeswitch", "/var/log/freeswitch", "/var/lib/freeswitch",
            "/usr/lib/freeswitch/mod", "/usr/bin/freeswitch", "/usr/bin/fs_cli",
        ):
            self.assertIn(path, self.dockerfile)
        self.assertIn("dpkg -L freeswitch", self.dockerfile)
        self.assertIn("build-manifest.txt", self.dockerfile)
        self.assertIn("sha256sum /usr/lib/freeswitch/mod/mod_audio_stream.so", self.dockerfile)
        self.assertIn("grep -q 'not found'", self.dockerfile)

    def test_signalwire_token_is_only_a_build_secret(self):
        self.assertIn(
            "RUN --mount=type=secret,id=signalwire_token,required=true",
            self.dockerfile,
        )
        self.assertIsNone(re.search(r"^ARG .*TOKEN", self.dockerfile, re.MULTILINE))
        self.assertNotIn("SIGNALWIRE_TOKEN", self.dockerfile)
        self.assertIn("test ! -e /etc/apt/auth.conf.d/freeswitch.conf", self.dockerfile)


class ComposeTelephonyTests(unittest.TestCase):
    def setUp(self):
        self.compose = (ROOT / "deploy/compose.yaml").read_text()

    def test_production_environment_and_gateway_mode_reach_freeswitch(self):
        freeswitch = self.compose.split("  freeswitch:", 1)[1].split("  agent:", 1)[0]
        self.assertIn("APP_ENV: ${APP_ENV}", freeswitch)
        self.assertIn("DEBIAN_IMAGE: ${FASTCALL_DEBIAN_IMAGE:-debian:bookworm-slim@sha256:", freeswitch)
        self.assertIn("FREESWITCH_DIAL_MODE: ${FREESWITCH_DIAL_MODE:-gateway}", freeswitch)
        self.assertIn("signalwire_token", freeswitch)

    def test_official_state_paths_and_wss_source_are_used(self):
        for path in (
            "freeswitch-logs:/var/log/freeswitch",
            "freeswitch-db:/var/lib/freeswitch",
            "/run/fastcall-wss-certs/wss.crt:ro",
            "/run/fastcall-wss-certs/wss.key:ro",
            "/run/fastcall-wss-certs/wss.pem:ro",
        ):
            self.assertIn(path, self.compose)
        self.assertNotIn("/usr/local/freeswitch", self.compose)

    def test_esl_is_not_published_and_build_token_is_not_a_service_variable(self):
        freeswitch = self.compose.split("  freeswitch:", 1)[1].split("  agent:", 1)[0]
        ports = freeswitch.split("    ports:", 1)[1].split("    volumes:", 1)[0]
        self.assertNotIn("8021", ports)
        self.assertNotIn("SIGNALWIRE_TOKEN", freeswitch.split("    environment:", 1)[1])
        self.assertIn("secrets:\n  signalwire_token:\n    environment: SIGNALWIRE_TOKEN", self.compose)


if __name__ == "__main__":
    unittest.main()
