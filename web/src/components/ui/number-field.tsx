"use client";

import * as React from "react";
import { Input } from "@/components/ui/input";

type NumberFieldProps = Omit<
  React.ComponentProps<"input">,
  "type" | "value" | "onChange" | "min" | "max"
> & {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  allowDecimal?: boolean;
};

/**
 * Plain text input that only accepts numbers. Renders the destructive
 * border state via aria-invalid when the buffer isn't a valid number or
 * falls outside [min, max]. Never reverts the buffer — the user can erase
 * freely and the parent stays at the last valid value.
 */
export function NumberField({
  value,
  onChange,
  min,
  max,
  allowDecimal = false,
  ...rest
}: NumberFieldProps) {
  const [raw, setRaw] = React.useState<string>(() => String(value));

  const parsed = parse(raw, allowDecimal);
  const invalid =
    raw.length > 0 &&
    (parsed === null ||
      (min !== undefined && parsed < min) ||
      (max !== undefined && parsed > max));

  return (
    <Input
      {...rest}
      type="text"
      inputMode={allowDecimal ? "decimal" : "numeric"}
      autoComplete="off"
      value={raw}
      aria-invalid={invalid}
      onChange={(e) => {
        const v = e.target.value;
        setRaw(v);
        const p = parse(v, allowDecimal);
        if (
          p !== null &&
          (min === undefined || p >= min) &&
          (max === undefined || p <= max)
        ) {
          onChange(p);
        }
      }}
    />
  );
}

function parse(raw: string, allowDecimal: boolean): number | null {
  if (raw.trim() === "") return null;
  const re = allowDecimal ? /^-?\d*(\.\d*)?$/ : /^-?\d+$/;
  if (!re.test(raw)) return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  return n;
}
