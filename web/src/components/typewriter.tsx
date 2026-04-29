"use client";

import { useEffect, useMemo, useState } from "react";

const TYPE_MS = 80;
const DELETE_MS = 35;
const HOLD_MS = 3500; // how long the finished word is held on screen

type Phase = "type" | "hold" | "delete" | "done";

export function Typewriter({
  word,
  widthBasis,
  onCycleComplete,
  className = "",
}: {
  word: string;
  widthBasis?: string;
  onCycleComplete?: () => void;
  className?: string;
}) {
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>("type");

  // Reset on word change.
  useEffect(() => {
    setText("");
    setPhase("type");
  }, [word]);

  useEffect(() => {
    if (phase === "type") {
      if (text.length < word.length) {
        const t = setTimeout(() => setText(word.slice(0, text.length + 1)), TYPE_MS);
        return () => clearTimeout(t);
      }
      setPhase("hold");
      return;
    }
    if (phase === "hold") {
      const t = setTimeout(() => setPhase("delete"), HOLD_MS);
      return () => clearTimeout(t);
    }
    if (phase === "delete") {
      if (text.length > 0) {
        const t = setTimeout(() => setText(text.slice(0, -1)), DELETE_MS);
        return () => clearTimeout(t);
      }
      setPhase("done");
      onCycleComplete?.();
    }
  }, [text, phase, word, onCycleComplete]);

  return (
    <span
      className={`relative inline-grid align-baseline ${className}`}
      aria-label={word}
    >
      <span className="invisible col-start-1 row-start-1 whitespace-nowrap" aria-hidden>
        {widthBasis ?? word}
      </span>
      <span
        className="col-start-1 row-start-1 whitespace-nowrap text-left"
        aria-hidden
      >
        {text}
        <span className="ml-0.5 inline-block w-[2px] animate-pulse bg-current align-text-bottom">
          &nbsp;
        </span>
      </span>
    </span>
  );
}

/** Self-cycling typewriter for places that don't need to sync with anything else.
 *  Owns its own index and rotates through the given list in order. */
export function TypewriterCycle({
  words,
  className = "",
}: {
  words: string[];
  className?: string;
}) {
  const [i, setI] = useState(0);
  const longest = useMemo(
    () => words.reduce((a, b) => (a.length >= b.length ? a : b), ""),
    [words],
  );
  return (
    <Typewriter
      word={words[i]}
      widthBasis={longest}
      onCycleComplete={() => setI((i + 1) % words.length)}
      className={className}
    />
  );
}
