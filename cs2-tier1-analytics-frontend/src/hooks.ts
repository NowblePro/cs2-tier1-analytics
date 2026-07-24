import { useCallback, useEffect, useState } from "react";

import { api } from "./services/api";
import { NAV, type LoadState, type Page } from "./model";


export function useHashPage() {
  const read = (): Page => {
    if (typeof window === "undefined") return "dashboard";
    const candidate = window.location.hash.replace("#/", "").split("/")[0] as Page;
    return NAV.some((item) => item.id === candidate) ? candidate : "dashboard";
  };
  const [page, setPageState] = useState<Page>(read);
  useEffect(() => {
    const onHash = () => setPageState(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const setPage = (next: Page) => {
    window.location.hash = `#/${next}`;
    setPageState(next);
  };
  return [page, setPage] as const;
}


export function useResource<T>(loader: () => Promise<T>, fallback: T, deps: unknown[] = []) {
  const [state, setState] = useState<LoadState<T>>({
    data: fallback,
    loading: true,
    error: null,
  });
  const load = useCallback(async () => {
    if (api.demo) {
      setState({ data: fallback, loading: false, error: null });
      return;
    }
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const data = await loader();
      setState({ data, loading: false, error: null });
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        error: error instanceof Error ? error.message : "Unknown request error",
      }));
    }
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => void load(), [load]);
  return { ...state, reload: load };
}
