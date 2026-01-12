import express from 'express';
import multer from 'multer';
import { execSync, spawn } from 'child_process';
import { createReadStream, statSync, unlinkSync, existsSync } from 'fs';
import { join } from 'path';
import fetch from 'node-fetch';
import FormData from 'form-data';
import { requireWalletAuth } from './auth.js';

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3001;
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

// Main pinning function - uploads to Pinata and pins locally
async function pinFile(filePath, filename) {
  const stats = statSync(filePath);
  console.log(`Pinning file: ${filename} (${(stats.size / 1024 / 1024).toFixed(2)} MB)`);

  // Step 1: Upload to Pinata
  const pinataCid = await uploadToPinata(filePath, filename);
  console.log(`Pinata CID: ${pinataCid}`);

  // Step 2: Pin to local IPFS node for redundancy
  let locallyPinned = false;
  try {
    await pinToLocalIPFS(pinataCid);
    locallyPinned = true;
    console.log(`Locally pinned: ${pinataCid}`);
  } catch (error) {
    console.warn(`Warning: Failed to pin locally: ${error.message}`);
    // Don't fail the whole request if local pinning fails
  }

  return {
    cid: pinataCid,
    ipfsUri: `ipfs://${pinataCid}`,
    gatewayUrl: `https://gateway.pinata.cloud/ipfs/${pinataCid}`,
    filename,
    size: stats.size,
    locallyPinned
  };
}

// Upload to Pinata
async function uploadToPinata(filePath, filename) {
  if (!PINATA_API_KEY || !PINATA_SECRET_KEY) {
    throw new Error('Pinata API credentials not configured');
  }

  const form = new FormData();
  form.append('file', createReadStream(filePath), filename);

  // Add metadata
  const metadata = JSON.stringify({
    name: filename,
    keyvalues: {
      source: 'blue-railroad',
      timestamp: new Date().toISOString()
    }
  });
  form.append('pinataMetadata', metadata);

  const response = await fetch('https://api.pinata.cloud/pinning/pinFileToIPFS', {
    method: 'POST',
    headers: {
      'pinata_api_key': PINATA_API_KEY,
      'pinata_secret_api_key': PINATA_SECRET_KEY,
    },
    body: form
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Pinata upload failed: ${response.status} ${errorText}`);
  }

  const result = await response.json();
  return result.IpfsHash;
}

// Pin CID to local IPFS node
async function pinToLocalIPFS(cid) {
  // Use the IPFS HTTP API to pin
  const response = await fetch(`${IPFS_API_URL}/api/v0/pin/add?arg=${cid}`, {
    method: 'POST'
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Local IPFS pin failed: ${response.status} ${errorText}`);
  }

  return await response.json();
}

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Blue Railroad Pinning Service listening on port ${PORT}`);
  console.log(`Pinata configured: ${PINATA_API_KEY ? 'yes' : 'NO - uploads will fail'}`);
  console.log(`IPFS API URL: ${IPFS_API_URL}`);
  const walletCount = AUTHORIZED_WALLETS.split(',').filter(w => w.trim()).length;
  console.log(`Wallet auth: ${walletCount} authorized wallet(s)`);
});
