import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Candidaturas | TrackFlow",
  description: "Talent Pipeline Tracker de TrackFlow",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="es"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <header className="app-header">
          <div className="header-content">
            <Link className="brand" href="/">TrackFlow<span>Talent Pipeline Tracker</span></Link>
            <nav aria-label="Navegación principal">
              <Link className="nav-link" href="/">Candidaturas</Link>
            </nav>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
