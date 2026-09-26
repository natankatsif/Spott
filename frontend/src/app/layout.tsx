import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin", "latin-ext", "cyrillic"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Asistentul Primăriei Chișinău",
  description: "Răspunsuri din documentele publice ale Primăriei, cu surse / Ответы по публичным документам Примэрии, с источниками",
  // iOS "Add to Home Screen": name under the icon; the icon itself is app/apple-icon.png
  appleWebApp: { title: "Asistent", capable: true, statusBarStyle: "default" },
};

// colour of the mobile browser bar / Android task switcher, same as the page background
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#f5f6f8",
  // Android Chrome: the keyboard shrinks the layout (100dvh) instead of scrolling the page to the focused field
  interactiveWidget: "resizes-content",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ro" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
