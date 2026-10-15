// Small building blocks shared by the panels.
import type { ReactNode } from "react";

export function Panel({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-linear-to-b from-panel-2 to-panel p-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <Label>{title}</Label>
        {note && <span className="text-xs text-faint">{note}</span>}
      </div>
      {children}
    </section>
  );
}

export function Label({ children }: { children: ReactNode }) {
  return <h2 className="text-[11px] font-semibold tracking-widest text-muted uppercase">{children}</h2>;
}

/** A thin progress bar; `value` is 0..1. */
export function Meter({ value, bright = false }: { value: number; bright?: boolean }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-line">
      <div
        className={`h-full rounded-full bg-linear-to-r transition-[width] duration-500 ${bright ? "from-gold to-gold-hi" : "from-gold-lo to-gold"}`}
        style={{ width: `${Math.min(100, value * 100)}%` }}
      />
    </div>
  );
}

export function ItemIcon({ item, className = "" }: { item: { icon: string; name: string }; className?: string }) {
  return <img src={item.icon} alt={item.name} title={item.name} className={`border border-line-2 bg-base ${className}`} />;
}
