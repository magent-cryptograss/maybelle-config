/**
 * Wiki Sync Module - Synchronizes releases from PickiPedia to local IPFS pins
 *
 * Queries the Release namespace via the releaselist API and pins any CIDs
 * that aren't already pinned locally.
 */

import fetch from 'node-fetch';

const WIKI_URL = process.env.WIKI_URL || 'https://pickipedia.xyz';
const IPFS_API_URL = process.env.IPFS_API_URL || 'http://ipfs:5001';

// In-memory sync state
let lastSyncTime = null;
let lastSyncResult = null;
let syncInProgress = false;

/**
 * Fetch all releases from the wiki API
 * @param {string} filter - 'all', 'ipfs', 'torrent', 'missing-torrent'
 * @returns {Promise<Array>}
 */
export async function getAllReleases(filter = 'all') {
  const url = `${WIKI_URL}/api.php?action=releaselist&filter=${filter}&format=json`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Wiki API error: ${response.status} ${response.statusText}`);
  }

  const data = await response.json();
  return data.releases || [];
}

/**
 * Get all locally pinned CIDs
 * @returns {Promise<Set<string>>}
 */
async function getLocalPins() {
  const response = await fetch(`${IPFS_API_URL}/api/v0/pin/ls?type=recursive`, {
    method: 'POST'
  });

  if (!response.ok) {
    throw new Error(`IPFS API error: ${response.status}`);
  }

  const result = await response.json();
  return new Set(Object.keys(result.Keys || {}));
}

/**
 * Pin a CID to the local IPFS node
 * @param {string} cid - The CID to pin
 * @param {string} name - Optional name for logging
 * @returns {Promise<boolean>}
 */
async function pinCid(cid, name = null) {
  console.log(`[wiki-sync] Pinning CID: ${cid}${name ? ` (${name})` : ''}`);

  try {
    const response = await fetch(`${IPFS_API_URL}/api/v0/pin/add?arg=${cid}&progress=false`, {
      method: 'POST'
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`[wiki-sync] Failed to pin ${cid}: ${response.status} - ${errorText}`);
      return false;
    }

    const result = await response.json();
    console.log(`[wiki-sync] Successfully pinned: ${cid}`);
    return true;
  } catch (error) {
    console.error(`[wiki-sync] Error pinning ${cid}: ${error.message}`);
    return false;
  }
}

/**
 * Sync releases from the wiki - pin any missing CIDs
 * @returns {Promise<Object>} Sync result with counts
 */
export async function syncReleases() {
  if (syncInProgress) {
    return {
      status: 'already-running',
      message: 'Sync already in progress',
      lastSync: lastSyncResult
    };
  }

  syncInProgress = true;
  const startTime = new Date();

  console.log('[wiki-sync] Starting release sync...');

  try {
    // Fetch releases and local pins in parallel
    const [releases, localPins] = await Promise.all([
      getAllReleases('ipfs'),
      getLocalPins()
    ]);

    console.log(`[wiki-sync] Found ${releases.length} releases, ${localPins.size} local pins`);

    const results = {
      totalReleases: releases.length,
      totalLocalPins: localPins.size,
      alreadyPinned: 0,
      newlyPinned: 0,
      failedToPPin: 0,
      skipped: 0,
      details: []
    };

    for (const release of releases) {
      const cid = release.ipfs_cid;

      if (!cid) {
        results.skipped++;
        continue;
      }

      if (localPins.has(cid)) {
        results.alreadyPinned++;
        continue;
      }

      // Pin the missing CID
      const success = await pinCid(cid, release.title);

      if (success) {
        results.newlyPinned++;
        results.details.push({
          action: 'pinned',
          cid,
          title: release.title,
          pageTitle: release.page_title
        });
      } else {
        results.failedToPPin++;
        results.details.push({
          action: 'failed',
          cid,
          title: release.title,
          pageTitle: release.page_title
        });
      }
    }

    const duration = (new Date() - startTime) / 1000;
    results.duration = duration;
    results.status = 'completed';
    results.syncedAt = startTime.toISOString();

    console.log(`[wiki-sync] Sync completed in ${duration.toFixed(1)}s: ` +
      `${results.newlyPinned} pinned, ${results.alreadyPinned} already present, ` +
      `${results.failedToPPin} failed, ${results.skipped} skipped`);

    lastSyncTime = startTime;
    lastSyncResult = results;

    return results;

  } catch (error) {
    console.error('[wiki-sync] Sync failed:', error.message);

    const errorResult = {
      status: 'error',
      error: error.message,
      syncedAt: startTime.toISOString()
    };

    lastSyncResult = errorResult;
    return errorResult;

  } finally {
    syncInProgress = false;
  }
}

/**
 * Get the current sync status
 * @returns {Object}
 */
export function getSyncStatus() {
  return {
    inProgress: syncInProgress,
    lastSync: lastSyncTime?.toISOString() || null,
    lastResult: lastSyncResult
  };
}

/**
 * Get releases that need torrent seeding
 * (have IPFS but missing BitTorrent infohash or not seeding)
 * @param {Set<string>} activeTorrents - Set of active torrent infohashes
 * @returns {Promise<Array>}
 */
export async function getReleasesNeedingSeeding(activeTorrents = new Set()) {
  const releases = await getAllReleases('all');

  return releases.filter(release => {
    // Has IPFS CID
    if (!release.ipfs_cid) return false;

    // Has torrent infohash but not seeding
    if (release.bittorrent_infohash) {
      return !activeTorrents.has(release.bittorrent_infohash.toLowerCase());
    }

    // No torrent infohash - might need one created
    return false;
  });
}
