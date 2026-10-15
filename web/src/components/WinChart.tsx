import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { gold, percent, time } from "../format";
import { Panel } from "./ui";

type Props = { probability: number; history: [number, number][]; gameTime: number; itemValueDiff: number };

export function WinChart({ probability, history, gameTime, itemValueDiff }: Props) {
  const points = history.map(([t, p]) => ({ t, p }));
  const end = Math.max(gameTime, 15 * 60); // at least a 15-minute axis, so early games aren't stretched
  return (
    <Panel title="Win chance" note="from the game state right now">
      <div className="flex items-baseline gap-2.5">
        <span className={`text-4xl font-bold ${probability >= 0.5 ? "text-good" : "text-enemy"}`}>{percent(probability)}</span>
        <span className="text-xs text-faint">
          {itemValueDiff >= 0 ? "+" : "−"}
          {gold(Math.abs(itemValueDiff))}g in items vs enemy team
        </span>
      </div>
      <div className="mt-3 h-28">
        <ResponsiveContainer>
          <AreaChart data={points} margin={{ top: 6, right: 6, bottom: 6, left: 6 }}>
            <defs>
              <linearGradient id="winFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="var(--color-gold)" stopOpacity={0.35} />
                <stop offset="1" stopColor="var(--color-gold)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="t" type="number" domain={[0, end]} hide />
            <YAxis domain={[0, 1]} hide />
            <ReferenceLine y={0.5} stroke="var(--color-line-2)" strokeDasharray="4 4" />
            <Tooltip
              formatter={(value) => [percent(Number(value)), "Win chance"]}
              labelFormatter={(t) => time(Number(t))}
              contentStyle={{ background: "var(--color-panel)", border: "1px solid var(--color-line-2)", borderRadius: 8 }}
            />
            <Area
              dataKey="p" type="monotone" stroke="var(--color-gold)" strokeWidth={2}
              fill="url(#winFill)" isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="flex justify-between text-[11px] text-faint">
        <span>0:00</span>
        <span>{time(end)}</span>
      </div>
    </Panel>
  );
}
