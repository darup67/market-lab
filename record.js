#!/usr/bin/env node
/**
 * Kalshi 15-minute outcome recorder.
 *
 * Kalshi's API only exposes roughly the last ~30 hours of settled markets, and
 * trades disappear with them. Any question about these contracts that needs
 * more than a day of history can only be answered by data captured as it goes
 * past. That is this program's entire job: capture it, once, correctly.
 *
 * For every settled 15-minute crypto window it records the settlement, the
 * price path, and the features most hypotheses need — then never touches that
 * row again. Append-only JSONL, one file per series, keyed by window ticker so
 * a re-run is a no-op rather than a duplicate.
 *
 *   node record.js            record every new settled window
 *   node record.js --status   how much history is on disk
 *   node record.js --series KXETH15M   just one series
 */

import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync,
         statSync, openSync, closeSync, writeSync, unlinkSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE     = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(HERE, 'data');
const LOG_FILE = join(HERE, 'record.log');
const LOCK_FILE= join(HERE, '.lock');

const API = process.env.KALSHI_API || 'https://external-api.kalshi.com/trade-api/v2';
const SERIES = ['KXBTC15M', 'KXETH15M', 'KXXRP15M', 'KXSOL15M'];

const HTTP_TIMEOUT_MS = 20000;
const LOCK_STALE_MS   = 10 * 60 * 1000;
const LOG_MAX_BYTES   = 512 * 1024;
const TRADE_PAGES     = 10;    // 1000/page; a BTC window runs ~8k
const PATH_POINTS     = 15;    // downsampled path — 15 points by TRADE INDEX, not per minute

// Storage ceiling for data/. At the observed ~425 bytes/row and 384 rows a day
// this is roughly 57 MB a year, so 5 GB is about ninety years out — it is a
// guard against a bug writing in a loop, not a real capacity limit.
//
// Hitting it STOPS capture of data that cannot be backfilled, so it must never
// be a quiet stop: it logs at WARN volume, exits non-zero so launchctl shows a
// failing agent, and warns on approach rather than only on arrival.
const MAX_BYTES  = Number(process.env.KLAB_MAX_BYTES) || 5 * 1024 ** 3;
const WARN_AT    = 0.8;

// Commit and push new rows automatically.
//
// This data cannot be backfilled, and the recorder appends every 10 minutes —
// so left to manual commits the working tree is permanently dirty and the
// remote permanently stale. A disk failure would then lose everything since
// whenever someone last remembered. Auto-committing makes the private remote a
// real backup rather than a periodic snapshot.
//
// Set KLAB_AUTOCOMMIT=0 to record locally and commit by hand instead.
const AUTOCOMMIT = process.env.KLAB_AUTOCOMMIT !== '0';

const args = new Set(process.argv.slice(2));
const argVal = f => { const a = process.argv.slice(2); const i = a.indexOf(f); return i >= 0 ? a[i+1] : null; };

/* ---------------------------------------------------------------- plumbing */

function log(msg) {
  const line = `${new Date().toISOString()}  ${msg}\n`;
  process.stdout.write(line);
  try {
    if (existsSync(LOG_FILE) && statSync(LOG_FILE).size > LOG_MAX_BYTES) {
      writeFileSync(LOG_FILE, readFileSync(LOG_FILE, 'utf8').split('\n').slice(-400).join('\n'));
    }
    appendFileSync(LOG_FILE, line);
  } catch { /* best effort */ }
}

function acquireLock() {
  try {
    const fd = openSync(LOCK_FILE, 'wx'); writeSync(fd, String(process.pid)); closeSync(fd);
    return true;
  } catch {
    try {
      const age = Date.now() - statSync(LOCK_FILE).mtimeMs;
      const pid = Number(readFileSync(LOCK_FILE, 'utf8').trim());
      if (age < LOCK_STALE_MS && pid && pid !== process.pid) {
        try { process.kill(pid, 0); return false; } catch { /* dead */ }
      }
      unlinkSync(LOCK_FILE);
      const fd = openSync(LOCK_FILE, 'wx'); writeSync(fd, String(process.pid)); closeSync(fd);
      return true;
    } catch { return false; }
  }
}
const releaseLock = () => {
  try {
    if (existsSync(LOCK_FILE) && readFileSync(LOCK_FILE,'utf8').trim() === String(process.pid)) unlinkSync(LOCK_FILE);
  } catch {}
};

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Paging trades is exactly the pattern that trips Kalshi's rate limiter, so
// pace every request and back off when told to rather than losing a window.
let _last = 0;
async function apiGet(path, params = {}) {
  const url = new URL(`${API}${path}`);
  for (const [k, v] of Object.entries(params)) if (v != null) url.searchParams.set(k, String(v));
  let lastErr;
  for (let attempt = 0; attempt <= 4; attempt++) {
    const since = Date.now() - _last;
    if (since < 130) await sleep(130 - since);
    _last = Date.now();
    let res;
    try {
      res = await fetch(url, { signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
                               headers: { Accept: 'application/json' } });
    } catch (e) {
      lastErr = e;
      if (attempt < 4) { await sleep(1000 * 2 ** attempt); continue; }
      throw new Error(`${e.message} on ${path}`);
    }
    if (res.ok) return res.json();
    const retryable = res.status === 429 || res.status >= 500;
    lastErr = new Error(`HTTP ${res.status} on ${path}`);
    if (!retryable || attempt === 4) throw lastErr;
    const ra = Number(res.headers.get('retry-after'));
    await sleep(Number.isFinite(ra) && ra > 0 ? ra * 1000 : 1000 * 2 ** attempt);
  }
  throw lastErr;
}

/* ------------------------------------------------------------- the capture */

const fileFor = s => join(DATA_DIR, `${s}.jsonl`);

const fmtBytes = b =>
  b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(2)} GB`
: b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB`
: `${(b / 1024).toFixed(0)} KB`;

/** Total bytes under data/. */
function dataBytes() {
  let total = 0;
  for (const s of SERIES) {
    const f = fileFor(s);
    if (existsSync(f)) { try { total += statSync(f).size; } catch {} }
  }
  return total;
}

/** Window tickers already on disk. Read once per series; keeps re-runs free. */
function recorded(series) {
  const f = fileFor(series);
  if (!existsSync(f)) return new Set();
  const seen = new Set();
  for (const line of readFileSync(f, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    try { seen.add(JSON.parse(line).ticker); } catch { /* skip a torn line */ }
  }
  return seen;
}

const money = t => {
  const m = /\$([\d,]+(?:\.\d+)?)/.exec(String(t || ''));
  return m ? Number(m[1].replace(/,/g, '')) : null;
};

/**
 * Clock-aligned YES quotes, in cents: index m = [bid, ask] at open + m minutes
 * (0 = the quote at the open, from the first candle's open; 1..15 = each
 * minute's close). null where Kalshi returned no candle. This is the field to
 * use for "price at minute m" and for the opening print — see `path` below.
 */
async function minuteQuotes(series, m) {
  const o = Math.floor(Date.parse(m.open_time) / 1000);
  const c = Math.floor(Date.parse(m.close_time) / 1000);
  const d = await apiGet(`/series/${series}/markets/${m.ticker}/candlesticks`,
    { start_ts: o, end_ts: c, period_interval: 1 });
  return quotesFromCandles(d?.candlesticks || [], o, c);
}

function quotesFromCandles(candles, o, c) {
  const cents = x => (x == null || !Number.isFinite(Number(x))) ? null : Number((Number(x) * 100).toFixed(1));
  const n = Math.round((c - o) / 60);
  const q = Array(n + 1).fill(null);
  for (const k of candles) {
    const i = Math.round((k.end_period_ts - o) / 60);
    const b = cents(k.yes_bid?.close_dollars), a = cents(k.yes_ask?.close_dollars);
    if (i >= 1 && i <= n && b != null && a != null) q[i] = [b, a];
    if (i === 1) {
      const b0 = cents(k.yes_bid?.open_dollars), a0 = cents(k.yes_ask?.open_dollars);
      if (b0 != null && a0 != null) q[0] = [b0, a0];
    }
  }
  return q.some(Boolean) ? q : null;
}

/** Every executed price for one window, oldest first. */
async function tradePath(ticker) {
  let cursor = null; const pts = [];
  for (let page = 0; page < TRADE_PAGES; page++) {
    const d = await apiGet('/markets/trades',
      { ticker, limit: 1000, ...(cursor ? { cursor } : {}) });
    const trades = d?.trades || [];
    for (const t of trades) {
      let p = t.yes_price_dollars ?? t.yes_price;
      if (p == null) continue;
      p = Number(p);
      if (!Number.isFinite(p)) continue;
      pts.push({ t: t.created_time || '', p: p <= 1.001 ? p * 100 : p });
    }
    cursor = d?.cursor;
    if (!cursor || !trades.length) break;
  }
  pts.sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
  return pts;
}

/**
 * One row per settled window. Stores the derived features most tests need
 * plus a downsampled path.
 *
 * CAUTION — `path`, `first`, `min`/`max`/`range` and the quartiles come from
 * the NEWEST TRADE_PAGES×1000 trades only, and `path` is spaced by trade
 * index, not by time. Every BTC window hits that cap, so `first` is a
 * mid-window price, NOT the opening print, and path[k] is NOT minute k. Use
 * `minute_quotes` for anything time-based. (Found 2026-09-23.)
 */
function features(m, pts, minute_quotes = null) {
  const v = pts.map(x => x.p);
  const n = v.length;
  const at = frac => v[Math.min(n - 1, Math.floor(frac * (n - 1)))];
  const path = [];
  for (let i = 0; i < PATH_POINTS; i++) path.push(Number(at(i / (PATH_POINTS - 1)).toFixed(1)));

  const sv = m.settlement_value_dollars;
  const settledYes = sv == null ? null : (Number(sv) <= 1.001 ? Number(sv) > 0.5 : Number(sv) > 50);

  return {
    series: String(m.ticker).split('-')[0],
    ticker: m.ticker,
    window: String(m.ticker).split('-')[1] || null,
    open_time: m.open_time ?? null,
    close_time: m.close_time ?? null,
    target: money(m.yes_sub_title),
    settled_yes: settledYes,
    settlement: sv == null ? null : Number(sv),
    trades: n,
    volume_fp: m.volume_fp == null ? null : Number(m.volume_fp),
    first: Number(v[0].toFixed(1)),
    last:  Number(v[n - 1].toFixed(1)),
    min:   Number(Math.min(...v).toFixed(1)),
    max:   Number(Math.max(...v).toFixed(1)),
    range: Number((Math.max(...v) - Math.min(...v)).toFixed(1)),
    q25:   Number(at(0.25).toFixed(1)),
    q50:   Number(at(0.50).toFixed(1)),
    q75:   Number(at(0.75).toFixed(1)),
    path,
    minute_quotes,
    recorded_at: new Date().toISOString(),
  };
}

async function captureSeries(series) {
  const seen = recorded(series);
  let markets;
  try {
    const d = await apiGet('/markets',
      { series_ticker: series, status: 'settled', limit: 200, mve_filter: 'exclude' });
    markets = (d?.markets || []).filter(m => m?.ticker);
  } catch (e) {
    log(`${series}: cannot list settled markets — ${e.message}`);
    return { added: 0, skipped: 0, failed: 1 };
  }

  const todo = markets.filter(m => !seen.has(m.ticker));
  let added = 0, failed = 0;
  for (const m of todo) {
    let pts;
    try { pts = await tradePath(m.ticker); }
    catch (e) { log(`${series}: trades failed for ${m.ticker} — ${e.message}`); failed++; continue; }
    // A window nobody traded carries no information; recording it would only
    // dilute later statistics with rows that have no path.
    if (pts.length < 20) continue;
    // Quotes are best-effort: a candle failure must not lose the window.
    let mq = null;
    try { mq = await minuteQuotes(series, m); }
    catch (e) { log(`${series}: candles failed for ${m.ticker} — ${e.message}`); }
    try {
      appendFileSync(fileFor(series), JSON.stringify(features(m, pts, mq)) + '\n');
      added++;
    } catch (e) { log(`${series}: write failed for ${m.ticker} — ${e.message}`); failed++; }
  }
  return { added, skipped: markets.length - todo.length, failed };
}

/** Run a git command; never throws — a git problem must not break capture. */
function git(cmdArgs, timeout = 45000) {
  return new Promise(resolve => {
    execFile('git', cmdArgs, { cwd: HERE, timeout, killSignal: 'SIGKILL' },
      (err, stdout, stderr) => resolve({
        ok: !err,
        out: (stdout || '').trim(),
        err: (stderr || err?.message || '').trim(),
      }));
  });
}

/**
 * Persist newly captured rows to the private remote.
 *
 * Deliberately best-effort: a failed push is logged and the run still succeeds,
 * because losing the next window to a network blip would be a worse outcome
 * than a remote that is briefly behind. The next run pushes both.
 */
async function persist(added) {
  if (!AUTOCOMMIT) return;

  if (added) {
    const status = await git(['status', '--porcelain', 'data']);
    if (!status.ok) { log(`autocommit: git status failed — ${status.err}`); return; }
    if (status.out) {
      const add = await git(['add', 'data']);
      if (!add.ok) { log(`autocommit: git add failed — ${add.err}`); return; }
      const commit = await git(['-c', 'user.name=kalshi-recorder',
                                '-c', 'user.email=recorder@localhost',
                                'commit', '-q', '-m', `data: +${added} window(s)`]);
      if (!commit.ok) { log(`autocommit: commit failed — ${commit.err}`); return; }
    }
  }

  // Push whenever anything is unpushed, NOT only when this run captured rows.
  // A push that failed earlier (bad PATH, network down) would otherwise sit
  // until the next run that happened to capture something — so a quiet market
  // could leave the only copy of a window on one disk for hours.
  const ahead = await git(['rev-list', '--count', '@{u}..HEAD']);
  if (!ahead.ok) return;                       // no upstream configured — nothing to do
  const n = Number(ahead.out || 0);
  if (!n) return;

  const push = await git(['push', '-q', 'origin', 'HEAD']);
  if (!push.ok) {
    log(`autocommit: ${n} commit(s) unpushed, push failed (retries next run) — ${push.err.slice(0, 140)}`);
    return;
  }
  log(`autocommit: pushed ${n} commit(s)`);
}

/* ------------------------------------------------------------------- main */

async function main() {
  mkdirSync(DATA_DIR, { recursive: true });

  if (args.has('--status')) {
    let total = 0;
    for (const s of SERIES) {
      const f = fileFor(s);
      if (!existsSync(f)) { console.log(`  ${s.padEnd(11)} no data yet`); continue; }
      const rows = readFileSync(f, 'utf8').trim().split('\n').filter(Boolean).map(l => {
        try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
      total += rows.length;
      const w = rows.map(r => r.window).filter(Boolean).sort();
      const kb = (statSync(f).size / 1024).toFixed(0);
      console.log(`  ${s.padEnd(11)} ${String(rows.length).padStart(5)} windows  ${w[0]} -> ${w[w.length-1]}  ${kb} KB`);
    }
    const used = dataBytes();
    const pctv = used / MAX_BYTES * 100;
    console.log(`\n  ${total} windows total (~${(total/96).toFixed(1)} series-days)`);
    console.log(`  storage ${fmtBytes(used)} of ${fmtBytes(MAX_BYTES)} ceiling (${pctv.toFixed(3)}%)` +
                (pctv >= 100 ? '  — HALTED' : pctv >= WARN_AT * 100 ? '  — approaching' : ''));
    if (total > 0 && used > 0) {
      const perDay = (used / total) * 96 * SERIES.length;
      const daysLeft = (MAX_BYTES - used) / perDay;
      console.log(`  growing ~${fmtBytes(perDay)}/day -> ceiling in ~${(daysLeft/365).toFixed(0)} years`);
    }
    return;
  }

  // Check the ceiling before any capture. Refusing here rather than mid-run
  // keeps every file a complete set of rows.
  const used = dataBytes();
  if (used >= MAX_BYTES) {
    log(`STOPPED: data/ is ${fmtBytes(used)}, at or past the ${fmtBytes(MAX_BYTES)} ceiling. ` +
        `Recording is HALTED and these windows cannot be backfilled later. ` +
        `Archive or prune data/, or raise KLAB_MAX_BYTES.`);
    process.exitCode = 1;          // surfaces as a failing agent in launchctl list
    return;
  }
  if (used >= MAX_BYTES * WARN_AT) {
    log(`WARN: data/ is ${fmtBytes(used)} — ${(used / MAX_BYTES * 100).toFixed(0)}% of the ` +
        `${fmtBytes(MAX_BYTES)} ceiling. Capture stops at 100%.`);
  }

  const only = argVal('--series');
  const list = only ? [only] : SERIES;
  let added = 0, skipped = 0, failed = 0;
  for (const s of list) {
    const r = await captureSeries(s);
    added += r.added; skipped += r.skipped; failed += r.failed;
    if (r.added) log(`${s}: +${r.added} window(s)`);
  }
  log(`recorded ${added} new, ${skipped} already had, ${failed} failed`);
  await persist(added);
  if (failed && !added) process.exitCode = 1;
}

export { minuteQuotes, quotesFromCandles, acquireLock, releaseLock, fileFor, SERIES, log };

// Run only when executed directly, so backfill-minute-quotes.js can import the helpers.
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const READ_ONLY = args.has('--status');
  if (!READ_ONLY && !acquireLock()) { log('another run in progress — skipping'); process.exit(0); }
  main()
    .catch(e => { log(`FATAL ${e.stack || e.message}`); process.exitCode = 1; })
    .finally(() => { if (!READ_ONLY) releaseLock(); });
}
