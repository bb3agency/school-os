import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Icon } from "@/components/ui/Icon";
import { Link } from "@/i18n/navigation";
import { formatDateTime } from "@/lib/format";
import { clockFromMinutes, type IncidentClock } from "./data";

/**
 * In-console incident clock alerts (D49 part 2; FR-DPDP-031): each open clock that is due soon
 * or overdue, from the API (the same rules as the CloudWatch alarms). Shown on the operator
 * dashboard and above the incidents list to holders of `platform.incidents.read`. Ids, the
 * school's tenant code and times only; nothing when no clock is alerting.
 */
export function IncidentClockAlerts({ clocks }: { clocks: readonly IncidentClock[] }) {
  const t = useTranslations("platform.incidents");
  if (clocks.length === 0) return null;
  const overdue = clocks.some((clock) => clock.stage === "overdue");
  return (
    <section aria-label={t("clockAlerts.label")}>
      <Alert
        tone={overdue ? "danger" : "warning"}
        title={t("clockAlerts.title", { count: clocks.length })}
      >
        <ul className="divide-y divide-current/15">
          {clocks.map((clock) => {
            const words = clockFromMinutes(clock);
            const left =
              words.state === "overdue"
                ? t("clock.overdueBy", { hours: words.hours })
                : words.state === "dueSoon"
                  ? t("clock.dueSoon")
                  : t("clock.dueIn", { hours: words.hours });
            return (
              <li
                key={`${clock.incident_id}-${clock.clock}-${clock.tenant_id ?? ""}`}
                className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2 first:pt-1 last:pb-0"
              >
                <span className="flex min-w-0 flex-wrap items-center gap-2">
                  <Pill
                    variant={clock.severity === "P1" ? "negative" : "tag"}
                    size="sm"
                    icon={null}
                  >
                    {t(`severity.${clock.severity}`)}
                  </Pill>
                  <span className="font-semibold break-anywhere">
                    {clock.clock === "cert_in"
                      ? t("clockAlerts.certIn")
                      : t("clockAlerts.schoolNotice", {
                          school: clock.tenant_code ?? clock.tenant_id ?? "",
                        })}
                  </span>
                  <span className="num">
                    {t("clockAlerts.due", { when: formatDateTime(clock.due_at) ?? "", left })}
                  </span>
                </span>
                <Link
                  href={`/platform/incidents/${clock.incident_id}`}
                  className="inline-flex items-center gap-1 rounded-sm font-semibold underline underline-offset-4"
                >
                  {t("clockAlerts.open")}
                  <Icon name="arrowRight" className="size-4" />
                </Link>
              </li>
            );
          })}
        </ul>
      </Alert>
    </section>
  );
}
