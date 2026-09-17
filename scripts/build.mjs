import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const project = dirname(dirname(fileURLToPath(import.meta.url)));
let activeChild;
let interrupted = false;
const interrupt = (signal) => {
  interrupted = true;
  process.exitCode = signal === "SIGINT" ? 130 : 143;
  activeChild?.kill(signal);
};
const onInterrupt = () => interrupt("SIGINT");
const onTerminate = () => interrupt("SIGTERM");
process.on("SIGINT", onInterrupt);
process.on("SIGTERM", onTerminate);

function run(script, args, env = process.env) {
  return new Promise((resolve, reject) => {
    if (interrupted) { reject(new Error("Build interrupted")); return; }
    const child = spawn(process.execPath, [script, ...args], {
      cwd: project, env, stdio: "inherit",
    });
    activeChild = child;
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      activeChild = undefined;
      if (code === 0) resolve();
      else reject(new Error(`${script} failed (${signal ?? code})`));
    });
  });
}

// Keep staging beside node_modules for normal package resolution. Each build
// owns its directory, and finally removes it on success or compiler failure.
const staging = await mkdtemp(join(project, ".deckclip-build-"));
try {
  await run("node_modules/typescript/bin/tsc", [
    "--project", "tsconfig.json", "--outDir", staging,
    "--sourceMap", "--inlineSources", "--noEmitOnError",
  ]);
  await run("node_modules/rollup/dist/bin/rollup", ["-c"], {
    ...process.env, DECKCLIP_BUILD_DIR: staging,
  });
} finally {
  await rm(staging, { recursive: true, force: true });
  process.off("SIGINT", onInterrupt);
  process.off("SIGTERM", onTerminate);
}
