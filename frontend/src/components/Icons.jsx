import React from "react";

const iconProps = (size = 18) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.85,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": "true",
});

export function SearchIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

export function SparkIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1" />
    </svg>
  );
}

export function PinIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <path d="M12 21s-6-5.3-6-10a6 6 0 1 1 12 0c0 4.7-6 10-6 10Z" />
      <circle cx="12" cy="11" r="2.2" />
    </svg>
  );
}

export function CubeIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z" />
      <path d="M12 12 4 7.5M12 12l8-4.5M12 12v9" />
    </svg>
  );
}

export function UsersIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

export function SlidersIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" />
    </svg>
  );
}

export function PlusIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function CloseIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

export function LockIcon({ size = 14 }) {
  return (
    <svg {...iconProps(size)}>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

export function GlobeIcon({ size = 14 }) {
  return (
    <svg {...iconProps(size)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a13 13 0 0 1 0 18M12 3a13 13 0 0 0 0 18" />
    </svg>
  );
}

export function ZoneIcon({ size = 16 }) {
  return (
    <svg {...iconProps(size)}>
      <rect x="3" y="3" width="18" height="18" rx="3" strokeDasharray="3 2" />
      <circle cx="12" cy="12" r="2.5" />
    </svg>
  );
}

export function GlassesIcon({ size = 18 }) {
  return (
    <svg {...iconProps(size)}>
      <circle cx="6.5" cy="13" r="3.5" />
      <circle cx="17.5" cy="13" r="3.5" />
      <path d="M10 13h4M3 13l2-6h2M21 13l-2-6h-2" />
    </svg>
  );
}

export function ArrowRightIcon({ size = 14 }) {
  return (
    <svg {...iconProps(size)}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export function StarIcon({ size = 14, filled = false }) {
  return (
    <svg {...iconProps(size)} fill={filled ? "currentColor" : "none"}>
      <polygon points="12 2 15.1 8.3 22 9.3 17 14.1 18.2 21 12 17.7 5.8 21 7 14.1 2 9.3 8.9 8.3 12 2" />
    </svg>
  );
}
