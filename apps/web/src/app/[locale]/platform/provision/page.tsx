import { useTranslations } from "next-intl";
import { PageHeader } from "@/components/ui/PageHeader";
import { ProvisionSchoolForm } from "@/features/platform/ProvisionSchoolForm";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.provision.title"));

/** FR-PLT-001..003: provision wizard → POST /api/v1/platform/tenants (step-up MFA). */
export default function ProvisionSchoolPage() {
  const t = useTranslations("platform.provision");
  const tn = useTranslations("platform.nav");
  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tn("dashboard"), href: "/platform" },
          { label: tn("schools"), href: "/platform/schools" },
          { label: t("title") },
        ]}
      />
      <ProvisionSchoolForm />
    </>
  );
}
