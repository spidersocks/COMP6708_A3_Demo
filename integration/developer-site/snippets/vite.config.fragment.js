// Merge into developer-site/frontend/tailwindcss4/vite.config.js
// inside build.rollupOptions.input (alongside littleScholarsBot):
//
//   import path from "node:path";
//   import { fileURLToPath } from "node:url";
//   const __dirname = path.dirname(fileURLToPath(import.meta.url));
//
//   input: {
//     main: path.resolve(__dirname, "index.html"),
//     littleScholarsBot: path.resolve(__dirname, "little-scholars-bot.html"),
//     comp6708A3Demo: path.resolve(__dirname, "comp6708-a3-demo.html"),  // ADD THIS
//   }

export const COMP6708_VITE_INPUT_KEY = "comp6708A3Demo";
export const COMP6708_VITE_INPUT_FILE = "comp6708-a3-demo.html";
