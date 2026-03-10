// What it does: Derives the API base URL from the current browser path so the SPA
//   works both at /ui/ and behind Tailscale Serve path prefixes like /queue/ui/.
// Decision it drives: Every store file imports API from here — single source of truth
//   for the backend URL, prevents circular imports between domain stores and index.js.

const pathBase = window.location.pathname.replace(/\/ui\/.*$/, '').replace(/\/ui$/, '');
export const API = `${pathBase}/api`;
