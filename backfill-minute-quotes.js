#!/usr/bin/env node
/**
 * One-off: add `minute_quotes` to rows recorded before the field existed.
 *
 * Existing bytes are never altered: each updated line is the original line with
 * `,"minute_quotes":[...]` spliced in before its closing brace, so
 * `line.slice(0, -1)` of every old row is a prefix of its new form (checked
 * before the file is replaced). Fetching runs unlocked and is cached, so it
 * survives interruption; the rewrite takes the recorder's own lock, re-reads
 * the file, and swaps it in atomically.
 *
 *   node backfill-minute-quotes.js [--dry-run] [SERIES ...]   (default: all series)
 */
import { readFileSync, writeFileSync, existsSync, renameSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { minuteQuotes, acquireLock, releaseLock, fileFor, SERIES, log } from './record.js';

const CACHE = join(homedir(), 'market-lab-backups', 'minute-quotes-cache.json');
const DRY = process.argv.includes('--dry-run');
const ONLY = process.argv.slice(2).filter(a => !a.startsWith('--'));
const TODO = ONLY.length ? SERIES.filter(s => ONLY.includes(s)) : SERIES;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const readRows = f => readFileSync(f, 'utf8').split('\n').filter(Boolean);
const cache = existsSync(CACHE) ? JSON.parse(readFileSync(CACHE, 'utf8')) : {};

// 1. fetch (no lock; cached)
for (const s of TODO) {
  const f = fileFor(s);
  if (!existsSync(f)) continue;
  const todo = new Map();
  for (const line of readRows(f)) {
    const r = JSON.parse(line);
    if (!('minute_quotes' in r) && !(r.ticker in cache)) todo.set(r.ticker, r);
  }
  let n = 0;
  for (const r of todo.values()) {
    try { cache[r.ticker] = await minuteQuotes(s, r); }
    catch (e) { console.error(`${r.ticker}: ${e.message}`); continue; }
    if (++n % 200 === 0) { writeFileSync(CACHE, JSON.stringify(cache)); console.error(`${s} ${n}/${todo.size}`); }
  }
  writeFileSync(CACHE, JSON.stringify(cache));
  console.error(`${s}: fetched ${n}/${todo.size}`);
}
if (DRY) process.exit(0);

// 2. rewrite under the recorder's lock
let locked = false;
for (let i = 0; i < 60 && !(locked = acquireLock()); i++) await sleep(5000);
if (!locked) { console.error('could not take the recorder lock in 5 min'); process.exit(1); }
try {
  for (const s of TODO) {
    const f = fileFor(s);
    if (!existsSync(f)) continue;
    const old = readRows(f);
    let added = 0, missing = 0;
    const out = old.map(line => {
      const r = JSON.parse(line);
      if ('minute_quotes' in r) return line;
      if (!(r.ticker in cache)) { missing++; return line; }
      added++;
      return line.slice(0, -1) + ',"minute_quotes":' + JSON.stringify(cache[r.ticker]) + '}';
    });
    if (out.length !== old.length) throw new Error(`${s}: row count changed`);
    old.forEach((line, i) => {
      if (out[i] !== line && !out[i].startsWith(line.slice(0, -1))) throw new Error(`${s}: row ${i} altered`);
      JSON.parse(out[i]);
    });
    writeFileSync(f + '.tmp', out.join('\n') + '\n');
    renameSync(f + '.tmp', f);
    log(`backfill: ${s} +minute_quotes on ${added} rows (${missing} not fetched)`);
    console.error(`${s}: +minute_quotes on ${added} rows, ${missing} not fetched`);
  }
} finally { releaseLock(); }
