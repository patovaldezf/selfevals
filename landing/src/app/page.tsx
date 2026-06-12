import Nav from "@/components/Nav";
import Hero from "@/components/Hero";
import TrustStrip from "@/components/TrustStrip";
import Features from "@/components/Features";
import HowItWorks from "@/components/HowItWorks";
import CaseStudy from "@/components/CaseStudy";
import Compare from "@/components/Compare";
import Proof from "@/components/Proof";
import FinalCTA from "@/components/FinalCTA";
import Footer from "@/components/Footer";

export default function Home() {
  return (
    <div className="grain relative min-h-screen">
      <Nav />
      <main>
        <Hero />
        <TrustStrip />
        <Features />
        <HowItWorks />
        <CaseStudy />
        <Compare />
        <Proof />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  );
}
