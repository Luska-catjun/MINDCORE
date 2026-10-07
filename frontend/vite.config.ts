import { validFeedbackEndpoint } from './src/services/feedbackContract.js'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const endpoint = loadEnv(mode, process.cwd(), "VITE_").VITE_MINDCORE_FEEDBACK_ENDPOINT?.trim();
  if (endpoint && !validFeedbackEndpoint(endpoint)) throw new Error("Feedback endpoint must be public HTTPS without credentials/query/fragment");
  return {
  plugins: [react()],
  // Public product builds use only bundled generic assets.
  publicDir: false,
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: { url: "http://localhost/" },
    },
    setupFiles: "./src/test/setup.ts",
  },
}
})
