// Full-tree npm audit at high severity with a reviewed, dated exception list (SEC-009).
// Production dependencies are audited separately with no exceptions (`npm audit --omit=dev`).
// Fails when: a high/critical advisory is not listed, a listed advisory reaches a production
// dependency, an entry is past its review_by date, or an entry no longer matches anything.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

const LEVELS = { info: 0, low: 1, moderate: 2, high: 3, critical: 4 };
const config = JSON.parse(readFileSync(new URL("../.npm-audit-exceptions.json", import.meta.url)));
const today = new Date().toISOString().slice(0, 10);

function audit(extra) {
  // Constant arguments only; a shell lets `npm` resolve to npm.cmd on Windows.
  const r = spawnSync(["npm", "audit", "--json", ...extra].join(" "), {
    encoding: "utf8",
    shell: true,
    maxBuffer: 64 * 1024 * 1024,
  });
  if (!r.stdout) {
    console.error(r.stderr);
    process.exit(2);
  }
  const report = JSON.parse(r.stdout);
  if (report.error) {
    console.error(JSON.stringify(report.error));
    process.exit(2);
  }
  return report;
}

function advisories(report) {
  const found = new Map();
  for (const vuln of Object.values(report.vulnerabilities ?? {})) {
    for (const via of vuln.via) {
      if (typeof via !== "object" || LEVELS[via.severity] < LEVELS.high) continue;
      const id = via.url.split("/").pop();
      found.set(id, { id, name: via.name, severity: via.severity, title: via.title });
    }
  }
  return found;
}

const full = advisories(audit([]));
const prod = advisories(audit(["--omit=dev"]));
const errors = [];
const allowed = new Map(config.exceptions.map((e) => [e.id, e]));

for (const e of config.exceptions) {
  if (!e.reason || !e.review_by) errors.push(`${e.id}: exception needs a reason and review_by`);
  if (e.review_by < today) errors.push(`${e.id}: exception expired on ${e.review_by}; review it`);
  if (!full.has(e.id)) errors.push(`${e.id}: exception no longer matches an advisory; remove it`);
}
for (const a of full.values()) {
  const e = allowed.get(a.id);
  if (!e) errors.push(`${a.id} (${a.name}, ${a.severity}): ${a.title}`);
  else if (prod.has(a.id)) errors.push(`${a.id} (${a.name}): excepted as dev-only but reaches production`);
  else console.log(`npm audit: ${a.id} (${a.name}, ${a.severity}) excepted until ${e.review_by}`);
}
if (errors.length) {
  console.error("npm audit (full tree) failed:\n  " + errors.join("\n  "));
  process.exit(1);
}
console.log("npm audit (full tree): no unreviewed high or critical advisories");
