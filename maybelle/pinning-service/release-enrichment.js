/**
 * Release Enrichment - Updates sparse Release pages on PickiPedia with metadata
 * from Pinata (names, sizes, dates) and delivery-kid (pin status).
 *
 * Runs on a schedule alongside wiki-sync. For each Release page that has
 * incomplete metadata, fetches what we know from pinning services and updates
 * the Release page YAML.
 */

import fetch from 'node-fetch';
import fs from 'fs';
import os from 'os';
import path from 'path';
import YAML from 'js-yaml';
import { getAllReleases } from './wiki-sync.js';
import { createTorrent, DEFAULT_TRACKERS } from './torrent.js';

const WIKI_URL = process.env.WIKI_URL || 'https://pickipedia.xyz';
const WIKI_BOT_USER = process.env.WIKI_BOT_USER;
const WIKI_BOT_PASSWORD = process.env.WIKI_BOT_PASSWORD;
const PINATA_JWT = process.env.PINATA_JWT;
const IPFS_API_URL = process.env.IPFS_API_URL || 'http://ipfs:5001';
const NODE_NAME = process.env.NODE_NAME || 'delivery-kid';

// Reuse login session
let cookies = '';
let editToken = null;

async function getToken(type) {
  const url = `${WIKI_URL}/api.php?action=query&meta=tokens&type=${type}&format=json`;
  const response = await fetch(url, {
    headers: { 'Cookie': cookies }
  });
  const setCookies = response.headers.raw()['set-cookie'];
  if (setCookies) {
    cookies = setCookies.map(c => c.split(';')[0]).join('; ');
  }
  const data = await response.json();
  return data.query.tokens[`${type}token`];
}

async function login() {
  if (!WIKI_BOT_USER || !WIKI_BOT_PASSWORD) {
    throw new Error('Wiki bot credentials not configured');
  }
  const loginToken = await getToken('login');
  const url = `${WIKI_URL}/api.php`;
  const params = new URLSearchParams({
    action: 'login',
    lgname: WIKI_BOT_USER,
    lgpassword: WIKI_BOT_PASSWORD,
    lgtoken: loginToken,
    format: 'json'
  });
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': cookies },
    body: params.toString()
  });
  const setCookies = response.headers.raw()['set-cookie'];
  if (setCookies) {
    cookies = setCookies.map(c => c.split(';')[0]).join('; ');
  }
  const data = await response.json();
  if (data.login?.result !== 'Success') {
    throw new Error(`Wiki login failed: ${data.login?.reason || JSON.stringify(data)}`);
  }
  console.log(`[enrich] Logged in as ${WIKI_BOT_USER}`);
  editToken = await getToken('csrf');
}

/**
 * Fetch all pins from Pinata with metadata (name, size, date).
 */
async function fetchPinataPins() {
  if (!PINATA_JWT) {
    console.log('[enrich] Pinata JWT not configured, skipping Pinata metadata');
    return new Map();
  }

  const pinMap = new Map();
  let offset = 0;
  const limit = 100;

  while (true) {
    const url = `https://api.pinata.cloud/data/pinList?status=pinned&pageLimit=${limit}&pageOffset=${offset}`;
    const response = await fetch(url, {
      headers: { 'Authorization': `Bearer ${PINATA_JWT}` }
    });

    if (!response.ok) {
      console.error(`[enrich] Pinata API error: ${response.status}`);
      break;
    }

    const data = await response.json();
    const rows = data.rows || [];

    for (const pin of rows) {
      pinMap.set(pin.ipfs_pin_hash, {
        name: pin.metadata?.name || null,
        size: pin.size || null,
        date_pinned: pin.date_pinned || null,
        keyvalues: pin.metadata?.keyvalues || {},
      });
    }

    if (rows.length < limit) break;
    offset += limit;
  }

  console.log(`[enrich] Fetched ${pinMap.size} pins from Pinata`);
  return pinMap;
}

/**
 * Fetch locally pinned CIDs from IPFS node.
 */
async function fetchLocalPins() {
  try {
    const response = await fetch(`${IPFS_API_URL}/api/v0/pin/ls?type=recursive`, {
      method: 'POST'
    });
    if (!response.ok) return new Set();
    const result = await response.json();
    const pins = new Set(Object.keys(result.Keys || {}));
    console.log(`[enrich] Found ${pins.size} local pins`);
    return pins;
  } catch (error) {
    console.error(`[enrich] Failed to fetch local pins: ${error.message}`);
    return new Set();
  }
}

/**
 * Get the current YAML content of a Release page via the action API.
 * Returns null if page doesn't exist or content can't be read.
 */
async function getReleaseContent(pageTitle) {
  const url = `${WIKI_URL}/api.php?action=query&titles=${encodeURIComponent(`Release:${pageTitle}`)}&prop=revisions&rvprop=content|contentmodel&rvslots=main&format=json`;
  const response = await fetch(url, {
    headers: { 'Cookie': cookies }
  });
  const data = await response.json();
  const pages = data.query.pages;
  const pageId = Object.keys(pages)[0];
  if (pageId === '-1') return null;

  const slot = pages[pageId].revisions?.[0]?.slots?.main;
  if (!slot) return null;

  return {
    content: slot['*'],
    contentmodel: slot.contentmodel,
  };
}

/**
 * Update a Release page with enriched YAML.
 */
async function updateReleasePage(cid, yamlContent, summary) {
  if (!editToken) await login();

  const pageTitle = `Release:${cid}`;
  const params = new URLSearchParams({
    action: 'edit',
    title: pageTitle,
    text: yamlContent,
    contentmodel: 'release-yaml',
    summary,
    token: editToken,
    bot: '1',
    format: 'json'
  });

  const response = await fetch(`${WIKI_URL}/api.php`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': cookies },
    body: params.toString()
  });

  const data = await response.json();

  if (data.error) {
    if (data.error.code === 'badtoken') {
      editToken = null;
      await login();
      return updateReleasePage(cid, yamlContent, summary);
    }
    throw new Error(`Wiki edit failed for ${cid}: ${data.error.info}`);
  }

  return data.edit;
}

/**
 * Fetch a directory from IPFS to a local temp directory.
 * Uses the IPFS /api/v0/get endpoint which returns a tar archive.
 * Returns the path to the extracted directory, or null on failure.
 */
async function fetchIpfsDirectory(cid) {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'torrent-'));

  try {
    const response = await fetch(`${IPFS_API_URL}/api/v0/get?arg=${cid}&archive=true`, {
      method: 'POST',
      timeout: 300000,
    });

    if (!response.ok) {
      console.log(`[enrich] IPFS get failed for ${cid}: ${response.status}`);
      fs.rmSync(tmpDir, { recursive: true });
      return null;
    }

    // Write tar to temp file, then extract
    const tarPath = path.join(tmpDir, 'archive.tar');
    const buffer = await response.buffer();
    fs.writeFileSync(tarPath, buffer);

    // Extract using tar command (available on all unix systems)
    const { execSync } = await import('child_process');
    execSync(`tar xf ${tarPath} -C ${tmpDir}`, { stdio: 'pipe' });
    fs.unlinkSync(tarPath);

    // The tar extracts to tmpDir/{cid}/...
    const extracted = path.join(tmpDir, cid);
    if (fs.existsSync(extracted) && fs.statSync(extracted).isDirectory()) {
      return { dir: extracted, cleanup: () => fs.rmSync(tmpDir, { recursive: true }) };
    }

    // CID might be case-mangled — check for any directory
    const entries = fs.readdirSync(tmpDir);
    for (const entry of entries) {
      const full = path.join(tmpDir, entry);
      if (fs.statSync(full).isDirectory()) {
        return { dir: full, cleanup: () => fs.rmSync(tmpDir, { recursive: true }) };
      }
    }

    fs.rmSync(tmpDir, { recursive: true });
    return null;
  } catch (error) {
    console.error(`[enrich] Error fetching ${cid} from IPFS: ${error.message}`);
    try { fs.rmSync(tmpDir, { recursive: true }); } catch {}
    return null;
  }
}

/**
 * Generate a deterministic torrent for a release and return the infohash.
 * Returns { infohash, trackers } or null on failure.
 */
async function generateTorrent(cid) {
  const fetched = await fetchIpfsDirectory(cid);
  if (!fetched) return null;

  try {
    const result = createTorrent(fetched.dir, cid, {
      webseeds: [`https://ipfs.io/ipfs/${cid}/`],
    });

    if (!result.success) {
      console.error(`[enrich] Torrent generation failed for ${cid}: ${result.error}`);
      return null;
    }

    console.log(`[enrich] Generated torrent for ${cid}: infohash=${result.infohash}, ${result.fileCount} files, ${result.totalSize} bytes`);
    return {
      infohash: result.infohash,
      trackers: DEFAULT_TRACKERS,
    };
  } finally {
    fetched.cleanup();
  }
}

/**
 * Determine if a release needs enrichment.
 * Returns true if important fields are missing.
 */
function needsEnrichment(release, pinataMeta) {
  // If we have Pinata metadata that the Release page lacks, enrich it
  if (pinataMeta) {
    if (!release.title && pinataMeta.name) return true;
    if (!release.file_size && pinataMeta.size) return true;
  }
  // If pinned_on is missing but we know it's pinned somewhere
  if (!release.pinned_on) return true;
  // If missing BitTorrent metadata
  if (!release.bittorrent_infohash) return true;

  return false;
}

/**
 * Build enriched YAML for a Release page.
 * Merges existing data with new metadata, never overwrites human-set fields.
 */
function buildEnrichedYaml(existingData, pinataMeta, localPins, cid, torrentData) {
  const data = { ...existingData };

  // Title: use existing, or fall back to Pinata name
  if (!data.title && pinataMeta?.name) {
    data.title = pinataMeta.name;
  }

  // CID is always the page title, but include for completeness
  data.ipfs_cid = cid;

  // File size from Pinata
  if (!data.file_size && pinataMeta?.size) {
    data.file_size = pinataMeta.size;
  }

  // Description from Pinata keyvalues if available
  if (!data.description && pinataMeta?.keyvalues?.description) {
    data.description = pinataMeta.keyvalues.description;
  }

  // File type from Pinata keyvalues
  if (!data.file_type && pinataMeta?.keyvalues?.file_type) {
    data.file_type = pinataMeta.keyvalues.file_type;
  }

  // Build pinned_on list from what we actually know
  const pinnedOn = new Set(
    Array.isArray(data.pinned_on) ? data.pinned_on : []
  );
  if (localPins.has(cid) || localPins.has(cid.toLowerCase())) {
    pinnedOn.add(NODE_NAME);
  }
  if (pinataMeta) {
    pinnedOn.add('pinata');
  }
  if (pinnedOn.size > 0) {
    data.pinned_on = Array.from(pinnedOn).sort();
  }

  // BitTorrent metadata
  if (torrentData && !data.bittorrent_infohash) {
    data.bittorrent_infohash = torrentData.infohash;
    if (torrentData.trackers && torrentData.trackers.length > 0) {
      data.bittorrent_trackers = torrentData.trackers;
    }
  }

  return YAML.dump(data, { lineWidth: -1 });
}

/**
 * Main enrichment loop.
 * Fetches all data sources, compares with Release pages, updates sparse ones.
 */
export async function enrichReleases() {
  console.log('[enrich] Starting release enrichment...');
  const startTime = Date.now();

  // Login first
  if (!editToken) await login();

  // Fetch all data sources in parallel
  const [releases, pinataPins, localPins] = await Promise.all([
    getAllReleases('all'),
    fetchPinataPins(),
    fetchLocalPins(),
  ]);

  console.log(`[enrich] ${releases.length} releases, ${pinataPins.size} Pinata pins, ${localPins.size} local pins`);

  const results = {
    checked: 0,
    enriched: 0,
    skipped: 0,
    errors: 0,
    details: [],
  };

  for (const release of releases) {
    results.checked++;
    const cid = release.ipfs_cid;

    // Check Pinata for this CID (try both cases for CIDv1 lowercase)
    const pinataMeta = pinataPins.get(cid) || pinataPins.get(cid.toLowerCase());

    if (!needsEnrichment(release, pinataMeta)) {
      results.skipped++;
      continue;
    }

    try {
      // Fetch current page content to see what content model it uses
      const pageData = await getReleaseContent(cid);

      let existingData = {};
      if (pageData) {
        if (pageData.contentmodel === 'release-yaml') {
          // Parse existing YAML
          try {
            existingData = YAML.load(pageData.content) || {};
          } catch (e) {
            console.warn(`[enrich] Failed to parse YAML for ${cid}: ${e.message}`);
            existingData = {};
          }
        } else {
          // Page exists but in wrong content model (e.g., wikitext with Bot_proposes)
          // We'll overwrite with proper release-yaml
          console.log(`[enrich] ${cid}: converting from ${pageData.contentmodel} to release-yaml`);
          existingData = {};
        }
      }

      // Generate torrent if missing
      let torrentData = null;
      if (!existingData.bittorrent_infohash && !release.bittorrent_infohash) {
        torrentData = await generateTorrent(cid);
      }

      const enrichedYaml = buildEnrichedYaml(existingData, pinataMeta, localPins, cid, torrentData);

      // Only update if content actually changed
      const existingYaml = pageData?.contentmodel === 'release-yaml' ? pageData.content : null;
      if (existingYaml && enrichedYaml.trim() === existingYaml.trim()) {
        results.skipped++;
        continue;
      }

      await updateReleasePage(cid, enrichedYaml, 'Enrich release metadata (automated)');
      results.enriched++;
      results.details.push({ cid, action: 'enriched' });
      console.log(`[enrich] Updated ${cid}`);

    } catch (error) {
      results.errors++;
      results.details.push({ cid, action: 'error', error: error.message });
      console.error(`[enrich] Error enriching ${cid}: ${error.message}`);
    }
  }

  const duration = ((Date.now() - startTime) / 1000).toFixed(1);
  console.log(`[enrich] Done in ${duration}s: ${results.enriched} enriched, ${results.skipped} skipped, ${results.errors} errors`);

  return results;
}

// CLI entry point
if (process.argv[1] && process.argv[1].endsWith('release-enrichment.js')) {
  enrichReleases()
    .then(results => {
      console.log(JSON.stringify(results, null, 2));
      process.exit(0);
    })
    .catch(error => {
      console.error('Fatal:', error);
      process.exit(1);
    });
}
