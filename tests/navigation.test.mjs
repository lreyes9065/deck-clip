import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

test("export manager survives remount and deletion invalidates the list without navigating", async () => {
  const source = readFileSync(new URL("../src/utils/navigation.ts", import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
  const navigation = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
  navigation.showExports(true);
  let notifications = 0;
  const unmount = navigation.subscribeNavigation(() => notifications++);
  unmount();
  // The old modal callback can finish while the panel has no subscriber.
  navigation.invalidateExports();
  assert.equal(notifications, 0);
  assert.equal(navigation.getNavigation().managingExports, true);
  assert.equal(navigation.getNavigation().exportsRevision, 1);
  const unsubscribe = navigation.subscribeNavigation(() => notifications++);
  navigation.invalidateExports();
  assert.equal(notifications, 1);
  assert.equal(navigation.getNavigation().managingExports, true);
  navigation.showExports(false);
  assert.equal(navigation.getNavigation().managingExports, false);
  unsubscribe();
});
