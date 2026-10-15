import { ROLES, ROLE_ORDER } from "../format";
import type { Player } from "../types";
import { Label, Panel } from "./ui";

export function Teams({ players, myTeam }: { players: Player[]; myTeam: number }) {
  const byRole = (a: Player, b: Player) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role);
  const allies = players.filter((p) => p.team === myTeam).sort(byRole);
  const enemies = players.filter((p) => p.team !== myTeam).sort(byRole);
  return (
    <Panel title="Teams" note="enemy items as last seen">
      <Team name="Your team" players={allies} color="border-ally" />
      <div className="mt-3.5">
        <Team name="Enemy team" players={enemies} color="border-enemy" />
      </div>
    </Panel>
  );
}

function Team({ name, players, color }: { name: string; players: Player[]; color: string }) {
  return (
    <div>
      <div className={`mb-1.5 border-l-3 pl-2 ${color}`}>
        <Label>{name}</Label>
      </div>
      {players.map((p) => (
        <PlayerRow key={`${p.team}-${p.champion}`} player={p} ring={color} />
      ))}
    </div>
  );
}

function PlayerRow({ player: p, ring }: { player: Player; ring: string }) {
  return (
    <div
      className={`grid grid-cols-[36px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-lg px-2 py-1.5 ${
        p.is_me ? "bg-gold/8 ring-1 ring-gold-lo" : ""
      }`}
    >
      <div className="relative">
        <img src={p.icon} alt="" className={`size-9 rounded-full border-2 ${ring}`} />
        <span className="absolute -right-1 -bottom-1 rounded-md border border-line-2 bg-base px-1 text-[10px] leading-3.5 font-bold">
          {p.level}
        </span>
      </div>
      <div className="min-w-0">
        <div className="truncate text-[13px] font-semibold">
          {p.champion}
          <small className="ml-1 font-medium text-faint">{ROLES[p.role]}</small>
        </div>
        <div className="mt-0.5 flex min-h-5 gap-0.75">
          {p.items.map((item) => (
            <img key={item.item_id} src={item.icon} title={item.name} alt={item.name} className="size-5 rounded border border-line-2" />
          ))}
          {p.trinket && (
            <img src={p.trinket.icon} title={p.trinket.name} alt={p.trinket.name} className="ml-1 size-5 rounded-full border border-line-2" />
          )}
        </div>
      </div>
      <div className="text-right text-[13px] font-semibold whitespace-nowrap">
        {p.kda.join(" / ")}
        <small className="block text-[11px] font-medium text-faint">{p.cs} cs</small>
      </div>
    </div>
  );
}
