import type { Metadata } from "next";
import { SourcesView } from "./sources-view";

export const metadata: Metadata = { title: "Sursele noastre · Asistentul Primăriei Chișinău" };

export default function SourcesPage() {
  return <SourcesView />;
}
