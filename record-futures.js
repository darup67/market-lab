#!/usr/bin/env node
/**
 * CME futures OHLCV recorder.
 *
 * Same premise as record.js and the same reason for existing: Yahoo serves only
 * about five days of 15-minute history, so the depth you have in a month is the
 * depth you started accumulating today. Nothing here can be backfilled beyond
 * that window.
 *
 *   node record-futures.js            capture new bars
 *   node record-futures.js --status   how much history is on disk
 *
 * A note on what this data is FOR. These feeds are delayed (Yahoo's futures run
 * ~10 min behind, as do the _DL feeds in flip-notifier), so this is research
 * data — volatility studies, daily-horizon work — not an intraday signal
 * source. A 15-minute forecast built on 10-minute-old prices has almost no
 * usable edge against participants sitting at the exchange.
 */

import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync,
         statSync, openSync, closeSync, writeSync, unlinkSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE     = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(HERE, 'data', 'futures');
const LOG_FILE = join(HERE, 'record-futures.log');
const LOCK_FILE= join(HERE, '.futures.lock');

// Yahoo symbol -> the contract it stands for. Chosen to mirror the five
// delayed feeds already watched in flip-notifier.
const SYMBOLS = [
  { y: 'NQ=F', name: 'Nasdaq-100',  note: 'MNQ is the micro' },
  { y: 'ES=F', name: 'S&P 500',     note: 'MES is the micro' },
  { y: 'YM=F', name: 'Dow',         note: 'MYM is the micro' },
  { y: 'GC=F', name: 'Gold',        note: 'MGC is the micro' },
  { y: 'CL=F', name: 'Crude oil',   note: 'MCL is the micro' },
];

const INTERVAL   = '15m';
const BAR_SECONDS = 15 * 60;             // must match INTERVAL
const RANGE      = '5d';              // Yahoo's ceiling for 15m data
const HTTP_TIMEOUT_MS = 20000;
const LOCK_STALE_MS   = 10 * 60 * 1000;
const LOG_MAX_BYTES   = 512 * 1024;
const MAX_BYTES  = Number(process.env.KLAB_MAX_BYTES) || 5 * 1024 ** 3;
const AUTOCOMMIT = process.env.KLAB_AUTOCOMMIT !== '0';

const args = new Set(process.argv.slice(2));
const sleep = ms => new Promise(r => setTimeout(r, ms));

function log(msg) {
  const line = `${new Date().toISOString()}  ${msg}\n`;
  process.stdout.write(line);
  try {
    if (existsSync(LOG_FILE) && statSync(LOG_FILE).size > LOG_MAX_BYTES) {
      writeFileSync(LOG_FILE, readFileSync(LOG_FILE, 'utf8').split('\n').slice(-400).join('\n'));
    }
    appendFileSync(LOG_FILE, line);
  } catch {}
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
        try { process.kill(pid, 0); return false; } catch {}
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

const fileFor = y => join(DATA_DIR, `${y.replace('=', '_')}.jsonl`);

/** Bar timestamps already on disk — the dedupe key, so a re-run is a no-op. */
function recorded(y) {
  const f = fileFor(y);
  if (!existsSync(f)) return new Set();
  const seen = new Set();
  for (const line of readFileSync(f, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    try { seen.add(JSON.parse(line).t); } catch {}
  }
  return seen;
}

async function fetchBars(y) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(y)}` +
              `?interval=${INTERVAL}&range=${RANGE}`;
  let lastErr;
  for (let attempt = 0; attempt <= 3; attempt++) {
    try {
      const res = await fetch(url, {
        signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
        // Yahoo refuses the default fetch agent.
        headers: { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      const r = d?.chart?.result?.[0];
      if (!r?.timestamp) throw new Error('no timestamps in response');
      const q = r.indicators?.quote?.[0] || {};
      const out = [];
      for (let i = 0; i < r.timestamp.length; i++) {
        const o = q.open?.[i], h = q.high?.[i], l = q.low?.[i], c = q.close?.[i], v = q.volume?.[i];
        // Yahoo pads the series with nulls for bars that never traded; keeping
        // them would put fake flat bars into a volatility study.
        if ([o, h, l, c].some(x => x == null)) continue;
        // The response ends with a live-quote pseudo-bar stamped with the quote
        // time (e.g. 12:32:35), o=h=l=c, v=0. It is not a bar: until 2026-09-18
        // one was stored per run (272 per symbol), adding fake zero-range bars.
        if (r.timestamp[i] % BAR_SECONDS !== 0) continue;
        out.push({ t: r.timestamp[i], o, h, l, c, v: v ?? null });
      }
      return out;
    } catch (e) {
      lastErr = e;
      if (attempt < 3) { await sleep(1200 * 2 ** attempt); continue; }
    }
  }
  throw lastErr;
}

function dataBytes() {
  let total = 0;
  for (const s of SYMBOLS) {
    const f = fileFor(s.y);
    if (existsSync(f)) { try { total += statSync(f).size; } catch {} }
  }
  return total;
}
const fmtBytes = b => b >= 1024**2 ? `${(b/1024**2).toFixed(1)} MB` : `${(b/1024).toFixed(0)} KB`;

function git(a, timeout = 45000) {
  return new Promise(res => execFile('git', a, { cwd: HERE, timeout, killSignal: 'SIGKILL' },
    (e, o, s) => res({ ok: !e, out: (o||'').trim(), err: (s||e?.message||'').trim() })));
}

async function persist(added) {
  if (!AUTOCOMMIT) return;
  if (added) {
    const stt = await git(['status', '--porcelain', 'data/futures']);
    if (stt.ok && stt.out) {
      await git(['add', 'data/futures']);
      const c = await git(['-c','user.name=market-recorder','-c','user.email=recorder@localhost',
                           'commit','-q','-m',`futures: +${added} bar(s)`]);
      if (!c.ok) { log(`autocommit: commit failed — ${c.err}`); return; }
    }
  }
  const ahead = await git(['rev-list','--count','@{u}..HEAD']);
  if (!ahead.ok || !Number(ahead.out || 0)) return;
  const push = await git(['push','-q','origin','HEAD']);
  log(push.ok ? `autocommit: pushed ${ahead.out} commit(s)`
              : `autocommit: push failed (retries next run) — ${push.err.slice(0,140)}`);
}

async function main() {
  mkdirSync(DATA_DIR, { recursive: true });

  if (args.has('--status')) {
    let total = 0;
    for (const s of SYMBOLS) {
      const f = fileFor(s.y);
      if (!existsSync(f)) { console.log(`  ${s.y.padEnd(6)} ${s.name.padEnd(11)} no data yet`); continue; }
      const rows = readFileSync(f,'utf8').trim().split('\n').filter(Boolean)
        .map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
      total += rows.length;
      const ts = rows.map(r => r.t).sort((a,b)=>a-b);
      const span = (ts[ts.length-1]-ts[0])/86400;
      console.log(`  ${s.y.padEnd(6)} ${s.name.padEnd(11)} ${String(rows.length).padStart(5)} bars  ` +
                  `${span.toFixed(1)}d  last ${new Date(ts[ts.length-1]*1000).toISOString().slice(5,16)}Z`);
    }
    const used = dataBytes();
    console.log(`\n  ${total} bars, ${fmtBytes(used)} (${(used/MAX_BYTES*100).toFixed(3)}% of ceiling)`);
    return;
  }

  const used = dataBytes();
  if (used >= MAX_BYTES) {
    log(`STOPPED: futures data is ${fmtBytes(used)}, at the ${fmtBytes(MAX_BYTES)} ceiling. ` +
        `Capture is HALTED and these bars cannot be backfilled later.`);
    process.exitCode = 1;
    return;
  }

  let added = 0, failed = 0;
  for (const s of SYMBOLS) {
    let bars;
    try { bars = await fetchBars(s.y); }
    catch (e) { log(`${s.y}: fetch failed — ${e.message}`); failed++; continue; }
    const seen = recorded(s.y);
    const fresh = bars.filter(b => !seen.has(b.t));
    if (!fresh.length) continue;
    try {
      appendFileSync(fileFor(s.y),
        fresh.map(b => JSON.stringify({ sym: s.y, name: s.name, ...b })).join('\n') + '\n');
      added += fresh.length;
      log(`${s.y}: +${fresh.length} bar(s)`);
    } catch (e) { log(`${s.y}: write failed — ${e.message}`); failed++; }
    await sleep(250);                       // be polite to Yahoo
  }
  log(`futures: recorded ${added} new bar(s), ${failed} failed`);
  await persist(added);
  if (failed && !added) process.exitCode = 1;
}

const READ_ONLY = args.has('--status');
if (!READ_ONLY && !acquireLock()) { log('another run in progress — skipping'); process.exit(0); }
main().catch(e => { log(`FATAL ${e.stack || e.message}`); process.exitCode = 1; })
      .finally(() => { if (!READ_ONLY) releaseLock(); });
