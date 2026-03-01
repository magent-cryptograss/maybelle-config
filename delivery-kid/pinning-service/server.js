/**
 * delivery-kid Pinning Service
 *
 * Simple API for pinning content to the local IPFS node
 * and optionally backing up to Pinata.
 */

import express from 'express';
import multer from 'multer';
import { execSync, spawn } from 'child_process';
import { createReadStream, statSync, existsSync, mkdirSync, writeFileSync, readFileSync } from 'fs';
import { join } from 'path';

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3001;
const IPFS_API = process.env.IPFS_API || 'http://127.0.0.1:5001';
const PINATA_JWT = process.env.PINATA_JWT;
const UPLOAD_DIR = process.env.UPLOAD_DIR || '/tmp/uploads';
const TORRENTS_DIR = process.env.TORRENTS_DIR || '/var/lib/aria2/torrents';

// Ensure directories exist
if (!existsSync(UPLOAD_DIR)) mkdirSync(UPLOAD_DIR, { recursive: true });
if (!existsSync(TORRENTS_DIR)) mkdirSync(TORRENTS_DIR, { recursive: true });

const upload = multer({ dest: UPLOAD_DIR });

// Health check
app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', service: 'delivery-kid' });
});

// Pin an existing CID
app.post('/api/pin', async (req, res) => {
    const { cid, name } = req.body;

    if (!cid) {
        return res.status(400).json({ error: 'CID is required' });
    }

    try {
        // Pin to local IPFS
        console.log(`Pinning ${cid}...`);
        execSync(`ipfs pin add ${cid}`, { timeout: 300000 });

        // Optionally pin to Pinata
        if (PINATA_JWT) {
            await pinToPinata(cid, name || cid);
        }

        res.json({
            success: true,
            cid,
            pinned: true,
            pinata: !!PINATA_JWT
        });
    } catch (error) {
        console.error('Pin error:', error);
        res.status(500).json({ error: error.message });
    }
});

// Upload and pin a file
app.post('/api/upload', upload.single('file'), async (req, res) => {
    if (!req.file) {
        return res.status(400).json({ error: 'No file uploaded' });
    }

    try {
        // Add to IPFS
        const result = execSync(`ipfs add -Q "${req.file.path}"`, { encoding: 'utf8' });
        const cid = result.trim();

        // Pin it
        execSync(`ipfs pin add ${cid}`);

        // Optionally pin to Pinata
        if (PINATA_JWT) {
            await pinToPinata(cid, req.file.originalname);
        }

        res.json({
            success: true,
            cid,
            name: req.file.originalname,
            size: req.file.size
        });
    } catch (error) {
        console.error('Upload error:', error);
        res.status(500).json({ error: error.message });
    }
});

// Upload and pin a directory (as tar)
app.post('/api/upload-directory', upload.single('archive'), async (req, res) => {
    if (!req.file) {
        return res.status(400).json({ error: 'No archive uploaded' });
    }

    try {
        // Extract and add to IPFS
        const extractDir = join(UPLOAD_DIR, `extract-${Date.now()}`);
        mkdirSync(extractDir, { recursive: true });

        execSync(`tar -xf "${req.file.path}" -C "${extractDir}"`);

        // Add directory to IPFS
        const result = execSync(`ipfs add -r -Q "${extractDir}"`, { encoding: 'utf8' });
        const cid = result.trim();

        // Pin it
        execSync(`ipfs pin add ${cid}`);

        // Optionally pin to Pinata
        if (PINATA_JWT) {
            await pinToPinata(cid, req.body.name || 'directory');
        }

        // Cleanup
        execSync(`rm -rf "${extractDir}" "${req.file.path}"`);

        res.json({
            success: true,
            cid,
            name: req.body.name
        });
    } catch (error) {
        console.error('Directory upload error:', error);
        res.status(500).json({ error: error.message });
    }
});

// Add a torrent for seeding
app.post('/api/torrent', upload.single('torrent'), async (req, res) => {
    if (!req.file) {
        return res.status(400).json({ error: 'No torrent file uploaded' });
    }

    try {
        const torrentPath = join(TORRENTS_DIR, req.file.originalname);
        execSync(`mv "${req.file.path}" "${torrentPath}"`);

        // Add to aria2 via RPC
        const response = await fetch('http://localhost:6800/jsonrpc', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jsonrpc: '2.0',
                id: Date.now(),
                method: 'aria2.addTorrent',
                params: [readFileSync(torrentPath).toString('base64')]
            })
        });

        const result = await response.json();

        res.json({
            success: true,
            torrent: req.file.originalname,
            gid: result.result
        });
    } catch (error) {
        console.error('Torrent add error:', error);
        res.status(500).json({ error: error.message });
    }
});

// List pinned content
app.get('/api/pins', async (req, res) => {
    try {
        const result = execSync('ipfs pin ls --type=recursive', { encoding: 'utf8' });
        const pins = result.trim().split('\n')
            .filter(line => line)
            .map(line => {
                const [cid, type] = line.split(' ');
                return { cid, type };
            });

        res.json({ pins, count: pins.length });
    } catch (error) {
        console.error('List pins error:', error);
        res.status(500).json({ error: error.message });
    }
});

// Pin to Pinata for backup
async function pinToPinata(cid, name) {
    if (!PINATA_JWT) return;

    try {
        const response = await fetch('https://api.pinata.cloud/pinning/pinByHash', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${PINATA_JWT}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                hashToPin: cid,
                pinataMetadata: { name }
            })
        });

        if (!response.ok) {
            console.error('Pinata error:', await response.text());
        }
    } catch (error) {
        console.error('Pinata pin error:', error);
    }
}

app.listen(PORT, () => {
    console.log(`delivery-kid pinning service listening on port ${PORT}`);
    console.log(`IPFS API: ${IPFS_API}`);
    console.log(`Pinata backup: ${PINATA_JWT ? 'enabled' : 'disabled'}`);
});
