import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { headers } from "next/headers";
import { ThemeProvider } from "next-themes";
import "./globals.css";

const inter = Inter({ subsets: ["latin", "latin-ext", "cyrillic"], variable: "--font-inter" });

const METADATA: Metadata = {
  title: "Spott",
  description: "Răspunsuri din documentele publice ale Primăriei, cu surse / Ответы по публичным документам Примэрии, с источниками",
  // iOS "Add to Home Screen": name under the icon; the icon itself is app/apple-icon.png
  appleWebApp: { title: "Spott", capable: true, statusBarStyle: "default" },
  // the link preview: the image is app/opengraph-image.jpg (1200×630), also used for twitter:image
  openGraph: {
    type: "website",
    url: "/",
    siteName: "Spott",
    title: "Spott — a spotlight in the maze of bureaucracy",
    description: "Răspunsuri din documentele publice ale Primăriei Chișinău, cu surse / Ответы по публичным документам Примэрии, с источниками",
  },
  twitter: { card: "summary_large_image" },
};

/** Messengers fetch the preview image by its absolute URL, so the metadata needs the site's own address. Next.js
 * only knows it on Vercel production (and falls back to localhost elsewhere, which no messenger can reach):
 * NEXT_PUBLIC_SITE_URL when set, else the address this request came to — right on any domain, preview or server. */
export async function generateMetadata(): Promise<Metadata> {
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host");
  const proto = h.get("x-forwarded-proto")?.split(",")[0] ?? (host && /^(localhost|127\.)/.test(host) ? "http" : "https");
  const base = process.env.NEXT_PUBLIC_SITE_URL || (host ? `${proto}://${host}` : undefined);
  return { ...METADATA, metadataBase: base ? new URL(base) : undefined };
}

// colour of the mobile browser bar / Android task switcher, same as the page background
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5f6f8" },
    { media: "(prefers-color-scheme: dark)", color: "#1d1f20" },
  ],
  // Android Chrome: the keyboard shrinks the layout (100dvh) instead of scrolling the page to the focused field
  interactiveWidget: "resizes-content",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: next-themes sets the theme class on <html> before React hydrates
    <html lang="ro" className={`${inter.variable} h-full antialiased`} suppressHydrationWarning>
      <body className="min-h-full flex flex-col">
        <ThemeProvider attribute="class" defaultTheme="system" disableTransitionOnChange enableSystem>
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
