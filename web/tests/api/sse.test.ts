import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { subscribeEvents } from "@/api/sse";

class FakeEventSource {
  url: string;
  withCredentials: boolean;
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  onerror: ((e: Event) => void) | null = null;
  closed = false;
  static last: FakeEventSource | null = null;

  constructor(url: string, init?: EventSourceInit) {
    this.url = url;
    this.withCredentials = init?.withCredentials ?? false;
    FakeEventSource.last = this;
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    (this.listeners[type] ||= []).push(fn);
  }
  removeEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners[type] = (this.listeners[type] || []).filter((x) => x !== fn);
  }
  close() {
    this.closed = true;
  }
  dispatch(type: string, data: unknown, lastEventId?: string) {
    const event = new MessageEvent(type, {
      data: typeof data === "string" ? data : JSON.stringify(data),
      lastEventId: lastEventId ?? "",
    });
    (this.listeners[type] || []).forEach((fn) => fn(event));
  }
}

describe("subscribeEvents", () => {
  beforeEach(() => {
    (globalThis as unknown as { EventSource: typeof FakeEventSource }).EventSource =
      FakeEventSource;
  });
  afterEach(() => {
    delete (globalThis as unknown as { EventSource?: unknown }).EventSource;
  });

  it("routes named events to handlers and tracks last-event-id", () => {
    const onLog = vi.fn();
    const onStatus = vi.fn();
    const sub = subscribeEvents("/runs/abc/events", {
      log: onLog,
      status: onStatus,
    });
    FakeEventSource.last!.dispatch("log", { line: "hi" }, "1");
    FakeEventSource.last!.dispatch("status", { status: "success" }, "2");
    expect(onLog).toHaveBeenCalledWith({ line: "hi" }, "1");
    expect(onStatus).toHaveBeenCalledWith({ status: "success" }, "2");
    sub.close();
    expect(FakeEventSource.last!.closed).toBe(true);
  });

  it("passes credentials flag to EventSource", () => {
    subscribeEvents("/policies/stream", {});
    expect(FakeEventSource.last!.withCredentials).toBe(true);
  });

  it("invokes onError when source errors", () => {
    const onError = vi.fn();
    subscribeEvents("/x", {}, { onError });
    const err = new Event("error");
    FakeEventSource.last!.onerror?.(err);
    expect(onError).toHaveBeenCalled();
  });
});

class FailingEventSource {
  static instances: FailingEventSource[] = [];
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  onopen: (() => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  readyState = 0;
  closed = false;
  constructor(public url: string) {
    FailingEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    (this.listeners[type] ||= []).push(fn);
  }
  removeEventListener() {}
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emitFatalError() {
    this.readyState = 2; // browser has given up reconnecting
    this.onerror?.(new Event("error"));
  }
}

describe("subscribeEvents self-healing", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FailingEventSource.instances = [];
    (globalThis as unknown as { EventSource: unknown }).EventSource =
      FailingEventSource;
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    delete (globalThis as unknown as { EventSource?: unknown }).EventSource;
  });

  it("recreates the EventSource with backoff after a fatal error", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ authenticated: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const sub = subscribeEvents("/policies/stream", { generation: vi.fn() });
    expect(FailingEventSource.instances).toHaveLength(1);

    FailingEventSource.instances[0].emitFatalError();
    // Let the auth-status probe resolve, then advance past the backoff.
    await vi.advanceTimersByTimeAsync(5_000);

    expect(fetchMock).toHaveBeenCalledWith("/auth/status", {
      credentials: "include",
    });
    expect(FailingEventSource.instances).toHaveLength(2);

    sub.close();
    expect(FailingEventSource.instances[1].closed).toBe(true);
  });

  it("stops reconnecting after close()", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ authenticated: true }),
      })
    );
    const sub = subscribeEvents("/policies/stream", {});
    sub.close();
    FailingEventSource.instances[0].emitFatalError();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FailingEventSource.instances).toHaveLength(1);
  });

  it("reloads the page when the session has expired", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ authenticated: false }),
      })
    );
    const reload = vi.fn();
    const original = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...original, reload },
    });

    subscribeEvents("/policies/stream", {});
    FailingEventSource.instances[0].emitFatalError();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(reload).toHaveBeenCalled();
    expect(FailingEventSource.instances).toHaveLength(1);
    Object.defineProperty(window, "location", {
      configurable: true,
      value: original,
    });
  });
});
