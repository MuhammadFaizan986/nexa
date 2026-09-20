"use client";

/**
 * The NEXA mark.
 *
 * The idea behind the shape: an "N" drawn as a retrieval path. Two columns (the
 * question and the answer) joined by a diagonal, with three nodes on it — the
 * passages the answer travelled through. That's literally what the product
 * does, and it still reads as a letter at favicon size.
 *
 * It's inline SVG, so it inherits the theme colours and needs no image request.
 */

export function LogoMark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="nexa-mark" x1="6" y1="6" x2="42" y2="42" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--brand)" />
          <stop offset="1" stopColor="var(--accent)" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="44" height="44" rx="13" fill="url(#nexa-mark)" />
      {/* left column, diagonal, right column: the "N" */}
      <path
        d="M15 34V14M33 14v20M15 14l18 20"
        stroke="var(--brand-ink)"
        strokeWidth="3.4"
        strokeLinecap="round"
        opacity="0.95"
      />
      {/* the passages the answer passed through */}
      <circle cx="15" cy="14" r="3.6" fill="var(--brand-ink)" />
      <circle cx="24" cy="24" r="2.8" fill="var(--brand-ink)" opacity="0.75" />
      <circle cx="33" cy="34" r="3.6" fill="var(--brand-ink)" />
    </svg>
  );
}

export function Logo({
  size = 32,
  withWordmark = true,
  subtitle,
}: {
  size?: number;
  withWordmark?: boolean;
  subtitle?: string;
}) {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark size={size} />
      {withWordmark && (
        <span className="leading-tight">
          <span
            className="block font-semibold tracking-tight"
            style={{ fontSize: size * 0.56, letterSpacing: "-0.01em" }}
          >
            NEXA
          </span>
          {subtitle && <span className="block text-xs text-muted">{subtitle}</span>}
        </span>
      )}
    </span>
  );
}
