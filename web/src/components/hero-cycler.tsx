"use client";

import { useState } from "react";
import { Typewriter } from "@/components/typewriter";

type Activity = { word: string; p1: string; p2: string };

const ACTIVITIES: Activity[] = [
  {
    word: "bouldering",
    p1: "Your GPUs nap until a request rolls in. The API wakes up, lifts the load, then goes back to sleep. No idle pods. No cold-start gymnastics. No bill for nothing.",
    p2: "Go climb something. Make coffee. Touch grass. The endpoint will spin up the second a customer hits it, and shut itself down before you finish your set.",
  },
  {
    word: "gokarting",
    p1: "Your GPUs sit on the grid until the green light. The API revs up the second a request comes in, then pulls into the pit. No idle pods. No parade laps. No bill for warming the tires.",
    p2: "Go nail a corner. Burn rubber. Sip a cold one. The endpoint will fire the moment a customer pulls the trigger, and shut itself down before the cooldown lap.",
  },
  {
    word: "running",
    p1: "Your GPUs sit at the start line until somebody fires the gun. The API kicks in the second a request rolls in, then jogs back to the gym. No idle pods. No warm-ups. No bill for stretching.",
    p2: "Go run laps. Hit splits. Earn segments. The endpoint clocks in the moment a customer hits it, then clocks out before you finish your cooldown jog.",
  },
  {
    word: "swiping right",
    p1: "Your GPUs ghost everyone until somebody slides into your endpoint's DMs. The API matches up the second a request rolls in, then unmatches. No idle pods. No third-date awkwardness. No bill for vibes.",
    p2: "Go on a date. Get rejected. Make small talk. The endpoint will spin up the moment a customer actually likes it, and ghost itself when nobody's interested.",
  },
  {
    word: "flying drones",
    p1: "Your GPUs sit on the launchpad until a request takes off. The API gets airborne the second a customer hits it, then auto-RTH's. No idle pods. No hovering. No bill while you're grounded.",
    p2: "Go fly FPV. Buzz the trees. Land in a tree. The endpoint will arm itself the moment a customer pulls the trigger, and disarm before your battery hits 20%.",
  },
];

const LONGEST = ACTIVITIES.reduce(
  (a, b) => (a.length >= b.word.length ? a : b.word),
  ACTIVITIES[0].word,
);

export function HeroCycler() {
  const [i, setI] = useState(0);
  const cur = ACTIVITIES[i];

  return (
    <>
      <h1 className="mt-6 text-balance bg-gradient-to-b from-foreground to-foreground/60 bg-clip-text text-4xl font-semibold tracking-tight text-transparent sm:text-6xl">
        Run inference while you&apos;re{" "}
        <Typewriter
          word={cur.word}
          widthBasis={LONGEST}
          onCycleComplete={() => setI((i + 1) % ACTIVITIES.length)}
          className="bg-gradient-to-r from-violet-500 to-fuchsia-500 bg-clip-text text-transparent"
        />
        .
      </h1>

      {/* Paragraphs fade + slide in on each activity change. aria-live so a SR
          announces the new copy when it swaps. */}
      <div aria-live="polite" aria-atomic="true">
        <p
          key={`p1-${i}`}
          className="mt-6 animate-in fade-in-0 slide-in-from-bottom-2 text-balance text-lg text-muted-foreground duration-700"
        >
          {cur.p1}
        </p>
        <p
          key={`p2-${i}`}
          className="mt-3 animate-in fade-in-0 slide-in-from-bottom-2 text-balance text-sm text-muted-foreground/80 duration-700 delay-150 fill-mode-both"
          style={{ animationDelay: "150ms", animationFillMode: "both" }}
        >
          {cur.p2}
        </p>
      </div>
    </>
  );
}
