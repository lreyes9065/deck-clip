import deckyPlugin from "@decky/rollup";
import { join, resolve, sep } from "node:path";
import { readFileSync } from "node:fs";

const config = deckyPlugin({});
if (process.env.DECKCLIP_BUILD_DIR) {
  // Release builds use tsc's one-shot output. Avoid the compiler plugin's
  // watch-program lifecycle; retain the rest of Decky's bundling defaults.
  config.input = join(process.env.DECKCLIP_BUILD_DIR, "index.js");
  config.plugins = config.plugins.filter((plugin) => plugin.name !== "typescript");
  const staging = resolve(process.env.DECKCLIP_BUILD_DIR) + sep;
  config.plugins.unshift({
    name: "deckclip-compiled-sources",
    load(id) {
      if (!id.startsWith(staging) || !id.endsWith(".js")) return null;
      // Compose tsc's maps so debugging still points to the original TSX.
      return {
        code: readFileSync(id, "utf8"),
        map: JSON.parse(readFileSync(`${id}.map`, "utf8")),
      };
    },
  });
}
export default config;
