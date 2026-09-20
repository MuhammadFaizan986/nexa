"use client";

/**
 * Light / dark / system, remembered between visits.
 *
 * `data-theme` on <html> drives everything (see globals.css). "System" removes
 * the attribute so the OS setting wins. The small script in layout.tsx applies
 * the saved choice *before* the first paint, so nobody sees a white flash on a
 * dark screen.
 */

import { Monitor, Moon, Sun } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";
const STORAGE_KEY = "nexa.theme";

const ThemeContext = createContext<{
  theme: Theme;
  setTheme: (theme: Theme) => void;
} | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("system");

  useEffect(() => {
    setThemeState((localStorage.getItem(STORAGE_KEY) as Theme) ?? "system");
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    localStorage.setItem(STORAGE_KEY, next);
    const root = document.documentElement;
    if (next === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", next);
  }, []);

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside <ThemeProvider>");
  return context;
}

/** Runs before the first paint: no flash of the wrong theme. */
export const themeScript = `
(function () {
  try {
    var saved = localStorage.getItem("${STORAGE_KEY}");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {}
})();
`;

const OPTIONS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
];

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, setTheme } = useTheme();
  return (
    <div
      className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-surface p-0.5"
      role="group"
      aria-label="Colour theme"
    >
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          onClick={() => setTheme(value)}
          aria-pressed={theme === value}
          title={label}
          className={`rounded-md p-1.5 transition ${
            theme === value
              ? "bg-brand-soft text-brand"
              : "text-muted hover:bg-canvas hover:text-ink"
          }`}
        >
          <Icon size={compact ? 14 : 16} strokeWidth={2} />
          <span className="sr-only">{label}</span>
        </button>
      ))}
    </div>
  );
}
