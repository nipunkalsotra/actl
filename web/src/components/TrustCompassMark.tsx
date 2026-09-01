interface TrustCompassMarkProps {
  size?: number;
  className?: string;
}

/** Original mark: a blue circle, a white abstract "A" built from two
 * upward strokes, a faint shield silhouette as negative-space texture
 * behind it, and one small coral waypoint dot. Not a copy of any real
 * brand's logo. */
export function TrustCompassMark({ size = 36, className }: TrustCompassMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      className={className}
      role="img"
      aria-label="ACTL Trust Compass"
    >
      <circle cx="20" cy="20" r="19" fill="var(--color-ocean-600)" />
      <path
        d="M20 6 L30 15 V22 C30 29 25.5 33.5 20 35.5 C14.5 33.5 10 29 10 22 V15 Z"
        fill="#ffffff"
        fillOpacity="0.08"
      />
      <path
        d="M13 27 L20 12 L27 27"
        stroke="#ffffff"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <circle cx="27.5" cy="27.5" r="3" fill="var(--color-coral-500)" stroke="#ffffff" strokeWidth="1.4" />
    </svg>
  );
}
