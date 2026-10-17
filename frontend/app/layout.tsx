import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ARC — Public evidence, in context",
  description: "A transparent workspace for public professional research.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
