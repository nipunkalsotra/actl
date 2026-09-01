import { useEffect, useState } from "react";

export function useCountdown(expiresAtIso: string | null | undefined) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!expiresAtIso) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [expiresAtIso]);

  if (!expiresAtIso) return { secondsLeft: 0, isExpired: true, label: "--:--" };

  const secondsLeft = Math.max(0, Math.floor((new Date(expiresAtIso).getTime() - now) / 1000));
  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;
  const label = `${minutes}:${seconds.toString().padStart(2, "0")}`;

  return { secondsLeft, isExpired: secondsLeft <= 0, label };
}
