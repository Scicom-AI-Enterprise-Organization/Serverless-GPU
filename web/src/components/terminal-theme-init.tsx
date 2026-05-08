"use client";

import { useEffect } from "react";
import { applyTerminalTheme, readTerminalTheme, type TerminalTheme } from "@/lib/terminal-theme";

// Client-only effects for the terminal theme system:
//
//  1. Hydrate the saved theme onto <html> so CSS rules pick it up.
//  2. Per-block --mouse-x / --mouse-y vars for the cursor-follow shine.
//  3. (Sparkle theme) emit small star particles from the cursor that fall
//     and fade — runs page-wide as `position: fixed` children of <body>
//     so they survive scrolling and don't get clipped by terminal blocks.
//
// One mousemove listener handles all three; the particle emitter is
// throttled by both time and travel distance so fast motion doesn't flood
// the DOM.

// Tuned to feel like a clearly-visible trail without becoming a storm.
// Bumping these up makes the effect denser; bumping them down makes it
// sparser.
const SPARKLE_COLOURS = ["#ffffff", "#ffd1ff", "#b388ff"];
const SPARKLE_MIN_INTERVAL_MS = 45;
const SPARKLE_MIN_TRAVEL_PX = 10;

export function TerminalThemeInit() {
  useEffect(() => {
    const initial = readTerminalTheme();
    applyTerminalTheme(initial);
    syncSantas(initial);

    // When the theme changes (or new .terminal-block elements get
    // mounted by route changes), keep the Santa flyers in sync.
    const themeObserver = new MutationObserver(() => {
      const t = (document.documentElement.getAttribute(
        "data-terminal-theme",
      ) ?? "default") as TerminalTheme;
      syncSantas(t);
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-terminal-theme"],
    });
    const domObserver = new MutationObserver(() => {
      const t = (document.documentElement.getAttribute(
        "data-terminal-theme",
      ) ?? "default") as TerminalTheme;
      if (t === "christmas") syncSantas(t);
    });
    domObserver.observe(document.body, { childList: true, subtree: true });

    let lastSpawnAt = 0;
    let lastX = 0;
    let lastY = 0;

    function onMove(e: MouseEvent) {
      // 1. Cursor-follow shine vars on the hovered terminal block.
      const block = (e.target as HTMLElement | null)?.closest(
        ".terminal-block",
      ) as HTMLElement | null;
      if (block) {
        const rect = block.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * 100;
        const y = ((e.clientY - rect.top) / rect.height) * 100;
        block.style.setProperty("--mouse-x", `${x}%`);
        block.style.setProperty("--mouse-y", `${y}%`);
      }

      // 2. Sparkle particle emission, only when sparkle theme is active.
      const theme = document.documentElement.getAttribute("data-terminal-theme");
      if (theme !== "sparkle") return;

      const now = performance.now();
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      const travelled = Math.hypot(dx, dy);
      if (
        now - lastSpawnAt < SPARKLE_MIN_INTERVAL_MS ||
        travelled < SPARKLE_MIN_TRAVEL_PX
      ) {
        return;
      }
      lastSpawnAt = now;
      lastX = e.clientX;
      lastY = e.clientY;
      spawnSparkle(e.clientX, e.clientY);
    }

    function spawnSparkle(x: number, y: number) {
      const el = document.createElement("span");
      el.className = "sparkle-particle";
      el.textContent = "✦";
      // Randomise drift, size, colour so successive sparkles feel organic.
      const drift = (Math.random() - 0.5) * 22;
      const size = 9 + Math.random() * 6;
      const colour =
        SPARKLE_COLOURS[Math.floor(Math.random() * SPARKLE_COLOURS.length)];
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
      el.style.fontSize = `${size}px`;
      el.style.color = colour;
      el.style.setProperty("--drift", `${drift}px`);
      el.addEventListener("animationend", () => el.remove(), { once: true });
      document.body.appendChild(el);
    }

    document.addEventListener("mousemove", onMove);
    return () => {
      document.removeEventListener("mousemove", onMove);
      themeObserver.disconnect();
      domObserver.disconnect();
      syncSantas("default");
    };
  }, []);
  return null;
}

// Inject 🎅 inside every .terminal-block when the Christmas theme is active;
// remove them otherwise. Idempotent — if a flyer already exists in a block,
// we don't add a second one.
function syncSantas(theme: TerminalTheme) {
  const blocks = document.querySelectorAll<HTMLElement>(".terminal-block");
  blocks.forEach((block) => {
    const existing = block.querySelector<HTMLElement>(".santa-flier");
    if (theme === "christmas") {
      if (!existing) {
        const santa = document.createElement("span");
        santa.className = "santa-flier";
        santa.setAttribute("aria-hidden", "true");
        santa.textContent = "🎅🛷";
        block.appendChild(santa);
      }
    } else if (existing) {
      existing.remove();
    }
  });
}
