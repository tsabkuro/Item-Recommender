import { BuildNext } from "./components/BuildNext";
import { EnemyThreat } from "./components/EnemyThreat";
import { Header } from "./components/Header";
import { Inventory } from "./components/Inventory";
import { ReplayBar } from "./components/ReplayBar";
import { Teams } from "./components/Teams";
import { TierPicker } from "./components/TierPicker";
import { WinChart } from "./components/WinChart";
import type { AppState, Game } from "./types";
import { isCatchingUp, useAppState } from "./useAppState";

export default function App() {
  const { state, offline, refresh } = useAppState();

  return (
    <main className="mx-auto max-w-6xl px-4 pt-5 pb-8">
      {state?.replay && <ReplayBar replay={state.replay} speeds={state.replay_speeds} onChange={refresh} />}

      {offline || !state ? (
        <Waiting message={offline ? "Recommender not running" : "Connecting..."} error={offline} />
      ) : state.status === "ok" ? (
        <div className={`transition-opacity duration-200 ${isCatchingUp(state) ? "opacity-50" : ""}`}>
          <Dashboard game={state} history={state.win_history} />
        </div>
      ) : (
        <Waiting message={state.message} error={state.status === "error"} />
      )}

      {state && <Footer state={state} onTierChange={refresh} />}
    </main>
  );
}

function Dashboard({ game, history }: { game: Game; history: [number, number][] }) {
  const me = game.players.find((p) => p.is_me)!;
  return (
    <>
      <Header game={game} champion={me.champion} />
      {game.patch_warning && (
        <div className="mb-3.5 rounded-xl border border-gold-lo bg-gold/8 px-3 py-2 text-[13px] text-gold-hi">
          Heads up: {game.patch_warning}
        </div>
      )}
      <div className="grid items-start gap-3.5 lg:grid-cols-[1.25fr_1fr]">
        <div className="space-y-3.5">
          <BuildNext recs={game.recommendations} myGold={game.gold} tier={game.tier} />
          <Inventory me={me} />
          <WinChart
            probability={game.win_probability} history={history}
            gameTime={game.game_time} itemValueDiff={game.team_item_value_diff}
          />
        </div>
        <div className="space-y-3.5">
          <Teams players={game.players} myTeam={game.my_team} />
          <EnemyThreat enemy={game.enemy} />
        </div>
      </div>
    </>
  );
}

function Waiting({ message, error }: { message: string; error: boolean }) {
  return (
    <div className="grid min-h-[60vh] place-content-center justify-items-center gap-5 text-muted">
      <div className={`size-16 rounded-full border-2 border-line-2 border-t-gold ${error ? "border-t-enemy" : "animate-spin"}`} />
      {message}
    </div>
  );
}

function Footer({ state, onTierChange }: { state: AppState; onTierChange: () => Promise<unknown> }) {
  return (
    <footer className="mt-5 flex flex-wrap items-center justify-between gap-2">
      <TierPicker tiers={state.tiers} selected={state.selected_tier} onChange={onTierChange} />
      {state.status === "ok" && (
        <span className="text-xs text-faint">
          model {state.model} · patch {state.patch} · {state.latency_ms} ms
        </span>
      )}
    </footer>
  );
}
