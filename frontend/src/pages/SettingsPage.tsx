import { useEffect } from "react";
import { useLocation } from "react-router";

import { AboutCard } from "../components/settings/AboutCard";
import { DoctorPanel } from "../components/settings/DoctorPanel";
import { PasswordCard } from "../components/settings/PasswordCard";
import { PathsCard } from "../components/settings/PathsCard";
import { RenderCard } from "../components/settings/RenderCard";
import { VoiceCard } from "../components/settings/VoiceCard";

/** `/settings`: folders, render defaults, your account, the health checks and About. */
export function SettingsPage() {
  const location = useLocation();

  // /settings#doctor (from the health pill) scrolls to that panel once it is on screen.
  useEffect(() => {
    if (!location.hash) return;
    const id = location.hash.slice(1);
    const timer = window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
    return () => window.clearTimeout(timer);
  }, [location.hash]);

  return (
    <div className="flex flex-col gap-6">
      <PathsCard />
      <RenderCard />
      <VoiceCard />
      <PasswordCard />
      <DoctorPanel />
      <AboutCard />
    </div>
  );
}
