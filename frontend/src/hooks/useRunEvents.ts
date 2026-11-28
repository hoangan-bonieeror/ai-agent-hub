import { useEffect, useRef, useState } from "react";
import { runEventsUrl } from "../api/client";
import type { HubEvent } from "../api/types";

export const EVENT_TYPES = [
  "workflow.started",
  "task.queued",
  "task.started",
  "task.completed",
  "task.failed",
  "task.skipped",
  "task.cancelled",
  "review.completed",
  "task.revision_requested",
  "workflow.completed",
  "workflow.failed",
  "workflow.cancelled",
  "run.status",
] as const;

const TERMINAL = new Set(["workflow.completed", "workflow.failed", "workflow.cancelled", "run.status"]);

export type StreamState = "connecting" | "live" | "ended" | "error";

/** Follow a run's Server-Sent Events. Calls `onEvent` for each one and stops
 * when the run ends (the browser would otherwise reconnect forever). */
export function useRunEvents(runId: string | undefined, enabled: boolean, onEvent: (e: HubEvent) => void) {
  const [events, setEvents] = useState<HubEvent[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!runId || !enabled) return;
    setEvents([]);
    setState("connecting");
    const source = new EventSource(runEventsUrl(runId));
    source.onopen = () => setState("live");
    const listen = (type: string) => (msg: MessageEvent<string>) => {
      let event: HubEvent;
      try {
        event = { type, ...JSON.parse(msg.data) } as HubEvent;
      } catch {
        return;
      }
      if (type !== "run.status") setEvents((prev) => [...prev, event]);
      handler.current(event);
      if (TERMINAL.has(type)) {
        source.close();
        setState("ended");
      }
    };
    const listeners = EVENT_TYPES.map((t) => [t, listen(t)] as const);
    listeners.forEach(([t, fn]) => source.addEventListener(t, fn as EventListener));
    source.onerror = () => {
      // CONNECTING means the browser is retrying (it resumes with Last-Event-ID).
      if (source.readyState === EventSource.CLOSED) setState("error");
    };
    return () => {
      listeners.forEach(([t, fn]) => source.removeEventListener(t, fn as EventListener));
      source.close();
    };
  }, [runId, enabled]);

  return { events, state };
}
