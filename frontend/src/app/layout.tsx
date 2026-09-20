import type { Metadata } from "next";
import { Inter } from "next/font/google";

import { AuthProvider } from "@/lib/auth";
import { ThemeProvider, themeScript } from "@/components/theme";
import { ToastProvider } from "@/components/ui";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: "NEXA — AI knowledge base",
  description:
    "Ask questions across thousands of documents and get accurate answers with citations, page by page.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applies the saved theme before the first paint: no white flash. */}
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className={`${inter.variable} min-h-screen font-sans antialiased`}>
        <ThemeProvider>
          <ToastProvider>
            <AuthProvider>{children}</AuthProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
