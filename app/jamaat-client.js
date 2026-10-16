// My Mosque Pro — read collected jamaat times from Supabase (for the hosted app).
// The anon key is safe to ship in the app: the database only lets it READ
// mosques and jamaat times (see supabase/schema.sql).

const SUPABASE_URL = "https://YOUR-PROJECT.supabase.co";
const SUPABASE_ANON_KEY = "YOUR-ANON-KEY";

const todayUK = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London" }).format(new Date()); // YYYY-MM-DD

/**
 * Jamaat times for today for the given mosque ids.
 * Returns { [mosqueId]: { Fajr, Dhuhr, Asr, Maghrib, Isha, jumuah, source } }.
 * Mosques with no collected times are simply missing — the app then shows
 * start times, as it does now.
 */
export async function loadJamaatToday(mosqueIds) {
  if (!mosqueIds.length) return {};
  const url = `${SUPABASE_URL}/rest/v1/jamaat_upcoming?select=*` +
    `&day=eq.${todayUK()}&mosque_id=in.(${mosqueIds.join(",")})`;
  const res = await fetch(url, {
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}` },
  });
  if (!res.ok) return {};
  const hm = (t) => (t ? t.slice(0, 5) : null);          // '13:30:00' → '13:30'
  const out = {};
  for (const r of await res.json()) {
    out[r.mosque_id] = {
      Fajr: hm(r.fajr), Dhuhr: hm(r.dhuhr), Asr: hm(r.asr), Maghrib: hm(r.maghrib), Isha: hm(r.isha),
      jumuah: r.jumuah, source: new URL(r.source_url).hostname.replace(/^www\./, ""),
    };
  }
  return out;
}

// In the app: call loadJamaatToday() for the mosques on screen (followed
// mosques, the open mosque page, nearby list), keep the result in memory,
// and have jamFor(m) return it ahead of the built-in fallback. Refresh once
// an hour and when the date changes.
