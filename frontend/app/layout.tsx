import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "@/lib/query";

export const metadata: Metadata = {
  title: "CC Trader",
  description: "Chart-Champions-driven AI trading intelligence",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen">
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
