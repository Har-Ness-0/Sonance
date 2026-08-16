import type { Metadata } from "next";
import { Inter, Geist_Mono } from "next/font/google";
import "./globals.css"
import { Toaster } from "@/components/ui/sonner";
import { ClerkProvider } from "@clerk/nextjs";

import { TRPCReactProvider } from "@/trpc/client";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: { 
    default: "Sonance",
    template: "%s | Sonance"

      
  },
  description: "AI-powered text to speech and voice cloning platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <ClerkProvider>
      <TRPCReactProvider>
      <html lang="en">
        <body
          className={`${inter.className} ${geistMono.variable} antialiased`}
        >
          {children}
          <Toaster />
        </body>
      </html>
       </TRPCReactProvider>
    </ClerkProvider>
  );
}
