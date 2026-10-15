import { Pause, Play, RotateCcw } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { time } from "../format";
import type { Replay } from "../types";

type Props = { replay: Replay; speeds: number[]; onChange: () => Promise<unknown> };

/** Playback controls, shown only while replaying a recording. `onChange` refreshes the page's state. */
export function ReplayBar({ replay, speeds, onChange }: Props) {
  // While the slider is being dragged, show where it is instead of where the replay is.
  const [dragging, setDragging] = useState<number | null>(null);
  const position = dragging ?? replay.position;

  async function togglePlay() {
    if (replay.ended) await api.seek(0);
    await api.pause(!replay.paused && !replay.ended);
    await onChange();
  }
  async function restart() {
    await api.seek(0);
    await api.pause(false);
    await onChange();
  }
  async function setSpeed(speed: number) {
    await api.speed(speed);
    await onChange();
  }
  async function commitSeek() {
    if (dragging === null) return;
    await api.seek(dragging);
    await onChange(); // the server's position is the new one now...
    setDragging(null); // ...so letting go of the slider no longer snaps it back to the old time
  }

  return (
    <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-panel px-3.5 py-2.5 sm:flex-nowrap">
      <span className="rounded-md border border-gold-lo px-2 py-0.5 text-[11px] font-bold tracking-widest text-gold uppercase">
        Replay
      </span>
      <RoundButton label={replay.paused || replay.ended ? "Play" : "Pause"} onClick={togglePlay}>
        {replay.paused || replay.ended ? <Play size={14} /> : <Pause size={14} />}
      </RoundButton>
      <RoundButton label="Restart" onClick={restart}>
        <RotateCcw size={14} />
      </RoundButton>
      <input
        type="range" aria-label="Game time" min={0} max={Math.floor(replay.duration)} step={5} value={position}
        onChange={(e) => setDragging(Number(e.target.value))}
        onPointerUp={commitSeek}
        onKeyUp={commitSeek}
        className="order-last w-full accent-gold sm:order-none sm:flex-1"
      />
      <span className="whitespace-nowrap text-muted">
        {time(position)} / {time(replay.duration)}
      </span>
      <div className="flex overflow-hidden rounded-lg border border-line-2">
        {speeds.map((speed) => (
          <button
            key={speed}
            onClick={() => setSpeed(speed)}
            className={`px-2.5 py-1 text-xs font-semibold ${speed === replay.speed ? "bg-gold-lo text-gold-hi" : "text-muted hover:text-ink"}`}
          >
            {speed}×
          </button>
        ))}
      </div>
    </div>
  );
}

function RoundButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      title={label}
      aria-label={label}
      onClick={onClick}
      className="grid size-8 shrink-0 place-items-center rounded-full border border-gold-lo bg-panel-2 text-gold-hi hover:border-gold"
    >
      {children}
    </button>
  );
}
