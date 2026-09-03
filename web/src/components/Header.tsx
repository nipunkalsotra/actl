import { Briefcase, CircleHelp, LayoutDashboard, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useJourney } from "../state/journeyContext";
import { ProfileMenu } from "./ProfileMenu";
import { ThemeToggle } from "./ThemeToggle";
import { TrustCompassMark } from "./TrustCompassMark";

interface HeaderProps {
  onOpenTrips: () => void;
  onOpenHelp: () => void;
}

export function Header({ onOpenTrips, onOpenHelp }: HeaderProps) {
  const { mandate, setMandate, activeOrder, resetForNewBrowse } = useJourney();
  const [destination, setDestination] = useState("Goa");

  const handleLogoClick = () => {
    const hasActiveFlow = mandate !== null || activeOrder !== null;
    if (hasActiveFlow) {
      const confirmed = window.confirm(
        "You have a booking in progress. Return to browsing and discard this draft?",
      );
      if (!confirmed) return;
    }
    setMandate(null);
    resetForNewBrowse();
  };

  return (
    <header className="sticky top-0 z-30 border-b border-sky-100 bg-card">
      <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:px-6">
        <button
          type="button"
          onClick={handleLogoClick}
          className="flex shrink-0 items-center gap-2 rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ocean-500"
        >
          <TrustCompassMark size={32} />
          <span className="text-lg font-semibold tracking-tight text-navy-900">ACTL.</span>
        </button>

        <label className="sr-only" htmlFor="destination-select">
          Destination
        </label>
        <select
          id="destination-select"
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          className="rounded-full border border-sky-100 bg-sky-50 px-4 py-2 text-sm font-medium text-navy-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500"
        >
          <option value="Goa">Goa</option>
          <option value="Kerala" disabled>
            Kerala (coming soon)
          </option>
          <option value="Manali" disabled>
            Manali (coming soon)
          </option>
        </select>

        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          <button
            type="button"
            onClick={onOpenTrips}
            className="hidden items-center gap-1.5 rounded-full px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 sm:flex"
          >
            <Briefcase size={16} />
            My trips
          </button>
          <button
            type="button"
            onClick={onOpenHelp}
            className="hidden items-center gap-1.5 rounded-full px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 sm:flex"
          >
            <CircleHelp size={16} />
            Help
          </button>
          <span className="hidden items-center gap-1.5 rounded-full bg-emerald-100 px-3 py-1.5 text-sm font-medium text-emerald-600 md:flex">
            <ShieldCheck size={16} />
            Budget protected
          </span>
          <Link
            to="/merchant"
            className="hidden items-center gap-1.5 rounded-full border border-sky-100 px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 sm:flex"
          >
            <LayoutDashboard size={16} />
            Merchant view
          </Link>
          <ThemeToggle />
          <ProfileMenu onOpenTrips={onOpenTrips} onOpenHelp={onOpenHelp} />
        </div>
      </div>
    </header>
  );
}
