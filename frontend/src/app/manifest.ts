import type { MetadataRoute } from "next";

// Android / "Add to home screen". Browser tab icons are app/favicon.ico, app/icon.png, app/apple-icon.png.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Spott",
    short_name: "Spott",
    start_url: "/",
    display: "standalone",
    background_color: "#f5f6f8",
    theme_color: "#f5f6f8",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
