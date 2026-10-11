import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#0f172a", soft: "#334155", muted: "#64748b" },
        line: "#e2e8f0",
        canvas: "#f8fafc",
        accent: { DEFAULT: "#4338ca", soft: "#eef2ff" },
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"] },
    },
  },
  plugins: [],
};

export default config;
