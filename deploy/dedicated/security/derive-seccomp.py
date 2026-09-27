"""Derive the worker's seccomp profile from Docker's default profile (ADR-0025).

    cd deploy/dedicated/security
    python derive-seccomp.py <docker default.json> > seccomp-worker.json

<docker default.json> is ``seccomp/default.json`` of https://github.com/moby/profiles (the profile
dockerd builds in as ``builtin``). The only change: Chromium's namespace sandbox may create a user
(and PID and network) namespace without CAP_SYS_ADMIN and chroot into an empty directory inside it.
Docker's default allows ``unshare`` only with CAP_SYS_ADMIN, ``clone`` only without namespace
flags and ``chroot`` only with CAP_SYS_CHROOT (the container drops every capability); this profile
drops the flag-filtered ``clone`` rules and allows ``chroot``, ``clone`` and ``unshare`` outright.
Measured on the worker image (ADR-0025 amendment): without ``chroot`` or ``unshare`` the sandbox
still fails; ``setns`` is not needed. Outside a user namespace ``chroot`` still needs
CAP_SYS_CHROOT, which the container does not have. Everything else (``setns``, ``mount``,
``bpf``, ``keyctl``, ``clone3`` answering ENOSYS so glibc falls back to ``clone``, ...) stays
exactly as in the default. Source used for the committed profile: moby/profiles commit
85e237f1fe229a0c61c9c7d8e743fa780d3b97ca (2026-09-25).

The same file is copied to infra/terraform/modules/ecs_ec2_capacity/files/ for the shared tier's
EC2 capacity (apps/api/tests/deploy/test_chromium_sandbox.py checks that both copies match and that
the delta is exactly the one above). Standard library only.
"""

from __future__ import annotations

import json
import sys
from typing import Any

SANDBOX_RULE: dict[str, Any] = {
    "names": ["chroot", "clone", "unshare"],
    "action": "SCMP_ACT_ALLOW",
    "comment": (
        "SchoolOS worker (ADR-0025): Chromium's namespace sandbox creates user, PID and network "
        "namespaces and chroots inside them without host capabilities"
    ),
}


def derive(default: dict[str, Any]) -> dict[str, Any]:
    rules = [
        rule
        for rule in default["syscalls"]
        if not (
            rule["names"] == ["clone"] and rule["action"] == "SCMP_ACT_ALLOW" and rule.get("args")
        )
    ]
    if len(rules) == len(default["syscalls"]):
        raise SystemExit("no flag-filtered clone rule found: is this Docker's default profile?")
    return {**default, "syscalls": [*rules, SANDBOX_RULE]}


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__ or "")
        return 2
    with open(sys.argv[1], encoding="utf-8") as fh:
        default = json.load(fh)
    # LF line endings on every platform.
    sys.stdout.buffer.write((json.dumps(derive(default), indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
