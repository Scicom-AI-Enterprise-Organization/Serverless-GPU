"use client";

import { useEffect, useState } from "react";

export function LandingDots({ ids }: { ids: string[] }) {
  const [active, setActive] = useState<string>(ids[0] ?? "");

  useEffect(() => {
    const root = document.getElementById("landing-scroll");
    if (!root) return;
    const observed: HTMLElement[] = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => !!el);

    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible) setActive(visible.target.id);
      },
      { root, threshold: [0.55] },
    );
    observed.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [ids]);

  const scrollTo = (id: string) => {
    const root = document.getElementById("landing-scroll");
    const target = document.getElementById(id);
    if (!root || !target) return;
    root.scrollTo({ top: target.offsetTop, behavior: "smooth" });
  };

  return (
    <ul className="fixed right-6 top-1/2 z-30 hidden -translate-y-1/2 flex-col gap-3 sm:flex">
      {ids.map((id) => {
        const isActive = id === active;
        return (
          <li key={id}>
            <button
              type="button"
              onClick={() => scrollTo(id)}
              aria-label={`Scroll to ${id}`}
              className={`block h-2.5 w-2.5 rounded-full border transition-all ${
                isActive
                  ? "scale-125 border-foreground bg-foreground"
                  : "border-foreground/30 bg-foreground/10 hover:border-foreground/70"
              }`}
            />
          </li>
        );
      })}
    </ul>
  );
}
