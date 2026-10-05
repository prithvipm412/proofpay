import { Inter } from "next/font/google";
import { Footer } from "~~/components/Footer";
import { Header } from "~~/components/Header";
import { Providers } from "~~/components/Providers";
import { WithdrawBanner } from "~~/components/WithdrawBanner";
import "~~/styles/globals.css";
import { getMetadata } from "~~/utils/scaffold-eth/getMetadata";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata = getMetadata({
  title: "ProofPay",
  description: "Escrow for real-world tasks on Monad, with automated visual checks and duplicate-evidence detection.",
});

const RootLayout = ({ children }: { children: React.ReactNode }) => {
  return (
    <html lang="en" className={inter.variable}>
      <body className="font-sans">
        <Providers>
          <div className="flex min-h-screen flex-col">
            <Header />
            <WithdrawBanner />
            <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 py-6 sm:py-10">{children}</main>
            <Footer />
          </div>
        </Providers>
      </body>
    </html>
  );
};

export default RootLayout;
