/**
 * Deterministic BitTorrent .torrent file generation.
 *
 * Creates torrent files from directories with deterministic infohashes:
 * - Torrent name = IPFS CID (links the two systems)
 * - Piece length = deterministic function of total file size
 * - Files sorted alphabetically by path
 * - No non-deterministic fields in the info dict
 *
 * Given the same files (fetchable by CID from IPFS), the same infohash
 * is always produced.
 */

import crypto from 'crypto';
import fs from 'fs';
import path from 'path';

// Default public trackers
export const DEFAULT_TRACKERS = [
  'udp://tracker.opentrackr.org:1337/announce',
  'udp://tracker.openbittorrent.com:6969/announce',
  'udp://open.stealth.si:80/announce',
  'udp://exodus.desync.com:6969/announce',
];

// --- Bencode ---

function bencode(obj) {
  if (typeof obj === 'number' || typeof obj === 'bigint') {
    return Buffer.from(`i${obj}e`);
  }
  if (Buffer.isBuffer(obj)) {
    return Buffer.concat([Buffer.from(`${obj.length}:`), obj]);
  }
  if (typeof obj === 'string') {
    const buf = Buffer.from(obj, 'utf-8');
    return Buffer.concat([Buffer.from(`${buf.length}:`), buf]);
  }
  if (Array.isArray(obj)) {
    const parts = [Buffer.from('l')];
    for (const item of obj) {
      parts.push(bencode(item));
    }
    parts.push(Buffer.from('e'));
    return Buffer.concat(parts);
  }
  if (typeof obj === 'object' && obj !== null) {
    // Keys must be sorted as raw bytes
    const keys = Object.keys(obj).sort();
    const parts = [Buffer.from('d')];
    for (const key of keys) {
      parts.push(bencode(key));
      parts.push(bencode(obj[key]));
    }
    parts.push(Buffer.from('e'));
    return Buffer.concat(parts);
  }
  throw new Error(`Cannot bencode type: ${typeof obj}`);
}

// --- Piece length ---

function determinsiticPieceLength(totalSize) {
  const MIN_PIECE_LENGTH = 256 * 1024;       // 256 KB
  const MAX_PIECE_LENGTH = 16 * 1024 * 1024;  // 16 MB
  const TARGET_PIECES = 1500;

  if (totalSize === 0) return MIN_PIECE_LENGTH;

  const ideal = totalSize / TARGET_PIECES;
  const power = Math.max(18, Math.ceil(Math.log2(Math.max(ideal, 1))));
  const pieceLength = 2 ** power;

  return Math.max(MIN_PIECE_LENGTH, Math.min(pieceLength, MAX_PIECE_LENGTH));
}

// --- File walking ---

function walkDirectory(dir, prefix = '') {
  const entries = [];
  const items = fs.readdirSync(dir).sort();

  for (const item of items) {
    const fullPath = path.join(dir, item);
    const relPath = prefix ? `${prefix}/${item}` : item;
    const stat = fs.statSync(fullPath);

    if (stat.isDirectory()) {
      entries.push(...walkDirectory(fullPath, relPath));
    } else if (stat.isFile()) {
      entries.push({
        path: relPath,
        size: stat.size,
        fullPath,
      });
    }
  }
  return entries;
}

// --- Main ---

/**
 * Create a .torrent from a directory with deterministic infohash.
 *
 * @param {string} directory - Path to directory to torrent
 * @param {string} name - Torrent name (use CID for determinism)
 * @param {Object} [options]
 * @param {string} [options.outputPath] - Write .torrent file here
 * @param {string[]} [options.trackers] - Tracker announce URLs (doesn't affect infohash)
 * @param {string[]} [options.webseeds] - Webseed URLs (doesn't affect infohash)
 * @param {string} [options.comment] - Comment (doesn't affect infohash)
 * @returns {{ success: boolean, infohash?: string, torrentBytes?: Buffer, pieceLength?: number, totalSize?: number, fileCount?: number, error?: string }}
 */
export function createTorrent(directory, name, options = {}) {
  const { outputPath, trackers, webseeds, comment } = options;

  if (!fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    return { success: false, error: `Not a directory: ${directory}` };
  }

  // Collect files sorted alphabetically
  const files = walkDirectory(directory);
  if (files.length === 0) {
    return { success: false, error: 'No files in directory' };
  }

  const totalSize = files.reduce((sum, f) => sum + f.size, 0);
  const pieceLength = determinsiticPieceLength(totalSize);

  // Build pieces: SHA-1 of each piece across all files concatenated
  const pieceHashes = [];
  let pieceBuffer = Buffer.alloc(0);

  for (const file of files) {
    const fd = fs.openSync(file.fullPath, 'r');
    const readBuf = Buffer.alloc(65536);
    let bytesRead;

    while ((bytesRead = fs.readSync(fd, readBuf, 0, readBuf.length, null)) > 0) {
      pieceBuffer = Buffer.concat([pieceBuffer, readBuf.subarray(0, bytesRead)]);

      while (pieceBuffer.length >= pieceLength) {
        const piece = pieceBuffer.subarray(0, pieceLength);
        pieceHashes.push(crypto.createHash('sha1').update(piece).digest());
        pieceBuffer = pieceBuffer.subarray(pieceLength);
      }
    }
    fs.closeSync(fd);
  }

  // Hash final partial piece
  if (pieceBuffer.length > 0) {
    pieceHashes.push(crypto.createHash('sha1').update(pieceBuffer).digest());
  }

  const pieces = Buffer.concat(pieceHashes);

  // Build info dict (only deterministic fields)
  const fileList = files.map(f => ({
    length: f.size,
    path: f.path.split('/'),
  }));

  const info = {
    files: fileList,
    name,
    'piece length': pieceLength,
    pieces,
  };

  // Compute infohash
  const infoBencoded = bencode(info);
  const infohash = crypto.createHash('sha1').update(infoBencoded).digest('hex');

  // Build full metainfo
  const metainfo = { info };

  const trackerList = trackers || DEFAULT_TRACKERS;
  if (trackerList.length > 0) {
    metainfo.announce = trackerList[0];
    metainfo['announce-list'] = trackerList.map(t => [t]);
  }

  if (webseeds && webseeds.length > 0) {
    metainfo['url-list'] = webseeds.length === 1 ? webseeds[0] : webseeds;
  }

  if (comment) {
    metainfo.comment = comment;
  }

  const torrentBytes = bencode(metainfo);

  // Write file if requested
  if (outputPath) {
    const dir = path.dirname(outputPath);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(outputPath, torrentBytes);
  }

  return {
    success: true,
    infohash,
    torrentBytes,
    pieceLength,
    totalSize,
    fileCount: files.length,
  };
}
