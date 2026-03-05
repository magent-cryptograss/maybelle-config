const express = require('express');
const cors = require('cors');

const app = express();
const PORT = process.env.PORT || 3001;

// Config from environment
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || 'http://localhost:5173').split(',');

// ============================================
// SECURITY: Rate limiting (in-memory)
// ============================================
const rateLimitMap = new Map();
const RATE_LIMIT_WINDOW = 60 * 1000; // 1 minute
const RATE_LIMIT_MAX_REQUESTS = 5; // max 5 requests per minute per IP

function getRateLimitKey(req) {
    return req.ip || req.headers['x-forwarded-for'] || 'unknown';
}

function checkRateLimit(req) {
    const key = getRateLimitKey(req);
    const now = Date.now();
    const windowStart = now - RATE_LIMIT_WINDOW;

    if (!rateLimitMap.has(key)) {
        rateLimitMap.set(key, []);
    }

    const requests = rateLimitMap.get(key).filter(time => time > windowStart);
    requests.push(now);
    rateLimitMap.set(key, requests);

    return requests.length <= RATE_LIMIT_MAX_REQUESTS;
}

// Clean up old rate limit entries every 5 minutes
setInterval(() => {
    const now = Date.now();
    const windowStart = now - RATE_LIMIT_WINDOW;
    for (const [key, times] of rateLimitMap.entries()) {
        const valid = times.filter(time => time > windowStart);
        if (valid.length === 0) {
            rateLimitMap.delete(key);
        } else {
            rateLimitMap.set(key, valid);
        }
    }
}, 5 * 60 * 1000);

// ============================================
// SECURITY: Input sanitization
// ============================================
function escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function sanitizeInput(text, maxLength = 1000) {
    if (!text) return '';
    return String(text).slice(0, maxLength).trim();
}

function isValidEmail(email) {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(email) && email.length <= 254;
}

// ============================================
// Middleware
// ============================================

// Request size limit (prevent large payload attacks)
app.use(express.json({ limit: '10kb' }));
app.use(express.urlencoded({ extended: true, limit: '10kb' }));

// CORS - strict origin checking
app.use(cors({
    origin: function(origin, callback) {
        // Allow requests with no origin (like mobile apps or curl)
        // but in production you might want to reject these
        if (!origin) return callback(null, true);

        if (ALLOWED_ORIGINS.includes(origin)) {
            callback(null, true);
        } else {
            console.log(`Blocked request from origin: ${origin}`);
            callback(new Error('Not allowed by CORS'));
        }
    },
    methods: ['POST', 'OPTIONS'],
    allowedHeaders: ['Content-Type']
}));

// Rate limiting middleware
app.use((req, res, next) => {
    if (req.method === 'POST') {
        if (!checkRateLimit(req)) {
            console.log(`Rate limit exceeded for ${getRateLimitKey(req)}`);
            return res.status(429).json({ error: 'Too many requests. Please wait a minute.' });
        }
    }
    next();
});

// ============================================
// Telegram notification
// ============================================
async function sendTelegram(message) {
    if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) {
        console.log('Telegram not configured, skipping notification');
        console.log('Message would be:', message);
        return { ok: false, reason: 'not_configured' };
    }

    const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                chat_id: TELEGRAM_CHAT_ID,
                text: message,
                parse_mode: 'HTML'
            })
        });

        const data = await response.json();
        if (!data.ok) {
            console.error('Telegram API error:', data);
        }
        return data;
    } catch (error) {
        console.error('Failed to send Telegram message:', error);
        return { ok: false, error: error.message };
    }
}

// ============================================
// Endpoints
// ============================================

// Health check
app.get('/health', (req, res) => {
    res.json({
        status: 'ok',
        telegram_configured: !!(TELEGRAM_BOT_TOKEN && TELEGRAM_CHAT_ID),
        allowed_origins: ALLOWED_ORIGINS
    });
});

// Booking inquiry endpoint
app.post('/inquiry', async (req, res) => {
    const { name, email, message, website } = req.body;

    // SECURITY: Honeypot field - bots often fill hidden fields
    if (website) {
        console.log(`Honeypot triggered from ${getRateLimitKey(req)}`);
        // Pretend success but don't actually process
        return res.json({ success: true, message: 'Inquiry received' });
    }

    // Validate required fields
    if (!name || !email) {
        return res.status(400).json({ error: 'Name and email are required' });
    }

    // Sanitize inputs
    const cleanName = sanitizeInput(name, 100);
    const cleanEmail = sanitizeInput(email, 254);
    const cleanMessage = sanitizeInput(message, 2000);

    // Validate email format
    if (!isValidEmail(cleanEmail)) {
        return res.status(400).json({ error: 'Invalid email address' });
    }

    const timestamp = new Date().toISOString();
    const clientIP = getRateLimitKey(req);

    const telegramMessage = `
<b>New Booking Inquiry</b>

<b>From:</b> ${escapeHtml(cleanName)}
<b>Email:</b> ${escapeHtml(cleanEmail)}
<b>Time:</b> ${timestamp}
<b>IP:</b> ${clientIP}

<b>Message:</b>
${escapeHtml(cleanMessage) || '(no message)'}
    `.trim();

    console.log(`[${timestamp}] Inquiry from ${cleanName} <${cleanEmail}> (${clientIP})`);

    const telegramResult = await sendTelegram(telegramMessage);

    res.json({
        success: true,
        message: 'Inquiry received'
    });
});

// Mailing list signup endpoint
app.post('/subscribe', async (req, res) => {
    const { email, website } = req.body;

    // SECURITY: Honeypot field
    if (website) {
        console.log(`Honeypot triggered from ${getRateLimitKey(req)}`);
        return res.json({ success: true, message: 'Subscribed successfully' });
    }

    if (!email) {
        return res.status(400).json({ error: 'Email is required' });
    }

    const cleanEmail = sanitizeInput(email, 254);

    if (!isValidEmail(cleanEmail)) {
        return res.status(400).json({ error: 'Invalid email address' });
    }

    const timestamp = new Date().toISOString();
    const clientIP = getRateLimitKey(req);

    const telegramMessage = `
<b>New Mailing List Signup</b>

<b>Email:</b> ${escapeHtml(cleanEmail)}
<b>Time:</b> ${timestamp}
<b>IP:</b> ${clientIP}
    `.trim();

    console.log(`[${timestamp}] Mailing list signup: ${cleanEmail} (${clientIP})`);

    // TODO: Store in PostgreSQL for actual mailing list

    const telegramResult = await sendTelegram(telegramMessage);

    res.json({
        success: true,
        message: 'Subscribed successfully'
    });
});

// Catch-all for undefined routes
app.use((req, res) => {
    res.status(404).json({ error: 'Not found' });
});

// Error handler
app.use((err, req, res, next) => {
    console.error('Server error:', err);
    res.status(500).json({ error: 'Internal server error' });
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`Inquiry notifier listening on port ${PORT}`);
    console.log(`Telegram configured: ${!!(TELEGRAM_BOT_TOKEN && TELEGRAM_CHAT_ID)}`);
    console.log(`Allowed origins: ${ALLOWED_ORIGINS.join(', ')}`);
    console.log(`Rate limit: ${RATE_LIMIT_MAX_REQUESTS} requests per ${RATE_LIMIT_WINDOW/1000}s`);
});
