/**
 * MediaWiki API client for updating Blue Railroad submission pages.
 * Updates submission pages with IPFS CID after successful pinning.
 */

import fetch from 'node-fetch';

const WIKI_URL = process.env.WIKI_URL || 'https://pickipedia.xyz';
const WIKI_BOT_USER = process.env.WIKI_BOT_USER;
const WIKI_BOT_PASSWORD = process.env.WIKI_BOT_PASSWORD;

// Cache the login session
let loginToken = null;
let editToken = null;
let cookies = '';

/**
 * Get a token from the MediaWiki API.
 */
async function getToken(type) {
  const url = `${WIKI_URL}/api.php?action=query&meta=tokens&type=${type}&format=json`;
  const response = await fetch(url, {
    headers: { 'Cookie': cookies }
  });

  const setCookies = response.headers.raw()['set-cookie'];
  if (setCookies) {
    cookies = setCookies.map(c => c.split(';')[0]).join('; ');
  }

  const data = await response.json();
  return data.query.tokens[`${type}token`];
}

/**
 * Login to the MediaWiki API.
 */
async function login() {
  if (!WIKI_BOT_USER || !WIKI_BOT_PASSWORD) {
    throw new Error('Wiki bot credentials not configured (WIKI_BOT_USER, WIKI_BOT_PASSWORD)');
  }

  // Get login token
  loginToken = await getToken('login');

  // Perform login
  const url = `${WIKI_URL}/api.php`;
  const params = new URLSearchParams({
    action: 'login',
    lgname: WIKI_BOT_USER,
    lgpassword: WIKI_BOT_PASSWORD,
    lgtoken: loginToken,
    format: 'json'
  });

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Cookie': cookies
    },
    body: params.toString()
  });

  const setCookies = response.headers.raw()['set-cookie'];
  if (setCookies) {
    cookies = setCookies.map(c => c.split(';')[0]).join('; ');
  }

  const data = await response.json();

  if (data.login?.result !== 'Success') {
    throw new Error(`Wiki login failed: ${data.login?.reason || JSON.stringify(data)}`);
  }

  console.log(`[wiki] Logged in as ${WIKI_BOT_USER}`);

  // Get edit token
  editToken = await getToken('csrf');
}

/**
 * Get the content of a wiki page.
 */
async function getPageContent(title) {
  const url = `${WIKI_URL}/api.php?action=query&titles=${encodeURIComponent(title)}&prop=revisions&rvprop=content&rvslots=main&format=json`;

  const response = await fetch(url, {
    headers: { 'Cookie': cookies }
  });

  const data = await response.json();
  const pages = data.query.pages;
  const pageId = Object.keys(pages)[0];

  if (pageId === '-1') {
    return null; // Page doesn't exist
  }

  return pages[pageId].revisions[0].slots.main['*'];
}

/**
 * Update or add a field in a Blue Railroad Submission template.
 */
function updateSubmissionField(wikitext, fieldName, fieldValue) {
  // Pattern to match the template and capture its contents
  const templatePattern = /(\{\{Blue Railroad Submission\s*)(.*?)(\}\})/is;
  const match = wikitext.match(templatePattern);

  if (!match) {
    throw new Error('Could not find {{Blue Railroad Submission}} template in page');
  }

  const templateStart = match[1];
  let templateBody = match[2];
  const templateEnd = match[3];

  // Check if field already exists
  const fieldPattern = new RegExp(`\\|${fieldName}\\s*=\\s*[^\\|]*`, 'i');
  const existingMatch = templateBody.match(fieldPattern);

  if (existingMatch) {
    // Update existing field
    const oldValue = existingMatch[0];
    const newValue = `|${fieldName}=${fieldValue}`;
    if (oldValue.trim() === newValue.trim()) {
      return { wikitext, changed: false };
    }
    templateBody = templateBody.slice(0, existingMatch.index) +
                   newValue +
                   templateBody.slice(existingMatch.index + existingMatch[0].length);
  } else {
    // Add new field before the closing }}
    templateBody = templateBody.trimEnd();
    if (!templateBody.endsWith('\n')) {
      templateBody += '\n';
    }
    templateBody += `|${fieldName}=${fieldValue}\n`;
  }

  const newWikitext = wikitext.slice(0, match.index) +
                      templateStart + templateBody + templateEnd +
                      wikitext.slice(match.index + match[0].length);

  return { wikitext: newWikitext, changed: true };
}

/**
 * Save a wiki page.
 */
async function savePage(title, content, summary) {
  if (!editToken) {
    await login();
  }

  const url = `${WIKI_URL}/api.php`;
  const params = new URLSearchParams({
    action: 'edit',
    title,
    text: content,
    summary,
    token: editToken,
    format: 'json'
  });

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Cookie': cookies
    },
    body: params.toString()
  });

  const data = await response.json();

  if (data.error) {
    // Token might have expired, try re-login
    if (data.error.code === 'badtoken') {
      editToken = null;
      await login();
      return savePage(title, content, summary);
    }
    throw new Error(`Wiki edit failed: ${data.error.info}`);
  }

  return data.edit;
}

/**
 * Update a submission page with IPFS CID.
 * @param {number} submissionId - The submission number
 * @param {string} ipfsCid - The IPFS CID to record
 * @returns {Object} Result with action and message
 */
export async function updateSubmissionCid(submissionId, ipfsCid) {
  const pageTitle = `Blue Railroad Submission/${submissionId}`;

  console.log(`[wiki] Updating ${pageTitle} with IPFS CID: ${ipfsCid}`);

  // Ensure we're logged in
  if (!editToken) {
    await login();
  }

  // Get current page content
  const currentContent = await getPageContent(pageTitle);

  if (!currentContent) {
    return {
      action: 'error',
      message: `Page not found: ${pageTitle}`
    };
  }

  // Update the ipfs_cid field
  let result;
  try {
    result = updateSubmissionField(currentContent, 'ipfs_cid', ipfsCid);
  } catch (err) {
    return {
      action: 'error',
      message: err.message
    };
  }

  if (!result.changed) {
    return {
      action: 'unchanged',
      message: 'IPFS CID already set to this value'
    };
  }

  // Save the updated page
  const summary = `Add IPFS CID: ${ipfsCid.slice(0, 20)}... (via pinning service)`;
  await savePage(pageTitle, result.wikitext, summary);

  console.log(`[wiki] Updated ${pageTitle}`);

  return {
    action: 'updated',
    message: `Updated ${pageTitle} with IPFS CID`
  };
}

/**
 * Check if wiki credentials are configured.
 */
export function isWikiConfigured() {
  return !!(WIKI_BOT_USER && WIKI_BOT_PASSWORD);
}
