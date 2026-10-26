# Experimental wrapper layer. Native Bambu Linux acceptance is not recorded.
# The operator-supplied base must contain the complete native Bambu runtime,
# Python 3.11+, venv/pip, CA certificates and sha256sum. See LINUX.md.
ARG BAMBU_RUNTIME_IMAGE
FROM ${BAMBU_RUNTIME_IMAGE}
RUN python3 -m venv /venv
COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN /venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements-runtime.txt \
    && rm /tmp/requirements-runtime.txt
COPY dist/bambu_studio_mcp-0.1.0-py3-none-any.whl /tmp/
RUN /venv/bin/pip install --no-cache-dir --no-deps /tmp/bambu_studio_mcp-0.1.0-py3-none-any.whl \
    && rm /tmp/bambu_studio_mcp-0.1.0-py3-none-any.whl
ARG ENGINE_BINARY
ARG ENGINE_RESOURCES
ARG ENGINE_COMMIT
ARG ENGINE_EXECUTABLE_SHA256
RUN test -n "$ENGINE_BINARY" && test -n "$ENGINE_RESOURCES" \
    && test -n "$ENGINE_COMMIT" && test -n "$ENGINE_EXECUTABLE_SHA256" \
    && printf '%s  %s\n' "$ENGINE_EXECUTABLE_SHA256" "$ENGINE_BINARY" | sha256sum --check - \
    && /venv/bin/bambu-studio-mcp --engine /opt/slicer-mcp/engine.json bind-engine \
       --binary "$ENGINE_BINARY" --resources "$ENGINE_RESOURCES" --commit "$ENGINE_COMMIT"
LABEL org.opencontainers.image.source="https://github.com/MoraesGil/bambu-studio-mcp" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.title="Bambu Studio MCP"
ENTRYPOINT ["/venv/bin/bambu-studio-mcp"]
CMD ["--engine", "/opt/slicer-mcp/engine.json", "--artifacts", "/artifacts", "serve"]
