export const ROLES: Record<string, string> = {
  TOP: "Top",
  JUNGLE: "Jungle",
  MIDDLE: "Mid",
  BOTTOM: "Bot",
  UTILITY: "Support",
};
export const ROLE_ORDER = Object.keys(ROLES);

export const time = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
export const percent = (p: number) => `${Math.round(p * 100)}%`;
export const gold = (n: number) => Math.round(n).toLocaleString();
export const titleCase = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();
export const withArticle = (s: string) => `${/^[aeiou]/i.test(s) ? "an" : "a"} ${titleCase(s)}`;
