# ADR-0025: Chromium sandbox for PDF rendering on Fargate and dedicated hosts

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-27 |
| Deciders | Product owner (accepted 2026-09-27) |
| Amends / supersedes | none. Would change how docs/07 §10 ("Chromium runs sandboxed") is met; does not change the promise itself. |

## Context

PDF exports (FR-EXP-002..004) print HTML templates with headless Chromium through Playwright in the worker, queue `pdf` (CLAUDE.md §3, docs/04 §6). docs/07 §10 says Chromium runs **sandboxed** with network disabled except for local assets. `app/exports/pdf.py` launches with `chromium_sandbox=True` in staging and prod (`pdf.chromium_sandbox` in `app/exports/config.yaml`), keeps JavaScript off, refuses every network request, and serves only the bundled font. The worker image now carries chrome-headless-shell 1194 (Chromium 141) and runs as UID 10001 with a read-only root filesystem, all capabilities dropped and `no-new-privileges`.

Chromium's Linux sandbox has two layers. Layer 1 needs either **unprivileged user namespaces** (`clone`/`unshare` with `CLONE_NEWUSER`, the "namespace sandbox") or the **setuid helper** `chrome-sandbox`. Layer 2 is seccomp-BPF, which it installs itself. Without layer 1, Chromium refuses to start unless it is told `--no-sandbox`, which turns both layers off.

Facts checked on 2026-09-27:

- **Docker's default seccomp profile** blocks `unshare` and `clone`/`clone3` with namespace flags unless the container has `CAP_SYS_ADMIN`. Measured with the built worker image (Docker 29.7, kernel 6.6): as UID 10001 with `--cap-drop ALL --security-opt no-new-privileges:true --read-only`, launching with the sandbox fails ("Chromium sandboxing failed!"); the same container with the sandbox off renders the PDF; with `--security-opt seccomp=unconfined` (test only) the **sandboxed** render succeeds non-root with all capabilities dropped. So on this kernel the only blocker is the seccomp profile. Playwright's Docker guidance for untrusted content is the same: a non-root user plus a seccomp profile that is Docker's default with `clone`, `setns` and `unshare` allowed.
- **ECS Fargate:** task definitions cannot set a seccomp profile (`dockerSecurityOptions` is not valid on Fargate, and its allowed values do not include seccomp even on EC2), `privileged` is not supported, and `linuxParameters.capabilities.add` accepts only `SYS_PTRACE`. Creating a user namespace in a Fargate task fails with `EPERM` (aws/containers-roadmap#2102, open since 2023). The setuid helper cannot help: with no capabilities in the bounding set a setuid-root binary gains nothing, and `no-new-privileges` blocks setuid anyway. **The Chromium sandbox cannot run on Fargate.**
- **Dedicated hosts** (Ubuntu 24.04, Docker, `deploy/dedicated/compose.yaml`): Compose can set `security_opt: [seccomp=<profile>]` per service, so the seccomp blocker can be removed for the worker only. Ubuntu 24.04 also sets `kernel.apparmor_restrict_unprivileged_userns=1`: a process may create a user namespace only if its AppArmor profile allows `userns`. Containers run under `docker-default`, which (as far as we can verify) does not, so a worker-specific AppArmor profile (docker-default rules plus `userns,`) loaded by `bootstrap-host.sh` would be needed too. Not yet verified on a real Ubuntu 24.04 host (the test machine has no AppArmor).
- Allowing user namespaces widens the kernel attack surface of that container (user namespaces expose code paths that have had local privilege-escalation bugs). It is still far narrower than `--no-sandbox`, where a renderer bug runs with the worker's full identity: its database role (`sos_app`, which can set any school's tenant context), its task role (S3 files of every school, KMS data key) and its secrets.
- `/dev/shm` is 64 MB on Fargate (`sharedMemorySize` unsupported). Playwright already passes `--disable-dev-shm-usage`, so this is not a blocker.

Current state (fail closed): the worker image and CI render PDFs **unsandboxed only in CI's smoke test**. In staging and prod the renderer still requires the sandbox, so on Fargate every PDF render fails with `pdf_render_failed` (XLSX exports are unaffected). `--no-sandbox` has **not** been added anywhere that handles school data.

## Options

| Option | Sandbox | Cost / risk |
|---|---|---|
| **A. Shared tier: `pdf` queue on ECS EC2 capacity.** A small Auto Scaling group (ECS-optimized Amazon Linux 2023, which does not restrict unprivileged user namespaces) whose Docker daemon's default seccomp profile is Docker's default plus `clone`/`clone3`/`unshare`/`setns` (`daemon.json` `seccomp-profile`, the only way on ECS since task definitions cannot name one). A separate `pdf-worker` service (`-Q pdf`) runs there with the same hardening; every other queue stays on Fargate. | Yes | A second capacity type to patch (monthly AMI refresh, SSM, no SSH), a capacity provider, and a small always-on instance or scale-from-zero latency. Daemon-wide profile, so only the pdf-worker service may be placed there. |
| **B. Shared tier: unsandboxed on Fargate with compensating controls.** Keep JavaScript off and all requests refused, and move rendering to its own `pdf-renderer` service that receives only finished HTML and returns bytes: no database credentials, no KMS, no S3 (the `pdf` task writes the result), egress denied. | No | A renderer compromise stays inside a task that holds no school data beyond the document being printed. Needs a new internal service and message path; still contradicts docs/07 §10 as written, so docs/07 would need an explicit, owner-approved exception. |
| **C. Unsandboxed in the existing worker** (`chromium_sandbox: false`). | No | A renderer bug becomes a worker compromise: all schools' data through `sos_app` and the task role. **Not recommended.** |
| **D. Dedicated tier: per-service seccomp + AppArmor.** `compose.yaml` worker gets `security_opt: [seccomp=/opt/schoolos/…/chromium-seccomp.json, apparmor=schoolos-worker]`; `bootstrap-host.sh` installs and loads the AppArmor profile; cap_drop ALL and no-new-privileges stay. | Yes | One more host artifact to maintain; must be verified on Ubuntu 24.04 before any school goes live. |
| **E. Replace Chromium** (another renderer). | n/a | Changes CLAUDE.md §3 / ADR-0004 (Telugu shaping was the reason for Chromium). Out of scope. |

## Recommendation

- **Dedicated tier: D.** It keeps the promise with a narrow, per-service change. Verify on an Ubuntu 24.04 scratch host (the restore-test host is a good place) that the worker renders with the sandbox under the two profiles and that the other services keep `docker-default`.
- **Shared tier: A** for the long term (the sandbox as documented). If PDFs are needed on the shared tier before the EC2 capacity exists, **B** is the only acceptable interim, with an explicit docs/07 exception and a date to move to A. **C** should be rejected.
- Until a decision: keep `pdf.chromium_sandbox: true` (fail closed). PDF exports fail on Fargate, XLSX exports work.

## Consequences

- If A and D are accepted: follow-up work in `infra/terraform` (EC2 capacity provider, launch template with the daemon seccomp profile, `pdf-worker` service, `worker_queues` without `pdf` for the Fargate worker), in `deploy/dedicated` (profiles, compose `security_opt`, bootstrap), a host-level smoke test that renders with the sandbox on, and docs/10 §6 and §15.
- The CI smoke test keeps rendering unsandboxed (CI runners use Docker's default profile); a sandboxed smoke test belongs to the host/capacity verification above.

## Related requirements

FR-EXP-002, FR-EXP-003, FR-EXP-004, SEC-030, SEC-011, NFR-SEC-005; docs/04 §6, docs/07 §10, docs/10 §6 and §15, ADR-0004, ADR-0015; `apps/api/app/exports/pdf.py`, `apps/api/app/exports/config.yaml`, `apps/api/Dockerfile` (target `worker`), `infra/docker/worker-pdf-smoke.py`.

## Acceptance (2026-09-27)

Accepted by the product owner on 2026-09-27 ("go with your recommendations"). Dedicated tier: option D; shared tier: option A (EC2 capacity for the `pdf` queue). Interim B only with a docs/07 exception if PDFs are needed on the shared tier before A exists; C rejected.
