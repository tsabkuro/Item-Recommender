import { api } from "../api";
import { titleCase, withArticle } from "../format";

/** Which rank's buying habits to imitate (the model takes tier as a feature). */
type Props = { tiers: string[]; selected: string; onChange: () => Promise<unknown> };

export function TierPicker({ tiers, selected, onChange }: Props) {
  return (
    <label className="flex items-center gap-2 text-[13px] text-muted">
      Recommend like {withArticle(selected).split(" ")[0]}
      <select
        value={selected}
        onChange={async (e) => {
          await api.setTier(e.target.value);
          await onChange();
        }}
        className="rounded-lg border border-gold-lo bg-panel-2 px-2 py-1 font-semibold text-gold-hi"
      >
        {tiers.map((tier) => (
          <option key={tier} value={tier}>
            {titleCase(tier)}
          </option>
        ))}
      </select>
      player
    </label>
  );
}
