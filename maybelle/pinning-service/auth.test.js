import { createAuthMessage, verifyAuth } from './auth.js';
import { privateKeyToAccount } from 'viem/accounts';

// Test wallet - DO NOT use in production
const TEST_PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
const testAccount = privateKeyToAccount(TEST_PRIVATE_KEY);
const TEST_ADDRESS = testAccount.address; // 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266

describe('createAuthMessage', () => {
    it('creates message with correct format', () => {
        const timestamp = 1704067200000; // 2024-01-01 00:00:00 UTC
        const message = createAuthMessage(timestamp);
        expect(message).toBe('Authorize Blue Railroad pinning\nTimestamp: 1704067200000');
    });
});

describe('verifyAuth', () => {
    const originalEnv = process.env.AUTHORIZED_WALLETS;

    beforeEach(() => {
        // Set test wallet as authorized
        process.env.AUTHORIZED_WALLETS = TEST_ADDRESS;
    });

    afterEach(() => {
        process.env.AUTHORIZED_WALLETS = originalEnv;
    });

    it('accepts valid signature from authorized wallet', async () => {
        const now = Date.now();
        const message = createAuthMessage(now);
        const signature = await testAccount.signMessage({ message });

        const result = await verifyAuth(signature, now, now);

        expect(result.valid).toBe(true);
        expect(result.address.toLowerCase()).toBe(TEST_ADDRESS.toLowerCase());
    });

    it('rejects signature with expired timestamp', async () => {
        const now = Date.now();
        const oldTimestamp = now - (6 * 60 * 1000); // 6 minutes ago
        const message = createAuthMessage(oldTimestamp);
        const signature = await testAccount.signMessage({ message });

        const result = await verifyAuth(signature, oldTimestamp, now);

        expect(result.valid).toBe(false);
        expect(result.error).toContain('Timestamp too old');
    });

    it('rejects signature with future timestamp', async () => {
        const now = Date.now();
        const futureTimestamp = now + (6 * 60 * 1000); // 6 minutes from now
        const message = createAuthMessage(futureTimestamp);
        const signature = await testAccount.signMessage({ message });

        const result = await verifyAuth(signature, futureTimestamp, now);

        expect(result.valid).toBe(false);
        expect(result.error).toContain('too far in future');
    });

    it('accepts signature within 5 minute window', async () => {
        const now = Date.now();
        const timestamp = now - (4 * 60 * 1000); // 4 minutes ago (within limit)
        const message = createAuthMessage(timestamp);
        const signature = await testAccount.signMessage({ message });

        const result = await verifyAuth(signature, timestamp, now);

        expect(result.valid).toBe(true);
    });

    it('rejects signature from unauthorized wallet', async () => {
        // Use a different private key
        const unauthorizedKey = '0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d';
        const unauthorizedAccount = privateKeyToAccount(unauthorizedKey);

        const now = Date.now();
        const message = createAuthMessage(now);
        const signature = await unauthorizedAccount.signMessage({ message });

        const result = await verifyAuth(signature, now, now);

        expect(result.valid).toBe(false);
        expect(result.error).toContain('not authorized');
        expect(result.address).toBeDefined();
    });

    it('rejects when no authorized wallets configured', async () => {
        process.env.AUTHORIZED_WALLETS = '';

        const now = Date.now();
        const message = createAuthMessage(now);
        const signature = await testAccount.signMessage({ message });

        const result = await verifyAuth(signature, now, now);

        expect(result.valid).toBe(false);
        expect(result.error).toContain('No authorized wallets configured');
    });

    it('accepts multiple authorized wallets', async () => {
        const otherAddress = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
        process.env.AUTHORIZED_WALLETS = `${TEST_ADDRESS},${otherAddress}`;

        const now = Date.now();
        const message = createAuthMessage(now);
        const signature = await testAccount.signMessage({ message });

        const result = await verifyAuth(signature, now, now);

        expect(result.valid).toBe(true);
    });

    it('handles malformed signature gracefully', async () => {
        const now = Date.now();
        const badSignature = '0xdeadbeef';

        const result = await verifyAuth(badSignature, now, now);

        expect(result.valid).toBe(false);
        expect(result.error).toContain('Invalid signature');
    });

    it('is case-insensitive for wallet addresses', async () => {
        // Set authorized wallet in lowercase
        process.env.AUTHORIZED_WALLETS = TEST_ADDRESS.toLowerCase();

        const now = Date.now();
        const message = createAuthMessage(now);
        const signature = await testAccount.signMessage({ message });

        const result = await verifyAuth(signature, now, now);

        expect(result.valid).toBe(true);
    });
});
