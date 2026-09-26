import type { Metadata } from "next";
import { SourcesView } from "./sources-view";

export const metadata: Metadata = { title: "Sursele noastre · Spott" };

export default function SourcesPage() {
  return <SourcesView />;
}
