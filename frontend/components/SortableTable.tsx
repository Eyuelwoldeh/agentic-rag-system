"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useState } from "react";

export type Column<T> = {
  key: string;
  label: string;
  render: (row: T) => React.ReactNode;
  sort?: (row: T) => string | number;
  num?: boolean;
  className?: string;
};

/** Data table with 40px rows, three-state sortable headers (Carbon), and right-aligned numbers. */
export default function SortableTable<T>({ columns, rows, rowKey, empty, initialSort }: {
  columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string | number; empty?: React.ReactNode; initialSort?: { key: string; dir: 1 | -1 };
}) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(initialSort ?? null);
  const col = columns.find((c) => c.key === sort?.key);
  const sorted = col?.sort
    ? [...rows].sort((a, b) => {
        const x = col.sort!(a), y = col.sort!(b);
        return (x < y ? -1 : x > y ? 1 : 0) * sort!.dir;
      })
    : rows;
  if (!rows.length) return <>{empty}</>;
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th key={c.key} className={`${c.num ? "num" : ""} ${c.className ?? ""}`} aria-sort={active ? (sort!.dir === 1 ? "ascending" : "descending") : undefined}>
                  {c.sort ? (
                    <button className={`group inline-flex items-center gap-1 ${c.num ? "flex-row-reverse" : ""}`}
                      onClick={() => setSort(active ? (sort!.dir === 1 ? { key: c.key, dir: -1 } : null) : { key: c.key, dir: 1 })}>
                      {c.label}
                      {active ? (sort!.dir === 1 ? <ArrowUp className="h-3.5 w-3.5" /> : <ArrowDown className="h-3.5 w-3.5" />)
                        : <ArrowUpDown className="h-3.5 w-3.5 opacity-0 group-hover:opacity-60" />}
                    </button>
                  ) : c.label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={rowKey(r)}>
              {columns.map((c) => <td key={c.key} className={`${c.num ? "num" : ""} ${c.className ?? ""}`}>{c.render(r)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
