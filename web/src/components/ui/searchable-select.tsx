"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { cn } from "@/lib/utils";

export type SearchableOption = {
  value: string;
  label: string;
  hint?: string;
  group?: string;
};

export function SearchableSelect({
  value,
  onChange,
  options,
  placeholder = "Select…",
  searchPlaceholder = "Search…",
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  options: SearchableOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const filtered = useMemo(() => {
    if (!query) return options;
    const q = query.toLowerCase();
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        o.value.toLowerCase().includes(q) ||
        (o.group ?? "").toLowerCase().includes(q),
    );
  }, [options, query]);

  useEffect(() => {
    setActiveIdx(0);
  }, [query, open]);

  const selected = options.find((o) => o.value === value);

  function commit(o: SearchableOption) {
    onChange(o.value);
    setOpen(false);
    setQuery("");
  }

  // Group rendering — preserve order of first appearance per group.
  const groups = useMemo(() => {
    const out: { name: string; items: SearchableOption[] }[] = [];
    const idx = new Map<string, number>();
    for (const o of filtered) {
      const g = o.group ?? "";
      if (!idx.has(g)) {
        idx.set(g, out.length);
        out.push({ name: g, items: [] });
      }
      out[idx.get(g)!].items.push(o);
    }
    return out;
  }, [filtered]);

  // Flat order for keyboard nav, mirrors what the user sees.
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none ring-offset-background hover:bg-accent/30 focus-visible:ring-2 focus-visible:ring-ring/30"
      >
        <span className={cn("truncate", !selected && "text-muted-foreground")}>
          {selected?.label ?? placeholder}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-md">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <Search className="h-3.5 w-3.5 shrink-0 opacity-50" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActiveIdx((i) => Math.min(i + 1, flat.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActiveIdx((i) => Math.max(i - 1, 0));
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  if (flat[activeIdx]) commit(flat[activeIdx]);
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  setOpen(false);
                  setQuery("");
                }
              }}
              placeholder={searchPlaceholder}
              className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <div ref={listRef} className="max-h-72 overflow-y-auto p-1">
            {flat.length === 0 && (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                No matches
              </div>
            )}
            {groups.map((g) => (
              <div key={g.name || "_"}>
                {g.name && (
                  <div className="px-2 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    {g.name}
                  </div>
                )}
                {g.items.map((o) => {
                  const flatIndex = flat.indexOf(o);
                  const isActive = flatIndex === activeIdx;
                  const isSelected = o.value === value;
                  return (
                    <button
                      key={o.value}
                      type="button"
                      onMouseEnter={() => setActiveIdx(flatIndex)}
                      onClick={() => commit(o)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-sm px-2 py-2 text-left text-sm",
                        isActive && "bg-accent",
                        isSelected && "font-medium",
                      )}
                    >
                      <span className="flex flex-col">
                        <span className="truncate">{o.label}</span>
                        {o.hint && (
                          <span className="truncate text-xs text-muted-foreground">
                            {o.hint}
                          </span>
                        )}
                      </span>
                      {isSelected && <Check className="h-3.5 w-3.5 shrink-0" />}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
