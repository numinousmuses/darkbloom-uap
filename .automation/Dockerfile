FROM node:22.22.3-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 make gcc libc6-dev curl && rm -rf /var/lib/apt/lists/*
RUN npm install -g --ignore-scripts @anthropic-ai/claude-code@2.1.241 uap-protocol@0.1.1
COPY --from=golang:1.25.0-bookworm /usr/local/go /usr/local/go
RUN node /usr/local/lib/node_modules/@anthropic-ai/claude-code/install.cjs && claude --version
ENV PATH="/usr/local/go/bin:${PATH}" HOME=/tmp/home GOMAXPROCS=2
USER node
WORKDIR /workspace
