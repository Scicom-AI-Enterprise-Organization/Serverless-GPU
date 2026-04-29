// Deterministic per-name avatar styling. Same name always picks the same
// color so the UI stays stable across reloads, but different endpoints get
// visually different badges.

const PALETTE: Array<{ bg: string; text: string }> = [
  { bg: "bg-violet-500/15",   text: "text-violet-400" },
  { bg: "bg-sky-500/15",      text: "text-sky-400" },
  { bg: "bg-emerald-500/15",  text: "text-emerald-400" },
  { bg: "bg-amber-500/15",    text: "text-amber-400" },
  { bg: "bg-rose-500/15",     text: "text-rose-400" },
  { bg: "bg-fuchsia-500/15",  text: "text-fuchsia-400" },
  { bg: "bg-teal-500/15",     text: "text-teal-400" },
  { bg: "bg-orange-500/15",   text: "text-orange-400" },
  { bg: "bg-indigo-500/15",   text: "text-indigo-400" },
  { bg: "bg-cyan-500/15",     text: "text-cyan-400" },
  { bg: "bg-lime-500/15",     text: "text-lime-400" },
  { bg: "bg-pink-500/15",     text: "text-pink-400" },
];

// djb2-style hash, kept tiny — we only need a small integer for indexing.
function hash(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = (h * 33) ^ s.charCodeAt(i);
  return h >>> 0; // force unsigned
}

export function avatarFor(name: string): {
  letter: string;
  bg: string;
  text: string;
} {
  const cleaned = (name || "?").trim();
  const letter = (cleaned[0] || "?").toUpperCase();
  const palette = PALETTE[hash(cleaned) % PALETTE.length];
  return { letter, bg: palette.bg, text: palette.text };
}
