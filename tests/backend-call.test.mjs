import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

async function load() {
  const source = readFileSync(new URL("../src/utils/backendCall.ts", import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const never = () => new Promise(() => {});

test("a timed-out list request is cleared so the next rescan makes a fresh call", async () => {
  const { timed, sharedRequest, isTimeout } = await load();
  let calls = 0;
  const listClips = timed("list_clips", () => { calls++; return never(); }, 30);
  const library = sharedRequest(() => listClips());
  const first = library.run();
  assert.equal(library.run(), first, "overlapping callers share one request");
  assert.equal(calls, 1);
  await assert.rejects(first, (error) => isTimeout(error) && /list_clips did not answer/.test(error.message));
  assert.equal(library.inFlight, false, "timeout must clear the shared in-flight promise");
  const second = library.run();
  assert.notEqual(second, first);
  assert.equal(calls, 2, "the next rescan must reach the backend again");
  await assert.rejects(second, isTimeout);
});

test("a retry after a timeout succeeds and a late answer from the dead call is ignored", async () => {
  const { timed, sharedRequest } = await load();
  const answers = [];
  let resolveFirst;
  const backend = [
    () => new Promise((resolve) => { resolveFirst = resolve; }), // hangs past the timeout
    async () => ["clip-b"],
  ];
  let calls = 0;
  const listClips = timed("list_clips", () => backend[calls++](), 30);
  const published = [];
  const library = sharedRequest(() => listClips(), (clips) => published.push(clips));
  await assert.rejects(library.run());
  answers.push(await library.run());
  assert.deepEqual(answers, [["clip-b"]]);
  resolveFirst(["stale"]);
  await sleep(10);
  assert.deepEqual(published, [["clip-b"]], "late result must not be published");
  assert.equal(library.inFlight, false);
});

test("a failed (rejected) list request is cleared too", async () => {
  const { timed, sharedRequest } = await load();
  let calls = 0;
  const library = sharedRequest(timed("list_clips", async () => { calls++; if (calls === 1) throw new Error("boom"); return []; }, 1000));
  await assert.rejects(library.run(), /boom/);
  assert.deepEqual(await library.run(), []);
  assert.equal(calls, 2);
});

test("polls never overlap, even when a status call is slower than the interval", async () => {
  const { startPoller } = await load();
  let running = 0, maxRunning = 0, polls = 0;
  await new Promise((done) => {
    startPoller({
      intervalMs: 1,
      poll: async () => {
        running++; maxRunning = Math.max(maxRunning, running); polls++;
        await sleep(15);
        running--;
        if (polls >= 6) { done(); return "stop"; }
        return "continue";
      },
      onError: () => "stop",
    });
  });
  await sleep(30);
  assert.equal(maxRunning, 1);
  assert.equal(polls, 6, "stop must end polling");
});

test("a hanging poll times out, reports an error, retries and recovers", async () => {
  const { startPoller, timed, isTimeout } = await load();
  const responses = [never, never, async () => "running", async () => "complete"];
  let index = 0;
  const getStatus = timed("get_export_status", () => responses[index++](), 20);
  const errors = [];
  let recovered = 0;
  const states = [];
  await new Promise((done) => {
    startPoller({
      intervalMs: 1, maxDelayMs: 5,
      poll: async () => {
        const state = await getStatus();
        states.push(state);
        if (state === "complete") { done(); return "stop"; }
        return "continue";
      },
      onError: (error, failures) => { errors.push([isTimeout(error), failures]); return "retry"; },
      onRecover: () => recovered++,
    });
  });
  assert.deepEqual(errors, [[true, 1], [true, 2]]);
  assert.equal(recovered, 1);
  assert.deepEqual(states, ["running", "complete"]);
});

test("polling gives up when onError says stop, and stop() cancels the next poll", async () => {
  const { startPoller } = await load();
  let polls = 0;
  const failures = [];
  const poller = startPoller({
    intervalMs: 1, maxDelayMs: 2,
    poll: async () => { polls++; throw new Error("backend gone"); },
    onError: (_error, count) => { failures.push(count); return count >= 3 ? "stop" : "retry"; },
  });
  await sleep(60);
  assert.deepEqual(failures, [1, 2, 3]);
  assert.equal(poller.stopped, true);
  assert.equal(polls, 3);

  let later = 0;
  const other = startPoller({ intervalMs: 5, poll: async () => { later++; return "continue"; }, onError: () => "stop" });
  other.stop();
  await sleep(30);
  assert.equal(later, 0);
});

test("every backend callable is wrapped with a timeout", () => {
  const api = readFileSync(new URL("../src/api/deckclip.ts", import.meta.url), "utf8");
  const exported = api.match(/^export const \w+ = .*$/gm) ?? [];
  const calls = exported.filter((line) => !/TIMEOUT_MS = \d/.test(line));
  assert.ok(calls.length >= 12, "expected every backend method");
  for (const line of calls) assert.match(line, /= call</, line);
  assert.doesNotMatch(api.replace(/callable<A, R>\(method\)/, ""), /callable</, "no un-timed callable");
  const library = readFileSync(new URL("../src/api/library.ts", import.meta.url), "utf8");
  assert.match(library, /sharedRequest\(/);
  for (const hook of ["useExportJob.ts", "usePhoneTransfer.ts"]) {
    const source = readFileSync(new URL(`../src/hooks/${hook}`, import.meta.url), "utf8");
    assert.match(source, /startPoller\(/, hook);
    assert.doesNotMatch(source, /setInterval\(/, `${hook} must not use overlapping setInterval polling`);
  }
});
