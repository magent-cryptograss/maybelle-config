/**
 * MediaWiki API client for updating Blue Railroad submission pages.
 * Updates submission pages with IPFS CID after successful pinning.
 */

import fetch from 'node-fetch';
import YAML from 'js-yaml';

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
 * Update or add a field in a template.
 * @param {string} wikitext - The page wikitext
 * @param {string} templateName - Template name (e.g., 'Blue Railroad Submission', 'Album Submission')
 * @param {string} fieldName - Field name to update
 * @param {string} fieldValue - New field value
 */
function updateTemplateField(wikitext, templateName, fieldName, fieldValue) {
  // Pattern to match the template and capture its contents
  // Escape special regex chars in template name
  const escapedName = templateName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const templatePattern = new RegExp(`(\\{\\{${escapedName}\\s*)(.*?)(\\}\\})`, 'is');
  const match = wikitext.match(templatePattern);

  if (!match) {
    throw new Error(`Could not find {{${templateName}}} template in page`);
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

  // Update the ipfs_cid field and add status=proposed for bot verification
  let result;
  try {
    result = updateTemplateField(currentContent, 'Blue Railroad Submission', 'ipfs_cid', ipfsCid);
    // Add status=proposed to satisfy PickiPedia bot edit requirements
    result = updateTemplateField(result.wikitext, 'Blue Railroad Submission', 'status', 'proposed');
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

/**
 * Create a Release page with YAML content.
 * @param {Object} metadata - Release metadata
 * @param {string} metadata.title - Track/release title
 * @param {string} metadata.ipfs_cid - Primary CID (streaming format)
 * @param {string} [metadata.ipfs_cid_lossless] - Lossless CID (FLAC)
 * @param {string} [metadata.album] - Album name
 * @param {string} [metadata.artist] - Artist name
 * @param {number} [metadata.track_number] - Track number in album
 * @param {string} [metadata.file_type] - MIME type (audio/ogg, video/webm, etc.)
 * @param {number} [metadata.file_size] - File size in bytes
 * @param {string} [metadata.description] - Description
 * @returns {Object} Result with page_title and action
 */
export async function createReleasePage(metadata) {
  if (!metadata.title || !metadata.ipfs_cid) {
    throw new Error('Release requires at least title and ipfs_cid');
  }

  // Ensure we're logged in
  if (!editToken) {
    await login();
  }

  // Generate page title from CID (canonical identifier)
  const pageTitle = `Release:${metadata.ipfs_cid}`;

  // Build YAML content safely using js-yaml to prevent injection
  const yamlData = {
    title: metadata.title,
    ipfs_cid: metadata.ipfs_cid
  };

  // Add optional fields if present
  if (metadata.ipfs_cid_lossless) yamlData.ipfs_cid_lossless = metadata.ipfs_cid_lossless;
  if (metadata.album) yamlData.album = metadata.album;
  if (metadata.artist) yamlData.artist = metadata.artist;
  if (metadata.track_number) yamlData.track_number = metadata.track_number;
  if (metadata.file_type) yamlData.file_type = metadata.file_type;
  if (metadata.file_size) yamlData.file_size = metadata.file_size;
  if (metadata.description) yamlData.description = metadata.description;
  yamlData.created_at = new Date().toISOString();

  // Use YAML.dump for safe serialization (handles special chars, newlines, etc.)
  const yamlContent = YAML.dump(yamlData, { lineWidth: -1 });

  // Check if page already exists
  const existingContent = await getPageContent(pageTitle);
  if (existingContent) {
    console.log(`[wiki] Release page already exists: ${pageTitle}`);
    return {
      action: 'exists',
      page_title: pageTitle,
      message: 'Release page already exists'
    };
  }

  // Create the page with release-yaml content model
  const url = `${WIKI_URL}/api.php`;
  const params = new URLSearchParams({
    action: 'edit',
    title: pageTitle,
    text: yamlContent,
    contentmodel: 'release-yaml',
    summary: `Create release: ${metadata.title}`,
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
    if (data.error.code === 'badtoken') {
      editToken = null;
      await login();
      return createReleasePage(metadata);
    }
    throw new Error(`Wiki create failed: ${data.error.info}`);
  }

  console.log(`[wiki] Created release page: ${pageTitle}`);

  return {
    action: 'created',
    page_title: pageTitle,
    message: `Created release: ${metadata.title}`
  };
}

/**
 * Update an Album Submission page with IPFS CID after pinning.
 * @param {number|string} submissionId - The submission number
 * @param {string} ipfsCid - The IPFS CID for the directory
 * @param {string} [ipfsCidLossless] - Optional separate CID for lossless files
 * @returns {Object} Result with action and message
 */
export async function updateAlbumSubmissionCid(submissionId, ipfsCid, ipfsCidLossless = null) {
  const pageTitle = `Album Submission/${submissionId}`;

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
    result = updateTemplateField(currentContent, 'Album Submission', 'ipfs_cid', ipfsCid);

    // Add lossless CID if provided
    if (ipfsCidLossless) {
      result = updateTemplateField(result.wikitext, 'Album Submission', 'ipfs_cid_lossless', ipfsCidLossless);
    }

    // Mark as pinned
    result = updateTemplateField(result.wikitext, 'Album Submission', 'status', 'pinned');
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
  const summary = `Pin album: ${ipfsCid.slice(0, 20)}... (via pinning service)`;
  await savePage(pageTitle, result.wikitext, summary);

  console.log(`[wiki] Updated ${pageTitle}`);

  return {
    action: 'updated',
    message: `Updated ${pageTitle} with IPFS CID`
  };
}
