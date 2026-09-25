import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Asistentul Primăriei Chișinău",
  description: "Răspunsuri din documentele publice ale Primăriei, cu surse / Ответы по публичным документам Примэрии, с источниками",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ro" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
