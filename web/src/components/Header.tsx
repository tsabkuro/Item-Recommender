import { ROLES, gold, time } from "../format";
import type { Game } from "../types";
import { Label } from "./ui";

export function Header({ game, champion }: { game: Game; champion: string }) {
  return (
    <header className="mb-5 flex flex-wrap items-center gap-4 border-b border-line pb-5">
      <div className="relative">
        <img src={game.champion_icon} alt="" className="size-17 rounded-full border-2 border-gold" />
        <span className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 rounded-full border border-gold bg-base px-1.5 text-xs font-bold text-gold-hi">
          {game.level}
        </span>
      </div>
      <div>
        <h1 className="font-display text-2xl text-gold-hi">{champion}</h1>
        <p className="mt-1 text-muted">
          {ROLES[game.role] ?? "No role"} · <b className="text-ink">{game.kda.join(" / ")}</b> · {game.cs} CS
        </p>
      </div>
      <div className="ml-auto flex gap-7 text-right">
        <div>
          <Label>Game time</Label>
          <div className="text-2xl font-bold">{time(game.game_time)}</div>
        </div>
        <div>
          <Label>Gold</Label>
          <div className="flex items-center gap-1.5 text-2xl font-bold text-gold">
            <span className="size-4 rounded-full bg-radial-[at_35%_35%] from-[#f6dd9a] via-[#b88a33] to-[#6e4f19]" />
            {gold(game.gold)}
          </div>
        </div>
      </div>
    </header>
  );
}
