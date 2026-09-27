"use client";

import { useRouter } from "next/navigation";
import { GapsSection } from "@/components/admin/gaps-section";
import { PageHeader } from "@/components/admin/page-header";
import { ADMIN_UI } from "@/lib/admin-i18n";
import { useUILang } from "@/lib/lang";

/** What people asked that the documents don't (fully) answer: its own page, next to the sources it leads to. */
export default function GapsPage() {
  const lang = useUILang();
  const t = ADMIN_UI[lang];
  const router = useRouter();
  return (
    <>
      <PageHeader subtitle={t.gaps.subtitle} title={t.gaps.title} />
      <GapsSection lang={lang} onAddSource={() => router.push("/admin/sources#add")} t={t} />
    </>
  );
}
