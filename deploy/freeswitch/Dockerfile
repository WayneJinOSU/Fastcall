ARG DEBIAN_IMAGE=debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
ARG FREESWITCH_PACKAGE_VERSION=1.11.4-release-44405182291-795228ad43~bookworm~arm64-1~bookworm+1
ARG MOD_AUDIO_STREAM_COMMIT=a25fb1fe530ec6a612d321ff04f70be69b1a257c

FROM ${DEBIAN_IMAGE} AS module-build

ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security
ARG FREESWITCH_PACKAGE_VERSION
ARG MOD_AUDIO_STREAM_COMMIT
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=secret,id=signalwire_token,required=true \
    set -eu; \
    trap 'rm -f /etc/apt/auth.conf.d/freeswitch.conf' EXIT; \
    sed -i \
        -e "s|http://deb.debian.org/debian-security\$|${DEBIAN_SECURITY_MIRROR}|" \
        -e "s|http://deb.debian.org/debian\$|${DEBIAN_MIRROR}|" \
        /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    mkdir -p /etc/apt/auth.conf.d; \
    token="$(cat /run/secrets/signalwire_token)"; \
    curl -fsSL --retry 3 --user "signalwire:${token}" \
        -o /usr/share/keyrings/signalwire-freeswitch-repo.gpg \
        https://freeswitch.signalwire.com/repo/deb/debian-release/signalwire-freeswitch-repo.gpg; \
    printf 'machine freeswitch.signalwire.com login signalwire password %s\n' "$token" \
        > /etc/apt/auth.conf.d/freeswitch.conf; \
    chmod 600 /etc/apt/auth.conf.d/freeswitch.conf; \
    echo 'deb [signed-by=/usr/share/keyrings/signalwire-freeswitch-repo.gpg] https://freeswitch.signalwire.com/repo/deb/debian-release/ bookworm main' \
        > /etc/apt/sources.list.d/freeswitch.list; \
    apt-get update; \
    printf 'FreeSWITCH build architecture: %s\n' "$(dpkg --print-architecture)"; \
    apt-cache policy freeswitch; \
    apt-cache madison freeswitch || true; \
    apt-cache show freeswitch | grep -E '^(Package|Version|Filename):'; \
    apt-get install -y --no-install-recommends \
        "freeswitch=${FREESWITCH_PACKAGE_VERSION}" \
        "libfreeswitch-dev=${FREESWITCH_PACKAGE_VERSION}" \
        cmake g++ make pkg-config git libssl-dev libspeexdsp-dev libopus-dev; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/mod-audio-stream
RUN git init . \
    && git remote add origin https://github.com/AlchemillaHQ/mod-audio-stream.git \
    && git fetch --depth 1 origin "${MOD_AUDIO_STREAM_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${MOD_AUDIO_STREAM_COMMIT}" \
    && test -z "$(git status --porcelain)" \
    && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
        -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib \
    && cmake --build build --parallel "$(nproc)" \
    && cmake --install build \
    && test -f /usr/lib/freeswitch/mod/mod_audio_stream.so

FROM ${DEBIAN_IMAGE}

ARG DEBIAN_IMAGE
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security
ARG FREESWITCH_PACKAGE_VERSION
ARG MOD_AUDIO_STREAM_COMMIT
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=secret,id=signalwire_token,required=true \
    set -eu; \
    trap 'rm -f /etc/apt/auth.conf.d/freeswitch.conf' EXIT; \
    sed -i \
        -e "s|http://deb.debian.org/debian-security\$|${DEBIAN_SECURITY_MIRROR}|" \
        -e "s|http://deb.debian.org/debian\$|${DEBIAN_MIRROR}|" \
        /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    mkdir -p /etc/apt/auth.conf.d; \
    token="$(cat /run/secrets/signalwire_token)"; \
    curl -fsSL --retry 3 --user "signalwire:${token}" \
        -o /usr/share/keyrings/signalwire-freeswitch-repo.gpg \
        https://freeswitch.signalwire.com/repo/deb/debian-release/signalwire-freeswitch-repo.gpg; \
    printf 'machine freeswitch.signalwire.com login signalwire password %s\n' "$token" \
        > /etc/apt/auth.conf.d/freeswitch.conf; \
    chmod 600 /etc/apt/auth.conf.d/freeswitch.conf; \
    echo 'deb [signed-by=/usr/share/keyrings/signalwire-freeswitch-repo.gpg] https://freeswitch.signalwire.com/repo/deb/debian-release/ bookworm main' \
        > /etc/apt/sources.list.d/freeswitch.list; \
    apt-get update; \
    printf 'FreeSWITCH build architecture: %s\n' "$(dpkg --print-architecture)"; \
    apt-cache policy freeswitch; \
    apt-cache madison freeswitch || true; \
    apt-cache show freeswitch | grep -E '^(Package|Version|Filename):'; \
    apt-get install -y --no-install-recommends \
        python3 \
        "freeswitch=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-conf-vanilla=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-cdr-csv=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-commands=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-console=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-dialplan-xml=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-dptools=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-event-socket=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-logfile=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-opus=${FREESWITCH_PACKAGE_VERSION}" \
        "freeswitch-mod-sofia=${FREESWITCH_PACKAGE_VERSION}"; \
    rm -rf /var/lib/apt/lists/*

COPY --from=module-build /usr/lib/freeswitch/mod/mod_audio_stream.so /usr/lib/freeswitch/mod/mod_audio_stream.so
RUN rm -rf /etc/freeswitch/dialplan/default \
        /etc/freeswitch/dialplan/public \
        /etc/freeswitch/sip_profiles/internal.xml \
        /etc/freeswitch/sip_profiles/internal-ipv6.xml \
        /etc/freeswitch/sip_profiles/external.xml \
        /etc/freeswitch/sip_profiles/external-ipv6.xml \
        /etc/freeswitch/sip_profiles/external \
    && rm -f /etc/freeswitch/directory/default/*.xml

COPY deploy/freeswitch/config/ /etc/freeswitch/
COPY deploy/freeswitch/templates/ /usr/local/share/fastcall-freeswitch/templates/
COPY deploy/freeswitch/entrypoint.py /usr/local/share/fastcall-freeswitch/entrypoint.py
RUN mkdir -p /etc/freeswitch/sip_profiles/external \
        /var/log/freeswitch /var/lib/freeswitch /run/freeswitch \
    && chown -R freeswitch:freeswitch \
        /etc/freeswitch /var/log/freeswitch /var/lib/freeswitch /run/freeswitch \
    && test -x /usr/bin/freeswitch \
    && test -x /usr/bin/fs_cli \
    && test -d /usr/lib/freeswitch/mod \
    && dpkg -L freeswitch | grep -qx /usr/bin/freeswitch \
    && dpkg -L freeswitch-mod-sofia | grep -qx /usr/lib/freeswitch/mod/mod_sofia.so \
    && if ldd /usr/bin/freeswitch /usr/lib/freeswitch/mod/mod_audio_stream.so \
        | grep -q 'not found'; then exit 1; fi \
    && /usr/bin/freeswitch -version \
    && { \
        printf 'base_image=%s\n' "${DEBIAN_IMAGE}"; \
        printf 'architecture=%s\n' "$(dpkg --print-architecture)"; \
        printf 'mod_audio_stream_commit=%s\n' "${MOD_AUDIO_STREAM_COMMIT}"; \
        dpkg-query -W -f='${Package}=${Version}\n'; \
        sha256sum /usr/lib/freeswitch/mod/mod_audio_stream.so; \
    } > /usr/local/share/fastcall-freeswitch/build-manifest.txt \
    && test ! -e /etc/apt/auth.conf.d/freeswitch.conf

ENV FASTCALL_SIP_ENABLED=true
ENV FREESWITCH_ESL_ALLOWED_CIDR=127.0.0.1/32
ENV FREESWITCH_RTP_START_PORT=16384
ENV FREESWITCH_RTP_END_PORT=16483

USER root
EXPOSE 5080/tcp 5080/udp 7443/tcp 8021/tcp
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD test "$(fs_cli -p "$FREESWITCH_ESL_PASSWORD" -x 'module_exists mod_sofia')" = "true" \
        && test "$(fs_cli -p "$FREESWITCH_ESL_PASSWORD" -x 'module_exists mod_audio_stream')" = "true"
ENTRYPOINT ["python3", "/usr/local/share/fastcall-freeswitch/entrypoint.py"]
CMD ["/usr/bin/freeswitch", "-nonat"]
