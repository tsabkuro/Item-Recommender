import { gold } from "../format";
import type { Game } from "../types";
import { Label, Panel } from "./ui";

/** What the enemy deals (physical vs magic) and how they defend, from the items seen on them. */
export function EnemyThreat({ enemy }: { enemy: Game["enemy"] }) {
  const physical = Math.round((100 * enemy.attack) / (enemy.attack + enemy.magic || 1));
  return (
    <Panel title="Enemy threat" note="from items seen">
      <div className="flex h-2.5 overflow-hidden rounded-full bg-line">
        <div className="bg-physical transition-[width] duration-500" style={{ width: `${physical}%` }} />
        <div className="flex-1 bg-magic" />
      </div>
      <div className="mt-1.5 flex justify-between text-xs">
        <span className="text-physical">Physical {physical}%</span>
        <span className="text-magic">Magic {100 - physical}%</span>
      </div>
      <div className="mt-3.5 grid grid-cols-2 gap-2.5">
        <Stat label="Armor" value={enemy.armor} />
        <Stat label="Magic resist" value={enemy.mr} />
      </div>
    </Panel>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-line bg-panel px-2.5 py-2">
      <Label>{label}</Label>
      <div className="text-2xl font-bold">{gold(value)}</div>
    </div>
  );
}
