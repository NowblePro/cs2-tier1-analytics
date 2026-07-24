import type { CSSProperties, ReactNode } from "react";


export type Tone = "good" | "warn" | "bad" | "neutral" | "info";


export function StatusDot({ tone = "neutral" }: { tone?: Exclude<Tone, "info"> }) {
  return <span className={`status-dot ${tone}`} aria-hidden="true" />;
}


export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}


export function Panel({
  title,
  meta,
  action,
  children,
  className = "",
}: {
  title: string;
  meta?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <header className="panel-head">
        <div>
          <h2>{title}</h2>
          {meta && <p>{meta}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}


export function TableState({
  loading,
  error,
  empty,
  onRetry,
}: {
  loading: boolean;
  error: string | null;
  empty: boolean;
  onRetry: () => void;
}) {
  if (loading) {
    return <div className="table-skeleton" aria-label="Loading"><i /><i /><i /><i /></div>;
  }
  if (error) {
    return (
      <div className="inline-state error">
        <div><strong>Не удалось загрузить данные</strong><span>{error}</span></div>
        <button onClick={onRetry}>Повторить</button>
      </div>
    );
  }
  if (empty) {
    return (
      <div className="inline-state">
        <div>
          <strong>Нет данных для этого раздела</strong>
          <span>Расширьте период или запустите загрузку данных.</span>
        </div>
      </div>
    );
  }
  return null;
}


export function TeamMark({ name }: { name: string }) {
  const initials = name
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  const hue = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 360;
  return (
    <span
      className="team-mark"
      style={{ "--team-hue": hue } as CSSProperties}
    >
      {initials}
    </span>
  );
}


export function PageTitle({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}


export function Metric({
  label,
  value,
  signed = false,
}: {
  label: string;
  value: string;
  signed?: boolean;
}) {
  const positive = signed && value.startsWith("+");
  const negative = signed && value.startsWith("-");
  return (
    <div className="metric">
      <span>{label}</span>
      <strong className={positive ? "positive" : negative ? "negative" : ""}>{value}</strong>
    </div>
  );
}


export function Toggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="toggle">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={value}
        onChange={(event) => onChange(event.target.checked)}
      />
      <i />
    </label>
  );
}
