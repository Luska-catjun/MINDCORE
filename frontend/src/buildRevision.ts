/**
 * A deployment-only diagnostic: exposed in the browser console and on window
 * without adding a permanent visible UI element.
 */
export const BUILD_REVISION = import.meta.env.VITE_BUILD_REVISION || "unknown";

declare global {
  interface Window {
    __MINDCORE_BUILD_REVISION__?: string;
  }
}

if (typeof window !== "undefined") {
  window.__MINDCORE_BUILD_REVISION__ = BUILD_REVISION;
  console.info(`[MINDCORE_BUILD] revision=${BUILD_REVISION}`);
}
