import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { MeProvider } from "@/lib/me";
import Nav from "./nav";
import "./globals.css";

const sans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = { title: "Agentic RAG", description: "Grounded answers over your documents, data and the web" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${mono.variable} antialiased`}>
        <MeProvider>
          <Nav />
          {children}
        </MeProvider>
      </body>
    </html>
  );
}
