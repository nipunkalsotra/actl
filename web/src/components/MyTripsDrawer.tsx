import { motion } from "framer-motion";
import { Briefcase, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { formatMinor } from "../lib/money";
import { useJourney } from "../state/journeyContext";
import { Overlay } from "./Overlay";

interface MyTripsDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function MyTripsDrawer({ open, onClose }: MyTripsDrawerProps) {
  const { trips } = useJourney();
  const navigate = useNavigate();

  return (
    <Overlay open={open} onClose={onClose}>
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="My trips"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 320, damping: 32 }}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col overflow-hidden bg-card shadow-float sm:rounded-l-3xl"
      >
        <div className="flex items-center justify-between border-b border-sky-100 px-5 py-4">
          <h2 className="flex items-center gap-2 text-base font-semibold text-navy-900">
            <Briefcase size={18} /> My trips
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close my trips"
            className="flex h-8 w-8 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {trips.length === 0 ? (
            <p className="text-sm text-navy-500">
              No trips yet in this browser session. Book a stay and it'll show up here.
            </p>
          ) : (
            <ul className="space-y-3">
              {trips.map((trip) => (
                <li key={trip.orderId} className="rounded-2xl border border-sky-100 p-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="text-sm font-semibold text-navy-900">{trip.hotelName}</p>
                      <p className="text-xs text-navy-500">{new Date(trip.createdAt).toLocaleString()}</p>
                    </div>
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        trip.status === "CAPTURED"
                          ? "bg-emerald-100 text-emerald-600"
                          : "bg-coral-100 text-coral-600"
                      }`}
                    >
                      {trip.status}
                    </span>
                  </div>
                  <p className="mt-2 text-sm font-medium text-navy-900">{formatMinor(trip.totalMinor)}</p>
                  <button
                    type="button"
                    onClick={() => navigate(`/merchant?order_id=${trip.orderId}&panel=proof`)}
                    className="mt-3 rounded-full border border-sky-100 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-50"
                  >
                    View proof
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </motion.div>
    </Overlay>
  );
}
