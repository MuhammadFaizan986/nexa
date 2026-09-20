"use client";

/**
 * What the assistant cost per day.
 *
 * Design decisions worth knowing:
 * - One measure over time, one series, so: bars, one hue, no legend (the title
 *   names the series). Never two y-axes.
 * - The mark colour is a token validated against both chart surfaces
 *   (lightness band, chroma, contrast) rather than picked by eye.
 * - Only the largest bar is labelled; a number on every bar is noise.
 * - Hovering any bar shows the exact figures, and the same data is available as
 *   a table below, so nothing depends on reading a colour.
 */

import { useState } from "react";

import type { UsageDay } from "@/lib/types";

const money = (value: number) => `$${value.toFixed(value < 1 ? 4 : 2)}`;
const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

export function UsageChart({ days }: { days: UsageDay[] }) {
  const [hovered, setHovered] = useState<UsageDay | null>(null);
  if (days.length === 0) {
    return <p className="py-8 text-center text-sm text-muted">No activity yet.</p>;
  }

  const peak = Math.max(...days.map((day) => day.cost_usd), 0.000001);
  const peakDate = days.find((day) => day.cost_usd === peak)?.date;

  return (
    <div>
      <div className="relative flex h-44 items-end gap-[2px]" role="img"
           aria-label={`Daily cost for the last ${days.length} days`}>
        {days.map((day) => {
          const height = Math.max((day.cost_usd / peak) * 100, day.cost_usd > 0 ? 3 : 1);
          return (
            <div
              key={day.date}
              className="group relative flex min-w-[6px] flex-1 flex-col justify-end"
              onMouseEnter={() => setHovered(day)}
              onMouseLeave={() => setHovered(null)}
            >
              {day.date === peakDate && (
                <span className="mb-1 text-center text-[10px] text-muted">
                  {money(day.cost_usd)}
                </span>
              )}
              <div
                className="w-full rounded-t bg-chart transition group-hover:opacity-80"
                style={{ height: `${height}%` }}
              />
            </div>
          );
        })}
        {hovered && (
          <div className="pointer-events-none absolute right-0 top-0 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-sm">
            <p className="font-medium">{shortDate(hovered.date)}</p>
            <p className="text-muted">
              {hovered.questions} question{hovered.questions === 1 ? "" : "s"} ·{" "}
              {hovered.tokens.toLocaleString()} tokens
            </p>
            <p className="text-muted">Total {money(hovered.cost_usd)}</p>
            {Object.entries(hovered.by_type).map(([type, cost]) => (
              <p key={type} className="text-muted">
                {type}: {money(cost)}
              </p>
            ))}
          </div>
        )}
      </div>

      <div className="mt-1 flex justify-between border-t border-line pt-1 text-[10px] text-muted">
        <span>{shortDate(days[0].date)}</span>
        <span>{shortDate(days[days.length - 1].date)}</span>
      </div>

      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-muted">Show the numbers</summary>
        <table className="mt-2 w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="pb-1 font-medium">Day</th>
              <th className="pb-1 font-medium">Questions</th>
              <th className="pb-1 font-medium">Tokens</th>
              <th className="pb-1 font-medium">Cost</th>
            </tr>
          </thead>
          <tbody>
            {[...days].reverse().map((day) => (
              <tr key={day.date} className="border-t border-line">
                <td className="py-1">{shortDate(day.date)}</td>
                <td className="py-1">{day.questions}</td>
                <td className="py-1">{day.tokens.toLocaleString()}</td>
                <td className="py-1">{money(day.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="text-xs uppercase tracking-wide text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}
