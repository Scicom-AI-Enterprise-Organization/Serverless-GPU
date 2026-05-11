"use client";

import { Flame } from "lucide-react";
import { cn } from "@/lib/utils";

/** Animated flame icon — flickers + hue-shifts to signal "burning money".
 * Pure CSS via the `.burn-flame` keyframe in globals.css; honours
 * prefers-reduced-motion. Pass `size` in tailwind units (default `h-3 w-3`).
 */
export function BurnFlame({
  className,
  size = "h-3 w-3",
}: {
  className?: string;
  size?: string;
}) {
  return (
    <Flame
      className={cn("burn-flame shrink-0", size, className)}
      aria-hidden="true"
    />
  );
}
