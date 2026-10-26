// Decky backend calls have no timeout of their own: if the Python backend
// failed to load or is stuck, a call never settles and the panel waits
// forever. Everything here is plain TypeScript with no imports, so the
// frontend tests can load it directly.

export class BackendTimeoutError extends Error {
  constructor(readonly method: string, readonly timeoutMs: number) {
    super(`${method} did not answer within ${Math.round(timeoutMs / 1000)} s`);
    this.name = "BackendTimeoutError";
  }
}

export function isTimeout(error: unknown): error is BackendTimeoutError {
  return error instanceof BackendTimeoutError || (error instanceof Error && error.name === "BackendTimeoutError");
}

/** Settle with the call's result, or reject with BackendTimeoutError after timeoutMs. */
export function withTimeout<T>(call: Promise<T>, timeoutMs: number, method: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new BackendTimeoutError(method, timeoutMs)), timeoutMs);
    call.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

/** Wrap a backend callable so every call has a timeout. */
export function timed<A extends unknown[], R>(
  method: string, call: (...args: A) => Promise<R>, timeoutMs: number,
): (...args: A) => Promise<R> {
  return (...args: A) => {
    let started: Promise<R>;
    try { started = call(...args); } catch (error) { return Promise.reject(error); }
    return withTimeout(started, timeoutMs, method);
  };
}

/**
 * One in-flight request shared by every caller (opening the panel and a
 * manual rescan, say). A failed or timed-out request is cleared at once, so
 * the next caller makes a fresh backend call instead of waiting on a dead one.
 * A late answer from a request that already timed out is ignored.
 */
export function sharedRequest<T>(run: () => Promise<T>, onResult?: (value: T) => void) {
  let pending: Promise<T> | null = null;
  let generation = 0;
  return {
    run(): Promise<T> {
      if (pending) return pending;
      const mine = ++generation;
      let request: Promise<T>;
      try { request = run(); } catch (error) { request = Promise.reject(error); }
      const current: Promise<T> = request.then((value) => {
        if (mine === generation && onResult) onResult(value);
        return value;
      }).finally(() => {
        if (pending === current) pending = null;
      });
      pending = current;
      return current;
    },
    get inFlight() { return pending !== null; },
  };
}

export type PollResult = "continue" | "stop";
export type Poller = { stop(): void; readonly stopped: boolean };

/**
 * Poll without overlap: the next poll is scheduled only after the previous
 * one settles (each poll should use a timed() call, so it always settles).
 * Failures are reported with a running count; onError decides whether to
 * retry (with backoff, capped at maxDelayMs) or stop. A success after
 * failures calls onRecover.
 */
export function startPoller(options: {
  poll: () => Promise<PollResult>;
  intervalMs: number;
  onError: (error: unknown, consecutiveFailures: number) => "retry" | "stop";
  onRecover?: () => void;
  maxDelayMs?: number;
  immediate?: boolean;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (timer: unknown) => void;
}): Poller {
  const setTimer = options.setTimer ?? ((callback, ms) => setTimeout(callback, ms));
  const clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>));
  const maxDelay = options.maxDelayMs ?? 5000;
  let stopped = false;
  let failures = 0;
  let timer: unknown;
  const schedule = (ms: number) => {
    if (!stopped) timer = setTimer(() => void tick(), ms);
  };
  const tick = async () => {
    if (stopped) return;
    let result: PollResult;
    try {
      result = await options.poll();
    } catch (error) {
      if (stopped) return;
      failures += 1;
      if (options.onError(error, failures) === "stop") { stopped = true; return; }
      schedule(Math.min(maxDelay, options.intervalMs * 2 ** failures));
      return;
    }
    if (stopped) return;
    if (failures) { failures = 0; options.onRecover?.(); }
    if (result === "stop") { stopped = true; return; }
    schedule(options.intervalMs);
  };
  schedule(options.immediate ? 0 : options.intervalMs);
  return {
    stop() { stopped = true; clearTimer(timer); },
    get stopped() { return stopped; },
  };
}
