/**
 * aria2 RPC Client - BitTorrent seeding via aria2c
 *
 * Manages torrents for Release pages from PickiPedia.
 * aria2c runs as a separate container and communicates via JSON-RPC.
 */

import fetch from 'node-fetch';

const ARIA2_RPC_URL = process.env.ARIA2_RPC_URL || 'http://aria2:6800/jsonrpc';
const ARIA2_RPC_SECRET = process.env.ARIA2_RPC_SECRET || '';
const ARIA2_DOWNLOAD_DIR = process.env.ARIA2_DOWNLOAD_DIR || '/downloads';

// Default trackers for magnet links
const DEFAULT_TRACKERS = [
  'udp://tracker.opentrackr.org:1337/announce',
  'udp://tracker.openbittorrent.com:6969/announce',
  'udp://open.stealth.si:80/announce',
  'udp://tracker.torrent.eu.org:451/announce'
];

/**
 * Make an aria2 JSON-RPC call
 * @param {string} method - RPC method name (without aria2. prefix)
 * @param {Array} params - Parameters for the method
 * @returns {Promise<any>}
 */
async function rpcCall(method, params = []) {
  // Prepend the secret token if configured
  const fullParams = ARIA2_RPC_SECRET
    ? [`token:${ARIA2_RPC_SECRET}`, ...params]
    : params;

  const response = await fetch(ARIA2_RPC_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: `pinning-service-${Date.now()}`,
      method: `aria2.${method}`,
      params: fullParams
    })
  });

  if (!response.ok) {
    throw new Error(`aria2 RPC error: ${response.status} ${response.statusText}`);
  }

  const data = await response.json();

  if (data.error) {
    throw new Error(`aria2 error: ${data.error.message} (code: ${data.error.code})`);
  }

  return data.result;
}

/**
 * Check if aria2 is available
 * @returns {Promise<boolean>}
 */
export async function isAria2Available() {
  try {
    await rpcCall('getVersion');
    return true;
  } catch (error) {
    console.warn(`[aria2] Not available: ${error.message}`);
    return false;
  }
}

/**
 * Get aria2 version info
 * @returns {Promise<Object>}
 */
export async function getVersion() {
  return rpcCall('getVersion');
}

/**
 * Get global stats (download/upload speeds, active torrents)
 * @returns {Promise<Object>}
 */
export async function getGlobalStats() {
  return rpcCall('getGlobalStat');
}

/**
 * Add a torrent by magnet link (infohash)
 * @param {string} infohash - 40-character hex infohash
 * @param {string} name - Human-readable name for the torrent
 * @param {Array<string>} trackers - Optional tracker URLs
 * @returns {Promise<string>} GID (download identifier)
 */
export async function addTorrentByInfohash(infohash, name, trackers = []) {
  // Build magnet URI
  let magnetUri = `magnet:?xt=urn:btih:${infohash}`;

  if (name) {
    magnetUri += `&dn=${encodeURIComponent(name)}`;
  }

  // Add trackers
  const allTrackers = [...(trackers.length > 0 ? trackers : DEFAULT_TRACKERS)];
  for (const tracker of allTrackers) {
    magnetUri += `&tr=${encodeURIComponent(tracker)}`;
  }

  console.log(`[aria2] Adding torrent: ${name || infohash}`);

  // Options for seeding
  const options = {
    // Seed indefinitely (ratio = 0 means no ratio limit)
    'seed-ratio': '0.0',
    // Keep seeding after completion
    'seed-time': '0',
    // Directory for downloads
    dir: ARIA2_DOWNLOAD_DIR
  };

  return rpcCall('addUri', [[magnetUri], options]);
}

/**
 * Add a torrent from a .torrent file (base64 encoded)
 * @param {string} torrentBase64 - Base64 encoded .torrent file
 * @param {string} name - Human-readable name
 * @returns {Promise<string>} GID
 */
export async function addTorrentFile(torrentBase64, name = null) {
  console.log(`[aria2] Adding torrent file: ${name || 'unknown'}`);

  const options = {
    'seed-ratio': '0.0',
    'seed-time': '0',
    dir: ARIA2_DOWNLOAD_DIR
  };

  return rpcCall('addTorrent', [torrentBase64, [], options]);
}

/**
 * Get list of active downloads/seeds
 * @returns {Promise<Array>}
 */
export async function getActiveTorrents() {
  const active = await rpcCall('tellActive');
  return active.filter(t => t.bittorrent); // Only BitTorrent, not HTTP downloads
}

/**
 * Get list of waiting downloads
 * @returns {Promise<Array>}
 */
export async function getWaitingTorrents() {
  const waiting = await rpcCall('tellWaiting', [0, 100]);
  return waiting.filter(t => t.bittorrent);
}

/**
 * Get list of stopped/completed downloads
 * @returns {Promise<Array>}
 */
export async function getStoppedTorrents() {
  const stopped = await rpcCall('tellStopped', [0, 100]);
  return stopped.filter(t => t.bittorrent);
}

/**
 * Get all torrent infohashes currently being seeded
 * @returns {Promise<Set<string>>}
 */
export async function getActiveInfohashes() {
  const infohashes = new Set();

  try {
    const [active, waiting, stopped] = await Promise.all([
      getActiveTorrents(),
      getWaitingTorrents(),
      getStoppedTorrents()
    ]);

    for (const torrent of [...active, ...waiting, ...stopped]) {
      if (torrent.infoHash) {
        infohashes.add(torrent.infoHash.toLowerCase());
      }
    }
  } catch (error) {
    console.error(`[aria2] Failed to get infohashes: ${error.message}`);
  }

  return infohashes;
}

/**
 * Get detailed info about a download
 * @param {string} gid - Download identifier
 * @returns {Promise<Object>}
 */
export async function getDownloadStatus(gid) {
  return rpcCall('tellStatus', [gid]);
}

/**
 * Remove a download
 * @param {string} gid - Download identifier
 * @returns {Promise<string>}
 */
export async function removeDownload(gid) {
  return rpcCall('remove', [gid]);
}

/**
 * Pause a download
 * @param {string} gid - Download identifier
 * @returns {Promise<string>}
 */
export async function pauseDownload(gid) {
  return rpcCall('pause', [gid]);
}

/**
 * Resume a paused download
 * @param {string} gid - Download identifier
 * @returns {Promise<string>}
 */
export async function unpauseDownload(gid) {
  return rpcCall('unpause', [gid]);
}

/**
 * Force remove a download (even if it's active)
 * @param {string} gid - Download identifier
 * @returns {Promise<string>}
 */
export async function forceRemove(gid) {
  return rpcCall('forceRemove', [gid]);
}

/**
 * Get a summary of current torrent status
 * @returns {Promise<Object>}
 */
export async function getTorrentSummary() {
  try {
    const [stats, active, waiting, stopped, version] = await Promise.all([
      getGlobalStats(),
      getActiveTorrents(),
      getWaitingTorrents(),
      getStoppedTorrents(),
      getVersion()
    ]);

    return {
      available: true,
      version: version.version,
      stats: {
        downloadSpeed: parseInt(stats.downloadSpeed) || 0,
        uploadSpeed: parseInt(stats.uploadSpeed) || 0,
        numActive: parseInt(stats.numActive) || 0,
        numWaiting: parseInt(stats.numWaiting) || 0,
        numStopped: parseInt(stats.numStopped) || 0
      },
      torrents: {
        active: active.map(t => ({
          gid: t.gid,
          name: t.bittorrent?.info?.name || 'Unknown',
          infohash: t.infoHash,
          status: t.status,
          completedLength: parseInt(t.completedLength) || 0,
          totalLength: parseInt(t.totalLength) || 0,
          uploadSpeed: parseInt(t.uploadSpeed) || 0,
          downloadSpeed: parseInt(t.downloadSpeed) || 0,
          numSeeders: parseInt(t.numSeeders) || 0,
          connections: parseInt(t.connections) || 0
        })),
        waiting: waiting.length,
        stopped: stopped.length
      }
    };
  } catch (error) {
    return {
      available: false,
      error: error.message
    };
  }
}
