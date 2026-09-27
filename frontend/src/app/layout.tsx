import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { ThemeProvider } from "next-themes";
import "./globals.css";

const inter = Inter({ subsets: ["latin", "latin-ext", "cyrillic"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Spott",
  description: "Răspunsuri din documentele publice ale Primăriei, cu surse / Ответы по публичным документам Примэрии, с источниками",
  // iOS "Add to Home Screen": name under the icon; the icon itself is app/apple-icon.png
  appleWebApp: { title: "Spott", capable: true, statusBarStyle: "default" },
  // the link preview: the image is app/opengraph-image.jpg (1200×630); messengers need its absolute URL, hence
  // the site's address (unset: Next uses the host it is served from in dev, localhost otherwise)
  metadataBase: process.env.NEXT_PUBLIC_SITE_URL ? new URL(process.env.NEXT_PUBLIC_SITE_URL) : undefined,
  openGraph: {
    type: "website",
    siteName: "Spott",
    title: "Spott — a spotlight in the maze of bureaucracy",
    description: "Răspunsuri din documentele publice ale Primăriei Chișinău, cu surse / Ответы по публичным документам Примэрии, с источниками",
  },
  twitter: { card: "summary_large_image" },
};

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
