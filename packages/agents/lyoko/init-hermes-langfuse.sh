#!/bin/sh
set -e
uv_bin=$(find /opt/hermes/tools/ -name uv -type f 2>/dev/null | head -n 1)
if [ -n "$uv_bin" ]; then
    echo "[cont-init] Installing / verifying langfuse in Hermes environments..."
    if [ -x "/opt/hermes/.venv/bin/python" ]; then
        $uv_bin pip install --python /opt/hermes/.venv/bin/python langfuse==4.15.2 || true
    fi
    for py in /opt/data/installs/*/environments/*/venv/bin/python; do
        if [ -x "$py" ]; then
            echo "[cont-init] Installing langfuse in active env $py"
            $uv_bin pip install --python "$py" langfuse==4.15.2 || true
        fi
    done
fi
