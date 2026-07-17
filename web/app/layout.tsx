import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Subgrade — cited road deterioration risk",
  description:
    "Cause-layer deterioration risk for local roads, every input cited to a federal source.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
