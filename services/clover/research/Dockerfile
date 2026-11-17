FROM rust:1.88-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked && cp target/release/clover-research /usr/local/bin/clover-research

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libssl3 \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system --gid 65532 clover \
    && adduser --system --uid 65532 --ingroup clover --no-create-home clover
COPY --from=build /usr/local/bin/clover-research /usr/local/bin/clover-research
USER 65532:65532
ENV CLOVER_RESEARCH_BIND=127.0.0.1:8092
EXPOSE 8092
HEALTHCHECK --interval=30s --timeout=8s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8092/health || exit 1
ENTRYPOINT ["/usr/local/bin/clover-research"]
