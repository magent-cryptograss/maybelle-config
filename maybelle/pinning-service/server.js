import express from 'express';
import cors from 'cors';
import multer from 'multer';
import { execSync, spawn } from 'child_process';
import { createReadStream, readFileSync, statSync, unlinkSync, existsSync } from 'fs';
import { join } from 'path';
import fetch from 'node-fetch';
import FormData from 'form-data';
import Hash from 'ipfs-only-hash';
import { CID } from 'multiformats/cid';
import { requireWalletAuth } from './auth.js';

const app = express();

// CORS configuration - allow requests from cryptograss domains
const allowedOrigins = [
  'https://cryptograss.live',
  'https://www.cryptograss.live',
  /\.hunter\.cryptograss\.live$/,  // All hunter dev subdomains
  /localhost:\d+$/,
];

app.use(cors({
  origin: function(origin, callback) {
    // Allow requests with no origin (curl, server-to-server)
    if (!origin) return callback(null, true);

    // Check if origin matches any allowed pattern
    const isAllowed = allowedOrigins.some(allowed => {
      if (allowed instanceof RegExp) {
        return allowed.test(origin);
      }
      return origin === allowed;
    });

    if (isAllowed) {
      callback(null, true);
    } else {
      console.log(`CORS blocked origin: ${origin}`);
      callback(new Error('Not allowed by CORS'));
    }
  },
  credentials: true
}));

app.use(express.json());

const PORT = process.env.PORT || 3001;
const PINATA_JWT = process.env.PINATA_JWT;
// Legacy keys kept for backwards compatibility during transition
const PINATA_API_KEY = process.env.PINATA_API_KEY;
const PINATA_SECRET_KEY = process.env.PINATA_SECRET_KEY;
const IPFS_API_URL = process.env.IPFS_API_URL || 'http://ipfs:5001';
const STAGING_DIR = process.env.STAGING_DIR || '/staging';
const AUTHORIZED_WALLETS = process.env.AUTHORIZED_WALLETS || '';

// File upload handling
const upload = multer({
  dest: STAGING_DIR,
  limits: { fileSize: 500 * 1024 * 1024 } // 500MB max
});

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// Download video from URL (Instagram, YouTube, etc.) and pin to IPFS
app.post('/pin-from-url', requireWalletAuth, async (req, res) => {
  const { url } = req.body;

  if (!url) {
    return res.status(400).json({ error: 'URL is required' });
  }

  const tempFile = join(STAGING_DIR, `download-${Date.now()}`);

  try {
    console.log(`Downloading from: ${url}`);

    // Use yt-dlp to download the video
    // Output template ensures we get a predictable filename
    const outputTemplate = `${tempFile}.%(ext)s`;

    execSync(`yt-dlp -o "${outputTemplate}" --no-playlist "${url}"`, {
      stdio: 'pipe',
      timeout: 300000 // 5 minute timeout
    });

    // Find the downloaded file (yt-dlp adds extension)
    const files = execSync(`ls ${tempFile}.*`).toString().trim().split('\n');
    if (files.length === 0 || !files[0]) {
      throw new Error('Download completed but file not found');
    }

    const downloadedFile = files[0];
    const filename = downloadedFile.split('/').pop();

    console.log(`Downloaded: ${filename}`);

    // Pin to Pinata and local IPFS
    const result = await pinFile(downloadedFile, filename);

    // Cleanup
    try { unlinkSync(downloadedFile); } catch (e) { /* ignore */ }

    res.json(result);

  } catch (error) {
    console.error('Error processing URL:', error.message);
    // Cleanup any partial downloads
    try {
      execSync(`rm -f ${tempFile}.*`);
    } catch (e) { /* ignore */ }

    res.status(500).json({
      error: 'Failed to download or pin video',
      details: error.message
    });
  }
});

// Upload file directly and pin to IPFS
app.post('/pin-file', requireWalletAuth, upload.single('file'), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ error: 'No file uploaded' });
  }

  try {
    const result = await pinFile(req.file.path, req.file.originalname);

    // Cleanup
    try { unlinkSync(req.file.path); } catch (e) { /* ignore */ }

    res.json(result);

  } catch (error) {
    console.error('Error pinning file:', error.message);
    try { unlinkSync(req.file.path); } catch (e) { /* ignore */ }

    res.status(500).json({
      error: 'Failed to pin file',
      details: error.message
    });
  }
});

// Pin an existing CID to local IPFS node (for redundancy)
app.post('/pin-cid', requireWalletAuth, async (req, res) => {
  const { cid } = req.body;

  if (!cid) {
    return res.status(400).json({ error: 'CID is required' });
  }

  try {
    await pinToLocalIPFS(cid);
    res.json({ success: true, cid, locallyPinned: true });
  } catch (error) {
    console.error('Error pinning CID locally:', error.message);
    res.status(500).json({
      error: 'Failed to pin CID locally',
      details: error.message
    });
  }
});

// Convert CIDv0 (Qm...) to CIDv1 (bafy...) for comparison
function cidToV1(cidString) {
  try {
    const cid = CID.parse(cidString);
    if (cid.version === 0) {
      // Convert to CIDv1 with base32 encoding (default for v1)
      return cid.toV1().toString();
    }
    return cidString;
  } catch (e) {
    console.warn(`CID conversion error: ${e.message}`);
    return cidString;
  }
}

// Check if a CID is already pinned on our Pinata account (fast database lookup)
// Uses v3 API which requires org:files:read scope
async function checkCidPinned(cidString) {
  if (!PINATA_JWT) {
    return false;
  }

  // Convert to CIDv1 since that's what Pinata v3 API uses
  const cidV1 = cidToV1(cidString);

  try {
    // v3 API endpoint for listing files, filtered by CID
    const response = await fetch(`https://api.pinata.cloud/v3/files/public?cid=${cidV1}`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${PINATA_JWT}`
      }
    });

    if (!response.ok) {
      console.warn(`Pinata v3 files check failed: ${response.status}`);
      return false;
    }

    const result = await response.json();
    // v3 API returns { data: { files: [...] } }
    return result.data && result.data.files && result.data.files.length > 0;
  } catch (e) {
    console.warn(`Pinata v3 files check error: ${e.message}`);
    return false;
  }
}

// Main pinning function - uploads to Pinata and pins locally (idempotent)
async function pinFile(filePath, filename) {
  const stats = statSync(filePath);
  console.log(`Pinning file: ${filename} (${(stats.size / 1024 / 1024).toFixed(2)} MB)`);

  // Step 1: Compute CID from file content
  const fileBuffer = readFileSync(filePath);
  const computedCid = await Hash.of(fileBuffer);
  console.log(`Computed CID: ${computedCid}`);

  // Step 2: Check if this CID is already pinned on our Pinata account
  // Convert to v1 for consistent comparison and storage
  const cidV1 = cidToV1(computedCid);
  console.log(`CID as v1: ${cidV1}`);

  const alreadyPinnedOnPinata = await checkCidPinned(computedCid);
  if (alreadyPinnedOnPinata) {
    console.log(`CID already pinned on Pinata, skipping upload`);

    // Still ensure it's pinned locally for redundancy (use v1 CID)
    const locallyPinned = await checkLocalPinned(cidV1);
    if (!locallyPinned) {
      console.log(`Not pinned locally, starting background pin...`);
      pinToLocalIPFS(cidV1)
        .then(() => console.log(`Local pin complete: ${cidV1}`))
        .catch(error => console.warn(`Local pin failed: ${error.message}`));
    } else {
      console.log(`Already pinned locally too`);
    }

    // Return CIDv1 for consistency with Pinata
    return {
      cid: cidV1,
      ipfsUri: `ipfs://${cidV1}`,
      gatewayUrl: `https://gateway.pinata.cloud/ipfs/${cidV1}`,
      filename,
      size: stats.size,
      alreadyPinned: true
    };
  }

  // Step 3: Upload to Pinata
  const pinataCid = await uploadToPinata(filePath, filename);
  console.log(`Pinata CID: ${pinataCid}`);

  // Sanity check - computed CID should match Pinata's (compare as v1 to handle version differences)
  const computedV1 = cidToV1(computedCid);
  const pinataV1 = cidToV1(pinataCid);
  if (pinataV1 !== computedV1) {
    console.warn(`CID mismatch! Computed: ${computedCid} (v1: ${computedV1}), Pinata: ${pinataCid}`);
  }

  // Step 4: Pin to local IPFS node for redundancy (fire and forget)
  // Local pinning can take a long time for large files, so we don't block on it
  console.log(`Starting local IPFS pin (background)...`);
  pinToLocalIPFS(pinataCid)
    .then(() => console.log(`Local pin complete: ${pinataCid}`))
    .catch(error => console.warn(`Local pin failed: ${error.message}`));

  return {
    cid: pinataCid,
    ipfsUri: `ipfs://${pinataCid}`,
    gatewayUrl: `https://gateway.pinata.cloud/ipfs/${pinataCid}`,
    filename,
    size: stats.size,
    alreadyPinned: false
  };
}

// Upload to Pinata using v3 API with JWT
async function uploadToPinata(filePath, filename) {
  if (!PINATA_JWT) {
    throw new Error('Pinata JWT not configured');
  }

  const form = new FormData();
  form.append('file', createReadStream(filePath), filename);

  // v3 API defaults to private - we need public for IPFS accessibility
  form.append('network', 'public');

  // v3 API uses 'name' field for the file name in metadata
  form.append('name', filename);

  // Add keyvalues as JSON
  const keyvalues = JSON.stringify({
    source: 'blue-railroad',
    timestamp: new Date().toISOString()
  });
  form.append('keyvalues', keyvalues);

  const response = await fetch('https://uploads.pinata.cloud/v3/files', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${PINATA_JWT}`,
    },
    body: form
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Pinata upload failed: ${response.status} ${errorText}`);
  }

  const result = await response.json();
  // v3 API returns the CID in data.cid
  return result.data.cid;
}

// Check if a CID is already pinned on local IPFS node
async function checkLocalPinned(cid) {
  try {
    const response = await fetch(`${IPFS_API_URL}/api/v0/pin/ls?arg=${cid}&type=recursive`, {
      method: 'POST'
    });

    if (!response.ok) {
      // 500 error with "not pinned" message means it's not pinned
      return false;
    }

    const result = await response.json();
    // If Keys object has our CID, it's pinned
    return result.Keys && Object.keys(result.Keys).length > 0;
  } catch (e) {
    return false;
  }
}

// Pin CID to local IPFS node with progress logging
async function pinToLocalIPFS(cid) {
  console.log(`Local IPFS: Starting pin for ${cid}`);
  const startTime = Date.now();

  // Use the IPFS HTTP API to pin with progress reporting
  const response = await fetch(`${IPFS_API_URL}/api/v0/pin/add?arg=${cid}&progress=true`, {
    method: 'POST'
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Local IPFS pin failed: ${response.status} ${errorText}`);
  }

  // With progress=true, IPFS streams newline-delimited JSON progress updates
  const text = await response.text();
  const lines = text.trim().split('\n');

  for (const line of lines) {
    try {
      const progress = JSON.parse(line);
      if (progress.Progress) {
        console.log(`Local IPFS: ${progress.Progress}`);
      }
    } catch (e) {
      // Not JSON, just log it
      if (line.trim()) console.log(`Local IPFS: ${line}`);
    }
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  console.log(`Local IPFS: Pin complete in ${elapsed}s`);

  // Return the last line which should be the final result
  return JSON.parse(lines[lines.length - 1]);
}

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Blue Railroad Pinning Service listening on port ${PORT}`);
  console.log(`Pinata configured: ${PINATA_JWT ? 'yes (JWT)' : 'NO - uploads will fail'}`);
  console.log(`IPFS API URL: ${IPFS_API_URL}`);
  const walletCount = AUTHORIZED_WALLETS.split(',').filter(w => w.trim()).length;
  console.log(`Wallet auth: ${walletCount} authorized wallet(s)`);
});
