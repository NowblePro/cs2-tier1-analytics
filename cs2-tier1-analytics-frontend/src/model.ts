export type Page = "dashboard" | "teams" | "matches" | "upcoming" | "data";

export type LoadState<T> = {
  data: T;
  loading: boolean;
  error: string | null;
};

export type Filters = {
  days: number;
  window: number;
  statsWindow: string;
  topLimit: number;
  map: string;
};

export const NAV: Array<{ id: Page; label: string }> = [
  { id: "dashboard", label: "Дашборд" },
  { id: "teams", label: "Команды" },
  { id: "matches", label: "Матчи" },
  { id: "upcoming", label: "Будущие" },
  { id: "data", label: "Данные" },
];

export const initialFilters: Filters = {
  days: 30,
  window: 20,
  statsWindow: "LAST_MONTH",
  topLimit: 50,
  map: "all",
};
