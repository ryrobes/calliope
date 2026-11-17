FROM rust:1.88-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked && cp target/release/clover-web /usr/local/bin/clover-web
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    set -eu; \
    mkdir -p /third-party-licenses; \
    anydoc_license="$(find /usr/local/cargo/registry/src -path '*/anydoc-0.1.3/LICENSE' -print -quit)"; \
    extractor_license="$(find /usr/local/cargo/git/checkouts -path '*/html-extractor*/LICENSE' -print -quit)"; \
    test -n "${anydoc_license}"; \
    test -n "${extractor_license}"; \
    cp "${anydoc_license}" /third-party-licenses/anydoc-MIT.txt; \
    cp "${extractor_license}" /third-party-licenses/html-extractor-APACHE-2.0.txt

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system --gid 65532 clover \
    && adduser --system --uid 65532 --ingroup clover --no-create-home clover
COPY --from=build /usr/local/bin/clover-web /usr/local/bin/clover-web
COPY THIRD_PARTY_NOTICES.md /usr/share/doc/clover-web/THIRD_PARTY_NOTICES.md
COPY --from=build /third-party-licenses /usr/share/doc/clover-web/licenses
USER 65532:65532
ENV CLOVER_WEB_BIND=127.0.0.1:8091
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8091/health || exit 1
ENTRYPOINT ["/usr/local/bin/clover-web"]
