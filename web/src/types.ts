export type Item = { item_id: number; name: string; icon: string };

export type Recommendation = Item & {
  cost: number; // full price
  remaining: number; // price minus components already held
  probability: number; // how likely this is the next item bought
  tags: string[];
};

export type Player = {
  champion: string;
  icon: string;
  team: number;
  role: string;
  level: number;
  kda: [number, number, number];
  cs: number;
  is_me: boolean;
  items: Item[];
  trinket: Item | null;
};

export type Replay = {
  name: string;
  position: number; // game seconds
  duration: number;
  speed: number;
  paused: boolean;
  ended: boolean;
};

export type Game = {
  status: "ok";
  game_time: number;
  role: string;
  tier: string;
  gold: number;
  level: number;
  kda: [number, number, number];
  cs: number;
  champion_icon: string;
  win_probability: number;
  team_item_value_diff: number;
  enemy: { attack: number; magic: number; armor: number; mr: number };
  recommendations: Recommendation[];
  players: Player[];
  my_team: number;
  model: string;
  patch: string;
  patch_warning: string | null;
  latency_ms: number;
};

type NoGame = { status: "waiting" | "error"; message: string };

export type AppState = (Game | NoGame) & {
  tiers: string[];
  selected_tier: string;
  win_history: [number, number][]; // [game seconds, win probability]
  replay: Replay | null;
  replay_speeds: number[];
};
