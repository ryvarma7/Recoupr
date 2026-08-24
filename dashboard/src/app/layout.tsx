import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Recoupr — revenue recovery console",
  description:
    "AI agent that watches a Razorpay payment stack for revenue-at-risk events, diagnoses root causes, and executes one bounded recovery action per case — through a deterministic guardrail gate.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Runtime font links: build never depends on network font fetches. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
