import type { Player } from "../types";
import { ItemIcon, Panel } from "./ui";

const ITEM_SLOTS = 6;

/** Six item slots plus the round trinket slot, like the in-game inventory. */
export function Inventory({ me }: { me: Player }) {
  const empty = Math.max(0, ITEM_SLOTS - me.items.length);
  return (
    <Panel title="Your items" note={`${me.items.length} item${me.items.length === 1 ? "" : "s"}`}>
      <div className="flex flex-wrap items-center gap-2">
        {me.items.slice(0, ITEM_SLOTS).map((item) => (
          <ItemIcon key={item.item_id} item={item} className="size-11 rounded-lg" />
        ))}
        {Array.from({ length: empty }, (_, i) => (
          <div key={i} className="size-11 rounded-lg border border-line-2 bg-base" />
        ))}
        <div className="ml-2.5">
          {me.trinket ? (
            <ItemIcon item={me.trinket} className="size-11 rounded-full" />
          ) : (
            <div className="size-11 rounded-full border border-line-2 bg-base" />
          )}
        </div>
      </div>
    </Panel>
  );
}
