const STATUS_LABELS: Record<string, string> = {
  completed: "Завершён",
  scheduled: "Запланирован",
  upcoming: "Предстоящий",
  live: "В эфире",
  cancelled: "Остановлен",
  failed: "Ошибка",
  running: "Выполняется",
  queued: "В очереди",
  cancelling: "Останавливается",
  interrupted: "Прерван перезапуском",
  partial: "Частично",
  finished_unknown: "Завершён без результата",
};


export const statusLabel = (value: unknown) =>
  STATUS_LABELS[String(value ?? "").toLowerCase()] ?? String(value ?? "Неизвестно");


export const completenessLabel = (value: unknown) =>
  ({
    schedule: "Расписание",
    result: "Результат",
    maps: "Карты",
    players: "Игроки",
    rounds: "Раунды",
  })[String(value)] ?? "Нет данных";


export const metricLabel = (value: unknown) =>
  ({
    "Series win rate": "Победы в сериях",
    "Map win rate": "Победы на картах",
    "T round win rate": "Победы за T",
    "CT round win rate": "Победы за CT",
    "Pistol win rate": "Победы в пистолетках",
    "GRID series WR": "Победы в сериях GRID",
    "First kill": "Первое убийство",
  })[String(value)] ?? String(value ?? "Показатель");
