/** Accept an origin, /api base, or /api/v1 base without duplicating prefixes. */
export function apiUrl(
  path: string,
  configured = import.meta.env?.VITE_API_BASE_URL,
): string {
  const root = (configured ?? '')
    .trim()
    .replace(/\/+$/, '')
    .replace(/\/api(?:\/v1)?$/, '')
  return `${root}${path}`
}
