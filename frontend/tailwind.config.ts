import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "hsl(220 24% 7%)",
        foreground: "hsl(0 0% 98%)",
        panel: "hsl(220 22% 10%)",
        muted: "hsl(220 14% 25%)",
        accent: "hsl(150 84% 48%)",
        danger: "hsl(0 84% 60%)",
        warn: "hsl(38 92% 50%)",
        border: "hsl(220 14% 18%)",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "Inter", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
