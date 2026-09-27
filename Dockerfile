# Build and run with pixi, using the same locked environment as development.
FROM ghcr.io/prefix-dev/pixi:0.78.0-bookworm-slim

WORKDIR /app
COPY pixi.toml pixi.lock ./
RUN pixi install --locked

COPY companion ./companion

ENV COMPANION_ENV=production \
    PORT=8000
EXPOSE 8000
# SECRET_KEY must be provided by the host and must not change between deploys:
# every phone's settings cookie is signed with it.
CMD ["pixi", "run", "--locked", "serve"]
