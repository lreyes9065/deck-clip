import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

async function load() {
  const source = readFileSync(new URL("../src/utils/retry.ts", import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}

test("busy previews retry with capped exponential backoff, then give up", async () => {
  const { thumbnailRetryDelay, MAX_THUMBNAIL_ATTEMPTS } = await load();
  const noJitter = () => 0;
  assert.equal(thumbnailRetryDelay(0, noJitter), 250);
  assert.equal(thumbnailRetryDelay(1, noJitter), 500);
  assert.equal(thumbnailRetryDelay(3, noJitter), 2000);
  assert.equal(thumbnailRetryDelay(9, noJitter), 5000);
  assert.equal(thumbnailRetryDelay(MAX_THUMBNAIL_ATTEMPTS, noJitter), null);
  const jittered = thumbnailRetryDelay(0, () => 0.999);
  assert.ok(jittered >= 250 && jittered < 500);
});
