import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "NaviGraph",
  description:
    "NaviGraph is a multi-tenant conversational BI platform that lets teams ask questions of their data in natural language and get trustworthy, explainable answers.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
