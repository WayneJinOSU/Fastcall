# syntax=docker/dockerfile:1
ARG DEBIAN_IMAGE=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2

FROM ${DEBIAN_IMAGE} AS build

ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security
ENV DEBIAN_FRONTEND=noninteractive
RUN sed -i \
        -e "s|http://deb.debian.org/debian-security\$|${DEBIAN_SECURITY_MIRROR}|" \
        -e "s|http://deb.debian.org/debian\$|${DEBIAN_MIRROR}|" \
        /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake libtool libtool-bin cmake g++ make pkg-config \
    libssl-dev libspeex-dev libspeexdsp-dev libopus-dev \
    libncurses-dev libsqlite3-dev libpcre2-dev \
    libcurl4-openssl-dev libedit-dev libldns-dev \
    libsndfile1-dev libtiff-dev libjpeg-dev \
    uuid-dev libasound2-dev nasm git curl ca-certificates xz-utils python3 \
    && rm -rf /var/lib/apt/lists/*

ARG PCRE_VERSION=8.45
ARG PCRE_SHA256=4e6ce03e0336e8b4a3d6c2b70b1c5e18590a5673a98186da90d4f33c23defc09
WORKDIR /usr/src
RUN curl -fL --retry 3 "https://sourceforge.net/projects/pcre/files/pcre/${PCRE_VERSION}/pcre-${PCRE_VERSION}.tar.gz" -o pcre.tar.gz \
    && echo "${PCRE_SHA256}  pcre.tar.gz" | sha256sum -c - \
    && tar xzf pcre.tar.gz \
    && cd "pcre-${PCRE_VERSION}" \
    && ./configure --prefix=/usr/local --enable-utf8 --enable-unicode-properties \
    && make -j"$(nproc)" && make install && ldconfig \
    && rm -rf /usr/src/pcre*

ARG SPANDSP_COMMIT=8f1e1646bdec99eac5fd2cd92c35563f736b9b89
WORKDIR /usr/src/spandsp
RUN git init . \
    && git remote add origin https://github.com/freeswitch/spandsp.git \
    && git fetch --depth 1 origin "${SPANDSP_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && ./bootstrap.sh \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" && make install && ldconfig \
    && rm -rf /usr/src/spandsp

ARG SOFIA_SIP_COMMIT=3cc785eeb186d6103ea628d15ec4915f1a59be1b
WORKDIR /usr/src/sofia-sip
RUN git init . \
    && git remote add origin https://github.com/freeswitch/sofia-sip.git \
    && git fetch --depth 1 origin "${SOFIA_SIP_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && ./bootstrap.sh \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" && make install && ldconfig \
    && rm -rf /usr/src/sofia-sip

ARG FREESWITCH_VERSION=1.10.12
ARG FREESWITCH_SHA256=eb018ecf91d2c55ec825ae25ba72dc969acd10da9f6d2286b111eef749db553b
WORKDIR /usr/src
RUN curl -fL --retry 3 "https://github.com/signalwire/freeswitch/archive/refs/tags/v${FREESWITCH_VERSION}.tar.gz" -o freeswitch.tar.gz \
    && echo "${FREESWITCH_SHA256}  freeswitch.tar.gz" | sha256sum -c - \
    && tar xzf freeswitch.tar.gz \
    && cd "freeswitch-${FREESWITCH_VERSION}" \
    && ./bootstrap.sh -j \
    && sed -i -E 's|^(applications/mod_signalwire)|#\1|' modules.conf \
    && sed -i -E 's|^(endpoints/mod_verto)|#\1|' modules.conf \
    && sed -i -E 's|^(applications/mod_av)|#\1|' modules.conf \
    && sed -i -E 's|^(applications/mod_conference)|#\1|' modules.conf \
    && sed -i -E 's|^(languages/mod_lua)|#\1|' modules.conf \
    && sed -i -E 's|^(databases/mod_pgsql)|#\1|' modules.conf \
    && sed -i -E 's|^(applications/mod_spandsp)|#\1|' modules.conf \
    && ./configure --prefix=/usr/local/freeswitch \
        CFLAGS="-Wno-error=stringop-truncation -Wno-error=deprecated-declarations -Wno-error=incompatible-pointer-types" \
    && make -j"$(nproc)" && make install \
    && rm -rf /usr/src/freeswitch*

ARG MOD_AUDIO_STREAM_COMMIT=a25fb1fe530ec6a612d321ff04f70be69b1a257c
WORKDIR /usr/src/mod_audio_stream
RUN git init . \
    && git remote add origin https://github.com/AlchemillaHQ/mod-audio-stream.git \
    && git fetch --depth 1 origin "${MOD_AUDIO_STREAM_COMMIT}" \
    && git checkout --detach FETCH_HEAD
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_LOCAL=ON -DBUILD_TESTING=OFF \
    && cmake --build build --parallel "$(nproc)" \
    && cmake --install build \
    && rm -rf /usr/src/mod_audio_stream

FROM ${DEBIAN_IMAGE}

ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security
ENV DEBIAN_FRONTEND=noninteractive
RUN sed -i \
        -e "s|http://deb.debian.org/debian-security\$|${DEBIAN_SECURITY_MIRROR}|" \
        -e "s|http://deb.debian.org/debian\$|${DEBIAN_MIRROR}|" \
        /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 passwd \
    libasound2t64 libcurl4t64 libedit2 libjpeg62-turbo libldns3t64 \
    libncurses6 libopus0 libsndfile1 libspeex1 libspeexdsp1 libsqlite3-0 \
    libssl3t64 libtiff6 libuuid1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system freeswitch \
    && useradd --system --gid freeswitch --no-create-home --home-dir /usr/local/freeswitch freeswitch

COPY --from=build /usr/local/freeswitch /usr/local/freeswitch
COPY --from=build /usr/local/lib /usr/local/lib
RUN echo "/usr/local/freeswitch/lib" > /etc/ld.so.conf.d/freeswitch.conf \
    && echo "/usr/local/lib" > /etc/ld.so.conf.d/local.conf \
    && ldconfig

ENV PATH="/usr/local/freeswitch/bin:${PATH}"
ENV LD_LIBRARY_PATH="/usr/local/freeswitch/lib:/usr/local/lib"
ENV FASTCALL_SIP_ENABLED=true
ENV FREESWITCH_ESL_ALLOWED_CIDR=127.0.0.1/32
ENV FREESWITCH_RTP_START_PORT=16384
ENV FREESWITCH_RTP_END_PORT=16483

RUN rm -rf /usr/local/freeswitch/etc/freeswitch/dialplan/default \
        /usr/local/freeswitch/etc/freeswitch/dialplan/public \
        /usr/local/freeswitch/etc/freeswitch/sip_profiles/internal.xml \
        /usr/local/freeswitch/etc/freeswitch/sip_profiles/internal-ipv6.xml \
        /usr/local/freeswitch/etc/freeswitch/sip_profiles/external-ipv6.xml \
        /usr/local/freeswitch/etc/freeswitch/sip_profiles/external-ipv6 \
        /usr/local/freeswitch/etc/freeswitch/sip_profiles/external

COPY deploy/freeswitch/config/ /usr/local/freeswitch/etc/freeswitch/
COPY deploy/freeswitch/templates/ /usr/local/share/fastcall-freeswitch/templates/
COPY deploy/freeswitch/entrypoint.py /usr/local/share/fastcall-freeswitch/entrypoint.py
RUN mkdir -p /usr/local/freeswitch/etc/freeswitch/sip_profiles/external \
    && chown -R freeswitch:freeswitch /usr/local/freeswitch

USER root
EXPOSE 5080/tcp 5080/udp 7443/tcp 8021/tcp
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD test "$(fs_cli -p "$FREESWITCH_ESL_PASSWORD" -x 'module_exists mod_sofia')" = "true" \
        && test "$(fs_cli -p "$FREESWITCH_ESL_PASSWORD" -x 'module_exists mod_audio_stream')" = "true"
ENTRYPOINT ["python3", "/usr/local/share/fastcall-freeswitch/entrypoint.py"]
CMD ["freeswitch", "-nonat"]
