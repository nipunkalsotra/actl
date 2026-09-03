import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useMediaQuery } from "../lib/useMediaQuery";
import { ThemeContext, type Theme } from "./themeContext";

const STORAGE_KEY = "actl-theme";

function readStoredTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

/** Explicit choice (persisted) always wins; otherwise the effective theme
 * tracks the system preference live via useMediaQuery. index.html's
 * pre-paint script applies the same resolution synchronously before first
 * paint -- this effect just keeps the DOM attribute in sync afterwards. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const systemPrefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  const [explicitTheme, setExplicitTheme] = useState<Theme | null>(() => readStoredTheme());
  const theme: Theme = explicitTheme ?? (systemPrefersDark ? "dark" : "light");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setExplicitTheme((current) => {
      const currentTheme = current ?? (systemPrefersDark ? "dark" : "light");
      const next: Theme = currentTheme === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Private-mode/unavailable storage -- theme still applies this
        // session via React state, it just won't persist across reload.
      }
      return next;
    });
  }, [systemPrefersDark]);

  return <ThemeContext.Provider value={{ theme, toggleTheme }}>{children}</ThemeContext.Provider>;
}
