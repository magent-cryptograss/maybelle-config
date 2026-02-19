import express from 'express';
import cors from 'cors';
import multer from 'multer';
import { execSync, spawn } from 'child_process';
import { createReadStream, readFileSync, statSync, unlinkSync, existsSync, writeFileSync, readdirSync, copyFileSync } from 'fs';
import { join } from 'path';
import fetch from 'node-fetch';
import FormData from 'form-data';
import Hash from 'ipfs-only-hash';
import { CID } from 'multiformats/cid';
import { requireWalletAuth } from './auth.js';
import { updateSubmissionCid, isWikiConfigured } from './wiki-update.js';

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
const IPFS_GATEWAY_URL = process.env.IPFS_GATEWAY_URL || 'https://ipfs.maybelle.cryptograss.live';
const STAGING_DIR = process.env.STAGING_DIR || '/staging';
const AUTHORIZED_WALLETS = process.env.AUTHORIZED_WALLETS || '';

// File upload handling
const upload = multer({
  dest: STAGING_DIR,
  limits: { fileSize: 500 * 1024 * 1024 } // 500MB max
});

// Separate upload handler for large videos (10GB limit)
const videoUpload = multer({
  dest: STAGING_DIR,
  limits: { fileSize: 10 * 1024 * 1024 * 1024 } // 10GB max
});

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// Server time endpoint - clients use this for auth timestamps to avoid clock drift issues
app.get('/time', (req, res) => {
  res.json({ timestamp: Date.now() });
});

// Always transcode videos to web-friendly VP9/WebM
// - VP9 is royalty-free (unlike H.264) with excellent browser support
// - Converts any codec (MJPEG, etc.) to efficient VP9
// - Downscales to max 720p (but never upscales)
// - Skips only if already a small MP4 or WebM

async function transcodeIfNeeded(inputPath, onProgress = null) {
  const stats = statSync(inputPath);
  const sizeMB = stats.size / 1024 / 1024;
  const ext = inputPath.split('.').pop().toLowerCase();

  // Skip if already a small MP4 or WebM (likely already web-optimized)
  if ((ext === 'mp4' || ext === 'webm') && stats.size <= 50 * 1024 * 1024) {
    console.log(`File is ${sizeMB.toFixed(1)}MB ${ext.toUpperCase()}, assuming already optimized`);
    if (onProgress) onProgress({ stage: 'transcode-skip', message: `File is ${sizeMB.toFixed(1)}MB ${ext.toUpperCase()}, already optimized` });
    return { path: inputPath, transcoded: false };
  }

  console.log(`File is ${sizeMB.toFixed(1)}MB ${ext.toUpperCase()}, transcoding to VP9...`);
  if (onProgress) onProgress({ stage: 'transcoding', message: `Transcoding ${sizeMB.toFixed(1)}MB video to VP9...`, progress: 30 });

  const outputPath = inputPath.replace(/\.[^.]+$/, '_transcoded.webm');

  try {
    // Transcode to VP9:
    // - scale=-2:'min(720,ih)' = downscale to 720p max, never upscale
    // - CRF 30 + -b:v 0 = constant quality mode (30 ≈ H.264 CRF 23)
    // - libopus = royalty-free audio codec, pairs well with VP9
    // - row-mt=1 = enables row-based multithreading for faster encoding
    execSync(`ffmpeg -i "${inputPath}" -vf "scale=-2:'min(720,ih)'" -c:v libvpx-vp9 -crf 30 -b:v 0 -row-mt 1 -c:a libopus -b:a 128k -y "${outputPath}"`, {
      stdio: 'pipe',
      timeout: 900000 // 15 minute timeout for VP9 (slower than H.264)
    });

    const newStats = statSync(outputPath);
    const newSizeMB = newStats.size / 1024 / 1024;
    const reduction = ((1 - newStats.size/stats.size) * 100).toFixed(0);
    console.log(`Transcoded: ${sizeMB.toFixed(1)}MB -> ${newSizeMB.toFixed(1)}MB (${reduction}% reduction)`);
    if (onProgress) onProgress({ stage: 'transcoded', message: `Transcoded: ${sizeMB.toFixed(1)}MB → ${newSizeMB.toFixed(1)}MB (${reduction}% smaller)`, progress: 50 });

    // Remove original, return transcoded path
    try { unlinkSync(inputPath); } catch (e) { /* ignore */ }

    return { path: outputPath, transcoded: true, originalSize: stats.size, newSize: newStats.size };
  } catch (error) {
    console.error('Transcoding failed:', error.message);
    if (onProgress) onProgress({ stage: 'transcode-error', message: `Transcoding failed: ${error.message}` });
    // Fall back to original file
    try { unlinkSync(outputPath); } catch (e) { /* ignore */ }
    return { path: inputPath, transcoded: false, error: error.message };
  }
}

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

// Download video from URL with SSE progress streaming
app.post('/pin-from-url-stream', requireWalletAuth, async (req, res) => {
  const { url, submissionId } = req.body;

  if (!url) {
    return res.status(400).json({ error: 'URL is required' });
  }

  // Set up SSE headers
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no'); // Disable nginx buffering

  // Helper to send SSE events
  const sendEvent = (data) => {
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  // Progress callback for internal functions
  const onProgress = (event) => {
    sendEvent(event);
  };

  const tempFile = join(STAGING_DIR, `download-${Date.now()}`);

  try {
    sendEvent({ stage: 'signing', message: 'Authorization verified', progress: 5 });

    console.log(`[stream] Downloading from: ${url}`);
    sendEvent({ stage: 'downloading', message: 'Downloading video...', progress: 10 });

    // Use yt-dlp to download the video
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
    console.log(`[stream] Downloaded: ${downloadedFile.split('/').pop()}`);
    sendEvent({ stage: 'downloaded', message: 'Video downloaded', progress: 25 });

    // Transcode if file is too large (with progress callback)
    const transcodeResult = await transcodeIfNeeded(downloadedFile, onProgress);
    const fileToPin = transcodeResult.path;
    const filename = fileToPin.split('/').pop().replace('_transcoded', '');

    // Pin to Pinata and local IPFS (with progress callback)
    const result = await pinFile(fileToPin, filename, onProgress);

    // Add transcoding info to response
    if (transcodeResult.transcoded) {
      result.transcoded = true;
      result.originalSize = transcodeResult.originalSize;
      result.transcodedSize = transcodeResult.newSize;
    }

    // Cleanup
    try { unlinkSync(fileToPin); } catch (e) { /* ignore */ }

    // Update wiki if submissionId provided (persist CID to PickiPedia)
    let wikiUpdate = null;
    if (submissionId && isWikiConfigured()) {
      try {
        sendEvent({ stage: 'wiki-update', message: 'Saving CID to PickiPedia...', progress: 95 });
        wikiUpdate = await updateSubmissionCid(submissionId, result.cid);
        console.log(`[stream] Wiki update: ${wikiUpdate.action} - ${wikiUpdate.message}`);
      } catch (wikiError) {
        console.error('[stream] Wiki update failed:', wikiError.message);
        wikiUpdate = { action: 'error', message: wikiError.message };
      }
    } else if (submissionId && !isWikiConfigured()) {
      console.log('[stream] Wiki credentials not configured, skipping wiki update');
      wikiUpdate = { action: 'skipped', message: 'Wiki credentials not configured' };
    }

    // Send final complete event
    sendEvent({
      stage: 'complete',
      cid: result.cid,
      alreadyPinned: result.alreadyPinned,
      transcoded: result.transcoded || false,
      originalSize: result.originalSize,
      transcodedSize: result.transcodedSize,
      gatewayUrl: result.gatewayUrl,
      wikiUpdate: wikiUpdate,
      progress: 100
    });

    res.end();

  } catch (error) {
    console.error('[stream] Error processing URL:', error.message);
    // Cleanup any partial downloads
    try {
      execSync(`rm -f ${tempFile}.*`);
    } catch (e) { /* ignore */ }

    sendEvent({
      stage: 'error',
      message: error.message
    });

    res.end();
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
async function pinFile(filePath, filename, onProgress = null) {
  const stats = statSync(filePath);
  console.log(`Pinning file: ${filename} (${(stats.size / 1024 / 1024).toFixed(2)} MB)`);
  if (onProgress) onProgress({ stage: 'computing-cid', message: 'Computing content hash...', progress: 55 });

  // Step 1: Compute CID from file content
  const fileBuffer = readFileSync(filePath);
  const computedCid = await Hash.of(fileBuffer);
  console.log(`Computed CID: ${computedCid}`);

  // Step 2: Check if this CID is already pinned on our Pinata account
  // Convert to v1 for consistent comparison and storage
  const cidV1 = cidToV1(computedCid);
  console.log(`CID as v1: ${cidV1}`);
  if (onProgress) onProgress({ stage: 'checking-pinata', message: 'Checking if already pinned...', progress: 60 });

  const alreadyPinnedOnPinata = await checkCidPinned(computedCid);
  if (alreadyPinnedOnPinata) {
    console.log(`CID already pinned on Pinata, skipping upload`);
    if (onProgress) onProgress({ stage: 'already-pinned', message: 'Already pinned to Pinata!', progress: 90 });

    // Still ensure it's pinned locally for redundancy (use v1 CID)
    const locallyPinned = await checkLocalPinned(cidV1);
    if (!locallyPinned) {
      console.log(`Not pinned locally, starting background pin...`);
      if (onProgress) onProgress({ stage: 'pinning-local', message: 'Pinning to local node for redundancy...', progress: 95 });
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
      gatewayUrl: `${IPFS_GATEWAY_URL}/ipfs/${cidV1}`,
      filename,
      size: stats.size,
      alreadyPinned: true
    };
  }

  // Step 3: Upload to Pinata
  if (onProgress) onProgress({ stage: 'uploading', message: 'Uploading to Pinata...', progress: 65 });
  const pinataCid = await uploadToPinata(filePath, filename);
  console.log(`Pinata CID: ${pinataCid}`);
  if (onProgress) onProgress({ stage: 'uploaded', message: 'Uploaded to Pinata', progress: 85 });

  // Sanity check - computed CID should match Pinata's (compare as v1 to handle version differences)
  const computedV1 = cidToV1(computedCid);
  const pinataV1 = cidToV1(pinataCid);
  if (pinataV1 !== computedV1) {
    console.warn(`CID mismatch! Computed: ${computedCid} (v1: ${computedV1}), Pinata: ${pinataCid}`);
  }

  // Step 4: Pin to local IPFS node for redundancy (fire and forget)
  // Local pinning can take a long time for large files, so we don't block on it
  console.log(`Starting local IPFS pin (background)...`);
  if (onProgress) onProgress({ stage: 'pinning-local', message: 'Pinning to local node...', progress: 90 });
  pinToLocalIPFS(pinataCid)
    .then(() => console.log(`Local pin complete: ${pinataCid}`))
    .catch(error => console.warn(`Local pin failed: ${error.message}`));

  return {
    cid: pinataCid,
    ipfsUri: `ipfs://${pinataCid}`,
    gatewayUrl: `${IPFS_GATEWAY_URL}/ipfs/${pinataCid}`,
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

// HLS transcoding and pinning endpoint
// Accepts video file, transcodes to multiple quality tiers, pins HLS directory
app.post('/transcode-video', requireWalletAuth, videoUpload.fields([
  { name: 'video', maxCount: 1 },
  { name: 'subtitle', maxCount: 1 }
]), async (req, res) => {
  // SSE headers for streaming progress
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  const sendProgress = (data) => {
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  const sendError = (message) => {
    sendProgress({ stage: 'error', message });
    res.end();
  };

  if (!req.files?.video?.[0]) {
    return sendError('No video file uploaded');
  }

  const videoFile = req.files.video[0];
  const subtitleFile = req.files.subtitle?.[0];

  let qualities;
  try {
    qualities = JSON.parse(req.body.qualities || '[720, 480]');
  } catch (e) {
    qualities = [720, 480];
  }
  const keepOriginal = req.body.keepOriginal === 'true';

  const hlsDir = join(STAGING_DIR, `hls-${Date.now()}`);

  try {
    sendProgress({ stage: 'starting', message: 'Starting video processing...', progress: 5 });

    // Create HLS output directory
    execSync(`mkdir -p "${hlsDir}"`);

    // Get video info
    sendProgress({ stage: 'analyzing', message: 'Analyzing video...', progress: 8 });
    let videoInfo;
    try {
      const probeResult = execSync(
        `ffprobe -v quiet -print_format json -show_streams -show_format "${videoFile.path}"`,
        { encoding: 'utf8' }
      );
      videoInfo = JSON.parse(probeResult);
    } catch (e) {
      console.warn('Could not probe video:', e.message);
      videoInfo = { streams: [] };
    }

    const videoStream = videoInfo.streams?.find(s => s.codec_type === 'video');
    const sourceHeight = videoStream?.height || 1080;
    const sourceWidth = videoStream?.width || 1920;

    // Filter qualities to not upscale
    const validQualities = qualities.filter(q => q <= sourceHeight).sort((a, b) => b - a);
    if (validQualities.length === 0) {
      validQualities.push(Math.min(...qualities)); // Use lowest requested if source is smaller
    }

    sendProgress({
      stage: 'transcoding',
      message: `Transcoding to ${validQualities.join('p, ')}p...`,
      details: `Source: ${sourceWidth}x${sourceHeight}`,
      progress: 10
    });

    // Build FFmpeg command for HLS output
    // Using H.264 for maximum compatibility
    const ffmpegArgs = ['-i', videoFile.path, '-y'];

    // Build filter complex for scaling
    if (validQualities.length > 1) {
      const splits = validQualities.map((q, i) => `[v${i}]`).join('');
      let filterComplex = `[0:v]split=${validQualities.length}${splits}`;
      validQualities.forEach((q, i) => {
        filterComplex += `;[v${i}]scale=-2:${q}[v${i}out]`;
      });
      ffmpegArgs.push('-filter_complex', filterComplex);
    }

    // Add output for each quality
    validQualities.forEach((q, i) => {
      const crf = q >= 1080 ? 20 : q >= 720 ? 23 : 26;
      const audioBitrate = q >= 720 ? '128k' : '96k';

      if (validQualities.length > 1) {
        ffmpegArgs.push('-map', `[v${i}out]`);
      } else {
        ffmpegArgs.push('-map', '0:v');
        ffmpegArgs.push('-vf', `scale=-2:${q}`);
      }
      ffmpegArgs.push('-map', '0:a?'); // ? means optional (video might not have audio)
      ffmpegArgs.push(
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', crf.toString(),
        '-c:a', 'aac',
        '-b:a', audioBitrate,
        '-f', 'hls',
        '-hls_time', '6',
        '-hls_playlist_type', 'vod',
        '-hls_segment_filename', join(hlsDir, `${q}p_%03d.ts`),
        join(hlsDir, `${q}p.m3u8`)
      );
    });

    // Run FFmpeg with progress
    console.log('FFmpeg command:', 'ffmpeg', ffmpegArgs.join(' '));

    const ffmpeg = spawn('ffmpeg', ffmpegArgs);

    let lastProgress = 10;
    ffmpeg.stderr.on('data', (data) => {
      const line = data.toString();
      // Parse FFmpeg progress
      const timeMatch = line.match(/time=(\d+):(\d+):(\d+)/);
      if (timeMatch) {
        const duration = videoInfo.format?.duration || 300;
        const currentTime = parseInt(timeMatch[1]) * 3600 + parseInt(timeMatch[2]) * 60 + parseInt(timeMatch[3]);
        const progress = Math.min(80, 10 + (currentTime / duration) * 70);
        if (progress > lastProgress + 2) {
          lastProgress = progress;
          sendProgress({
            stage: 'transcoding',
            message: `Transcoding... ${Math.round(currentTime)}s processed`,
            progress: Math.round(progress)
          });
        }
      }
    });

    await new Promise((resolve, reject) => {
      ffmpeg.on('close', (code) => {
        if (code === 0) resolve();
        else reject(new Error(`FFmpeg exited with code ${code}`));
      });
      ffmpeg.on('error', reject);
    });

    sendProgress({ stage: 'transcoded', message: 'Transcoding complete', progress: 80 });

    // Create master playlist
    sendProgress({ stage: 'packaging', message: 'Creating master playlist...', progress: 82 });

    let masterPlaylist = '#EXTM3U\n#EXT-X-VERSION:3\n';
    for (const q of validQualities) {
      const bandwidth = q >= 1080 ? 5000000 : q >= 720 ? 2500000 : 1000000;
      const width = Math.round(sourceWidth * (q / sourceHeight));
      masterPlaylist += `#EXT-X-STREAM-INF:BANDWIDTH=${bandwidth},RESOLUTION=${width}x${q}\n`;
      masterPlaylist += `${q}p.m3u8\n`;
    }

    writeFileSync(join(hlsDir, 'master.m3u8'), masterPlaylist);

    // Copy subtitle if provided
    let hasSubtitles = false;
    if (subtitleFile) {
      copyFileSync(subtitleFile.path, join(hlsDir, 'subtitles.vtt'));
      hasSubtitles = true;
      sendProgress({ stage: 'subtitles', message: 'Added subtitles', progress: 84 });
    }

    // Pin original if requested
    let originalCid = null;
    if (keepOriginal) {
      sendProgress({ stage: 'pinning-original', message: 'Pinning original file...', progress: 85 });
      try {
        const origResult = await pinFile(videoFile.path, videoFile.originalname);
        originalCid = origResult.cid;
      } catch (e) {
        console.warn('Failed to pin original:', e.message);
      }
    }

    // Pin HLS directory to IPFS
    sendProgress({ stage: 'pinning', message: 'Pinning HLS directory to IPFS...', progress: 88 });

    // Add directory to local IPFS node using multipart form
    // We need to add each file with its path relative to the directory
    const files = readdirSync(hlsDir);
    const form = new FormData();

    for (const filename of files) {
      const filePath = join(hlsDir, filename);
      form.append('file', createReadStream(filePath), {
        filepath: filename  // This sets the path in the directory
      });
    }

    const addResponse = await fetch(`${IPFS_API_URL}/api/v0/add?recursive=true&wrap-with-directory=true&pin=true`, {
      method: 'POST',
      body: form
    });

    if (!addResponse.ok) {
      throw new Error(`IPFS add failed: ${addResponse.status}`);
    }

    const addResult = await addResponse.text();

    // Parse the directory add result (returns multiple lines, last one is the root wrapper)
    const addLines = addResult.trim().split('\n');
    let rootCid = null;
    let totalSize = 0;

    for (const line of addLines) {
      try {
        const item = JSON.parse(line);
        totalSize += parseInt(item.Size || 0);
        // The last entry is the wrapper directory
        rootCid = item.Hash;
      } catch (e) {
        continue;
      }
    }

    if (!rootCid) {
      throw new Error('Could not determine root CID from IPFS add');
    }

    sendProgress({ stage: 'pinning-pinata', message: 'Pinning to Pinata...', progress: 92 });

    // Pin the root CID to Pinata for persistence
    // Note: Pinata will fetch from our local node
    try {
      const pinataResponse = await fetch('https://api.pinata.cloud/pinning/pinByHash', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${PINATA_JWT}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          hashToPin: rootCid,
          pinataMetadata: {
            name: `hls-${videoFile.originalname}`,
            keyvalues: {
              source: 'blue-railroad-video',
              qualities: validQualities.join(','),
              timestamp: new Date().toISOString()
            }
          }
        })
      });

      if (!pinataResponse.ok) {
        console.warn('Pinata pin by hash failed:', await pinataResponse.text());
      }
    } catch (e) {
      console.warn('Pinata pinning failed:', e.message);
    }

    sendProgress({ stage: 'cleanup', message: 'Cleaning up...', progress: 98 });

    // Cleanup
    try {
      execSync(`rm -rf "${hlsDir}"`);
      unlinkSync(videoFile.path);
      if (subtitleFile) unlinkSync(subtitleFile.path);
    } catch (e) {
      console.warn('Cleanup error:', e.message);
    }

    // Send final result
    sendProgress({
      stage: 'complete',
      message: 'Video ready!',
      progress: 100,
      cid: rootCid,
      qualities: validQualities,
      totalSize,
      hasSubtitles,
      originalCid,
      masterPlaylist: `${IPFS_GATEWAY_URL}/ipfs/${rootCid}/master.m3u8`
    });

    res.end();

  } catch (error) {
    console.error('Transcode error:', error);

    // Cleanup on error
    try {
      execSync(`rm -rf "${hlsDir}"`);
      if (videoFile?.path) unlinkSync(videoFile.path);
      if (subtitleFile?.path) unlinkSync(subtitleFile.path);
    } catch (e) { /* ignore */ }

    sendError(error.message || 'Transcoding failed');
  }
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Blue Railroad Pinning Service listening on port ${PORT}`);
  console.log(`Pinata configured: ${PINATA_JWT ? 'yes (JWT)' : 'NO - uploads will fail'}`);
  console.log(`IPFS API URL: ${IPFS_API_URL}`);
  console.log(`IPFS Gateway URL: ${IPFS_GATEWAY_URL}`);
  const walletCount = AUTHORIZED_WALLETS.split(',').filter(w => w.trim()).length;
  console.log(`Wallet auth: ${walletCount} authorized wallet(s)`);
});
