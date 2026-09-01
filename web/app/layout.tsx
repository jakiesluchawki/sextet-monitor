import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mieszko Monitor",
  description: "Prywatny monitor zdarzeń, ostrzeżeń i źródeł. Dane przed interpretacją.",
  robots: {index:false, follow:false},
};
export const viewport: Viewport = {width:"device-width", initialScale:1, colorScheme:"dark", themeColor:"#171b1a"};
export default function RootLayout({children}: Readonly<{children:React.ReactNode}>) {
  return <html lang="pl"><body>{children}</body></html>;
}
