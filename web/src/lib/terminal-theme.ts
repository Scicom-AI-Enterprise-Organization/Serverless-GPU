// Terminal-block appearance preference. Stored client-side in localStorage —
// no backend needed; this is a personal vibe setting, not security-relevant.

export const TERMINAL_THEMES = [
  "default",
  "classic",
  "rainbow",
  "sparkle",
  "christmas",
] as const;
export type TerminalTheme = (typeof TERMINAL_THEMES)[number];

const STORAGE_KEY = "sgpu_terminal_theme";

// The free-tier default. Other themes sit behind a "burn $100 first" paywall
// (UI-only — no actual billing logic).
export const DEFAULT_TERMINAL_THEME: TerminalTheme = "rainbow";
export const PAYWALLED_THEMES: ReadonlySet<TerminalTheme> = new Set<TerminalTheme>([
  "default",
  "classic",
  "sparkle",
  "christmas",
]);

export function readTerminalTheme(): TerminalTheme {
  if (typeof window === "undefined") return DEFAULT_TERMINAL_THEME;
  const v = window.localStorage.getItem(STORAGE_KEY);
  return (TERMINAL_THEMES as readonly string[]).includes(v ?? "")
    ? (v as TerminalTheme)
    : DEFAULT_TERMINAL_THEME;
}

export function writeTerminalTheme(theme: TerminalTheme) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, theme);
  applyTerminalTheme(theme);
}

export function applyTerminalTheme(theme: TerminalTheme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "default") {
    root.removeAttribute("data-terminal-theme");
  } else {
    root.setAttribute("data-terminal-theme", theme);
  }
}
