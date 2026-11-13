import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { UIProvider } from "@/components/ui";
import { MeProvider } from "@/lib/me";
import Shell from "./nav";
import "./globals.css";

const sans = IBM_Plex_Sans({ variable: "--font-plex-sans", subsets: ["latin"], weight: ["400", "500", "600"] });
const mono = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400", "500"] });

export const metadata: Metadata = { title: "Agentic RAG", description: "Company knowledge and analytics assistant" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${mono.variable} antialiased`}>
        <UIProvider>
          <MeProvider>
            <Shell>{children}</Shell>
          </MeProvider>
        </UIProvider>
      </body>
    </html>
  );
}
