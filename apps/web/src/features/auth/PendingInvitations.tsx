"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { createBffClient } from "@/lib/bff/fetch";
import { defaultNavigate, type Navigate } from "@/lib/bff/session-client";

interface Invitation {
  membership_id: string;
  school_name: string;
  roles: string[];
}

/**
 * Invitations to a person who already has a SchoolOS account wait for their own answer (audit
 * DL-09): list them (GET /me/invitations) with Accept and Decline. Accepting goes to the school
 * picker; declining removes the invitation and the school never sees your contact details.
 */
export function PendingInvitations({ navigate = defaultNavigate }: { navigate?: Navigate }) {
  const t = useTranslations("invitations");
  const locale = useLocale();
  const [items, setItems] = useState<Invitation[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    const api = createBffClient({ kind: "staff", locale, navigate });
    api
      .GET("/api/v1/me/invitations")
      .then(({ data }) => {
        if (live && data) setItems(data.data);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [locale, navigate]);

  async function answer(id: string, verb: "accept" | "decline") {
    setBusy(id);
    setFailed(false);
    try {
      const api = createBffClient({ kind: "staff", locale, navigate });
      const path =
        verb === "accept"
          ? "/api/v1/me/invitations/{membership_id}/accept"
          : "/api/v1/me/invitations/{membership_id}/decline";
      const { response } = await api.POST(path, { params: { path: { membership_id: id } } });
      if (!response.ok) {
        setFailed(true);
        return;
      }
      if (verb === "accept") {
        navigate("/choose-school");
        return;
      }
      setItems((current) => current.filter((item) => item.membership_id !== id));
    } catch {
      setFailed(true);
    } finally {
      setBusy(null);
    }
  }

  if (items.length === 0) return null;
  return (
    <Card title={t("title")}>
      <p className="mb-3 text-sm text-ink-muted">{t("body")}</p>
      <ul className="space-y-3">
        {items.map((item) => (
          <li key={item.membership_id} className="flex flex-wrap items-center gap-3">
            <span className="font-semibold">{item.school_name}</span>
            <span className="text-sm text-ink-muted">{item.roles.join(", ")}</span>
            <Button
              onClick={() => answer(item.membership_id, "accept")}
              disabled={busy !== null}
              aria-label={t("acceptLabel", { school: item.school_name })}
            >
              {t("accept")}
            </Button>
            <Button
              variant="secondary"
              onClick={() => answer(item.membership_id, "decline")}
              disabled={busy !== null}
              aria-label={t("declineLabel", { school: item.school_name })}
            >
              {t("decline")}
            </Button>
          </li>
        ))}
      </ul>
      {failed ? (
        <Alert tone="danger" live>
          {t("failed")}
        </Alert>
      ) : null}
    </Card>
  );
}
