"""Smoke check for the worker image (FR-EXP-002, docs/10 §6): print a synthetic Telugu page to PDF
with the Chromium baked into the image, through the production renderer (JavaScript off, every
network request refused, bundled font served from memory).

CI runs it in the built image as UID 10001 on a read-only root filesystem with all capabilities
dropped and no network:

    docker run --rm --read-only --tmpfs /tmp:size=256m,uid=10001,gid=10001 --cap-drop ALL \
      --security-opt no-new-privileges:true --network none \
      -v "$PWD/infra/docker/worker-pdf-smoke.py:/smoke/worker-pdf-smoke.py:ro" \
      --entrypoint python schoolos-worker:<tag> /smoke/worker-pdf-smoke.py [--sandbox]

Without ``--sandbox`` Chromium runs unsandboxed: Docker's default seccomp profile (like ECS
Fargate's) blocks the user namespace the sandbox needs (ADR-0025). Staging and prod keep
``pdf.chromium_sandbox`` required, so a render there fails instead of running unsandboxed.
``infra/docker/worker-pdf-sandbox-check.sh`` runs it with ``--sandbox`` under the worker's
seccomp (and, in CI, AppArmor) profile, as dedicated hosts and the shared tier's pdf capacity do.
Synthetic text only.
"""

from __future__ import annotations

import sys

from app.exports.pdf import FONT_FAMILY, FONT_URL, ChromiumRenderer, RenderError

HTML = f"""<!doctype html>
<html lang="te"><head><meta charset="utf-8"><style>
@font-face {{ font-family: "{FONT_FAMILY}"; src: url("{FONT_URL}"); }}
body {{ font-family: "{FONT_FAMILY}", sans-serif; }}
</style></head>
<body><h1>Synthetic check</h1><p>తెలుగు పరీక్ష</p><img src="https://example.invalid/x.png"></body>
</html>"""


def main() -> int:
    sandbox = "--sandbox" in sys.argv[1:]
    try:
        pdf = ChromiumRenderer(sandbox=sandbox, timeout_ms=60_000).render(HTML)
    except RenderError as exc:
        sys.stderr.write(f"pdf smoke FAILED (sandbox={sandbox}): {exc.__cause__}\n")
        return 1
    if not pdf.startswith(b"%PDF-"):
        sys.stderr.write("pdf smoke FAILED: output is not a PDF\n")
        return 1
    sys.stdout.write(f"pdf smoke ok (sandbox={sandbox}, {len(pdf)} bytes)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
