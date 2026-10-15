import { gold, percent, withArticle } from "../format";
import type { Recommendation } from "../types";
import { ItemIcon, Meter, Panel } from "./ui";

export function BuildNext({ recs, myGold, tier }: { recs: Recommendation[]; myGold: number; tier: string }) {
  const [top, ...others] = recs;
  return (
    <Panel title="Build next" note={`like ${withArticle(tier)} player would`}>
      {top ? (
        // A new key remounts the card when the top pick changes, which replays the flash animation.
        <TopPick key={top.item_id} rec={top} myGold={myGold} />
      ) : (
        <p className="text-faint">Nothing left to recommend.</p>
      )}
      <div className="mt-2.5 grid gap-2.5 sm:grid-cols-2">
        {others.map((rec, i) => (
          <OtherPick key={rec.item_id} rank={i + 2} rec={rec} myGold={myGold} />
        ))}
      </div>
    </Panel>
  );
}

function TopPick({ rec, myGold }: { rec: Recommendation; myGold: number }) {
  return (
    <div className="grid animate-flash grid-cols-[auto_1fr] items-center gap-4 rounded-xl border border-gold-lo bg-linear-to-br from-gold/10 to-transparent p-3.5">
      <ItemIcon item={rec} className="size-21 rounded-xl border-2 border-gold!" />
      <div>
        <div className="text-lg font-bold text-gold-hi">{rec.name}</div>
        <div className="mt-1.5 mb-2.5 flex flex-wrap gap-1.5">
          {rec.tags.map((tag) => (
            <span key={tag} className="rounded-full border border-line-2 px-2 text-[11px] text-muted">
              {tag}
            </span>
          ))}
        </div>
        <Meter value={rec.probability} />
        <p className="mt-1 text-xs text-muted">
          <b className="text-ink">{percent(rec.probability)}</b> likely next buy
        </p>
        <div className="mt-3">
          <Affordability rec={rec} myGold={myGold} />
        </div>
      </div>
    </div>
  );
}

function OtherPick({ rank, rec, myGold }: { rank: number; rec: Recommendation; myGold: number }) {
  return (
    <div className="grid grid-cols-[auto_1fr] items-center gap-2.5 rounded-xl border border-line bg-panel p-2.5">
      <ItemIcon item={rec} className="size-11 rounded-lg" />
      <div>
        <div className="mb-1.5 text-[13px] leading-tight font-semibold">
          <span className="mr-1 text-faint">#{rank}</span>
          {rec.name}
        </div>
        <Meter value={rec.probability} />
        <div className="mt-1 flex justify-between text-xs text-muted">
          <span>
            <b className="text-ink">{percent(rec.probability)}</b> likely
          </span>
          {rec.remaining <= myGold ? <span className="text-good">affordable</span> : <span>{gold(rec.remaining)}g</span>}
        </div>
      </div>
    </div>
  );
}

/** "Ready to buy", or how far my gold is from the item's remaining cost. */
function Affordability({ rec, myGold }: { rec: Recommendation; myGold: number }) {
  if (rec.remaining <= myGold) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-bold tracking-wider text-good uppercase">
        <span className="size-2 animate-pulse rounded-full bg-good shadow-[0_0_10px] shadow-good" />
        Ready to buy
      </span>
    );
  }
  const componentsHeld = rec.cost - rec.remaining;
  return (
    <>
      <Meter value={myGold / rec.remaining} bright />
      <div className="mt-1 flex justify-between text-xs text-muted">
        <span>
          <b className="text-ink">{gold(myGold)}</b> / {gold(rec.remaining)}g needed
        </span>
        <span>{componentsHeld > 0 ? `${gold(componentsHeld)}g in components` : `${gold(rec.cost)}g`}</span>
      </div>
    </>
  );
}
