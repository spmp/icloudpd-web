type Handler = (data: unknown, lastEventId: string) => void;

export interface SseSubscription {
  close(): void;
}

export interface SseOptions {
  onError?: (e: Event) => void;
}

// EventSource.CLOSED (2); referenced numerically so test fakes without the
// static constants still work.
const READY_STATE_CLOSED = 2;
const MAX_BACKOFF_MS = 30_000;

/**
 * Subscribe to a server-sent-events endpoint, self-healing on failure.
 *
 * The browser's EventSource only auto-reconnects on transient network
 * drops. On anything else — session-cookie expiry (401), a proxy 502, a
 * backend restart mid-stream — it fires `error` and gives up permanently.
 * Here we tear the source down and recreate it with exponential backoff,
 * and if the failure turns out to be an expired session we reload the page
 * so the login screen appears instead of a silently frozen UI.
 */
export function subscribeEvents(
  url: string,
  handlers: Record<string, Handler>,
  opts: SseOptions = {}
): SseSubscription {
  let source: EventSource | null = null;
  let closed = false;
  let attempt = 0;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;

  const attach = (es: EventSource) => {
    for (const [name, fn] of Object.entries(handlers)) {
      es.addEventListener(name, (event: MessageEvent) => {
        let parsed: unknown = event.data;
        try {
          parsed = JSON.parse(event.data);
        } catch {
          /* raw string */
        }
        fn(parsed, event.lastEventId);
      });
    }
  };

  const scheduleReconnect = () => {
    attempt += 1;
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** attempt);
    retryTimer = setTimeout(connect, delay);
  };

  const handleFatalError = async () => {
    // Distinguish "session expired" from other failures: if the backend is
    // reachable but says we're unauthenticated, reload so the login modal
    // shows. Otherwise just keep retrying with backoff.
    try {
      const res = await fetch("/auth/status", { credentials: "include" });
      if (res.ok) {
        const body = (await res.json()) as { authenticated?: boolean };
        if (body.authenticated === false) {
          window.location.reload();
          return;
        }
      }
    } catch {
      /* backend unreachable; fall through to backoff */
    }
    if (!closed) scheduleReconnect();
  };

  const connect = () => {
    if (closed) return;
    const es = new EventSource(url, { withCredentials: true });
    source = es;
    attach(es);
    es.onopen = () => {
      attempt = 0;
    };
    es.onerror = (e: Event) => {
      opts.onError?.(e);
      if (closed) return;
      // CONNECTING means the browser is retrying by itself; only take over
      // once it has given up (CLOSED).
      if (es.readyState === READY_STATE_CLOSED) {
        es.close();
        if (source === es) source = null;
        void handleFatalError();
      }
    };
  };

  connect();

  return {
    close() {
      closed = true;
      if (retryTimer !== undefined) clearTimeout(retryTimer);
      source?.close();
      source = null;
    },
  };
}
