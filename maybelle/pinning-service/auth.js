import { verifyMessage } from 'viem';

// Message format: "Authorize Blue Railroad pinning\nTimestamp: {timestamp}"
// Timestamp must be within 5 minutes of server time to prevent replay attacks
const MAX_TIMESTAMP_DRIFT_MS = 5 * 60 * 1000; // 5 minutes

// Authorized wallets - can be contract owner or allowlisted addresses
// Loaded from environment variable as comma-separated list
function getAuthorizedWallets() {
    const walletsEnv = process.env.AUTHORIZED_WALLETS || '';
    return walletsEnv
        .split(',')
        .map(w => w.trim().toLowerCase())
        .filter(w => w.length > 0);
}

/**
 * Create the message that must be signed for authorization
 * @param {number} timestamp - Unix timestamp in milliseconds
 * @returns {string} The message to sign
 */
export function createAuthMessage(timestamp) {
    return `Authorize Blue Railroad pinning\nTimestamp: ${timestamp}`;
}

/**
 * Verify a signed authorization message
 * @param {string} signature - The signature (0x...)
 * @param {number} timestamp - The timestamp that was signed
 * @param {number} [now] - Current time for testing (defaults to Date.now())
 * @returns {Promise<{valid: boolean, address?: string, error?: string}>}
 */
export async function verifyAuth(signature, timestamp, now = Date.now()) {
    // Check timestamp is within acceptable range
    const drift = Math.abs(now - timestamp);
    if (drift > MAX_TIMESTAMP_DRIFT_MS) {
        return {
            valid: false,
            error: `Timestamp too old or too far in future (drift: ${Math.round(drift / 1000)}s, max: ${MAX_TIMESTAMP_DRIFT_MS / 1000}s)`
        };
    }

    // Recover signer address from signature
    const message = createAuthMessage(timestamp);
    let address;
    try {
        // verifyMessage returns boolean, we need recoverMessageAddress for the address
        const { recoverMessageAddress } = await import('viem');
        address = await recoverMessageAddress({
            message,
            signature,
        });
    } catch (error) {
        return {
            valid: false,
            error: `Invalid signature: ${error.message}`
        };
    }

    // Check if address is authorized
    const authorizedWallets = getAuthorizedWallets();

    // If no wallets configured, reject all (fail secure)
    if (authorizedWallets.length === 0) {
        return {
            valid: false,
            address,
            error: 'No authorized wallets configured'
        };
    }

    const isAuthorized = authorizedWallets.includes(address.toLowerCase());
    if (!isAuthorized) {
        return {
            valid: false,
            address,
            error: `Wallet ${address} is not authorized`
        };
    }

    return {
        valid: true,
        address
    };
}

/**
 * Express middleware to require wallet signature auth
 */
export function requireWalletAuth(req, res, next) {
    const signature = req.headers['x-signature'];
    const timestamp = parseInt(req.headers['x-timestamp'], 10);

    if (!signature || !timestamp) {
        return res.status(401).json({
            error: 'Missing authentication headers',
            required: ['X-Signature', 'X-Timestamp']
        });
    }

    verifyAuth(signature, timestamp)
        .then(result => {
            if (!result.valid) {
                return res.status(401).json({ error: result.error });
            }
            // Attach verified address to request for logging
            req.verifiedAddress = result.address;
            next();
        })
        .catch(error => {
            console.error('Auth error:', error);
            res.status(500).json({ error: 'Authentication failed' });
        });
}
