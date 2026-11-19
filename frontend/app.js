
/* winter.cc Frontend V7.4
   Provider-aware queues, cooldowns, automatic resume,
   live results tabs, and existing website features.
*/

const $ = id => document.getElementById(id);

let token = sessionStorage.getItem('winter_token') || '';
let names = [];
let statuses = {};
let sources = {};
let checkReasons = {};
let searchId = null;
let wordlist = null;
let platforms = {};
let isAdmin = false;
let adminUserCache = [];
let selectedAdminMember = null;
let checkingInProgress = false;
let generatingInProgress = false;
let generationVersion = 0;
let checkRun = null;
let resultTab = 'all';

const RESULT_TABS = [
  ['all', 'All'],
  ['available', 'Available'],
  ['claimed', 'Taken'],
  ['unknown', 'Unknown'],
  ['pending', 'Pending'],
  ['invalid', 'Invalid']
];

const types = [
  'Random letters',
  'Letters + numbers',
  'Real words / OG',
  'Pronounceable',
  'Aesthetic',
  'Numbers only'
];

const chars = [
  'Lowercase',
  'Uppercase',
  'Numbers',
  'Underscores',
  'Periods'
];

const DEFAULT_BATCH_SIZE = 25;
const MINECRAFT_BATCH_SIZE = 10;
const ROBLOX_BATCH_SIZE = 5;
const DISCORD_BATCH_SIZE = 1;
const DISCORD_DELAY_MS = 900;
const MAX_AUTO_RESUMES = 0;

const RATE_LIMIT_REASONS = new Set([
  'discord_rate_limited',
  'discord_paused',
  'rate_limited',
  'minecraft_http_429',
  'roblox_http_429',
  'discord_http_429'
]);

const ACCESS_BLOCK_REASONS = new Set([
  'discord_access_blocked',
  'pomelo_access_denied'
]);

let resumeTimer = null;
let countdownTimer = null;

// =====================================
// API
// =====================================

async function api(path, body, method = 'POST') {
  const response = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(token
        ? { Authorization: 'Bearer ' + token }
        : {})
    },
    ...(body === undefined
      ? {}
      : { body: JSON.stringify(body) })
  });

  let data;

  try {
    data = await response.json();
  } catch {
    throw Error(
      'Server returned an unexpected response'
    );
  }

  if (!response.ok) {
    if (
      response.status === 401 &&
      path !== 'login'
    ) {
      showLogin();
    }

    throw Error(
      typeof data.detail === 'string'
        ? data.detail
        : 'Request failed'
    );
  }

  return data;
}

function showLogin() {
  token = '';
  isAdmin = false;
  adminUserCache = [];
  selectedAdminMember = null;
  generationVersion++;

  clearResumeTimers();
  checkRun = null;

  sessionStorage.removeItem('winter_token');

  $('app').hidden = true;
  $('login').hidden = false;
}

function showApp() {
  $('login').hidden = true;
  $('app').hidden = false;
}

function notice(message) {
  $('message').textContent = message;
}

function adminNotice(message, kind = 'success') {
  const box = $('adminMessage');

  box.textContent = message;
  box.className = 'admin-feedback ' +
    (
      kind === 'error'
        ? 'feedback-error'
        : 'feedback-success'
    );
}

function checked(id) {
  return [
    ...document.querySelectorAll(
      '#' + id + ' input:checked'
    )
  ].map(x => x.value);
}

function config() {
  return {
    platform: $('platform').value,
    lengths: checked('lengths').map(Number),
    types: checked('types'),
    characters: checked('characters'),
    prefix: $('prefix').value,
    suffix: $('suffix').value,
    amount: Number($('amount').value),
    wordlist
  };
}

function checkboxes(id, values, selected) {
  $(id).replaceChildren(
    ...values.map(value => {
      const label = document.createElement('label');
      label.className = 'check';

      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = value;
      input.checked = selected.includes(value);

      label.append(
        input,
        document.createTextNode(' ' + value)
      );

      return label;
    })
  );
}

function updateLengths(selected) {
  const rule = platforms[$('platform').value];

  if (!rule) return;

  const values = Array.from(
    { length: rule.max - rule.min + 1 },
    (_, i) => String(rule.min + i)
  );

  checkboxes(
    'lengths',
    values,
    selected
      .filter(x => values.includes(String(x)))
      .map(String)
  );
}

function link(platform, name) {
  const n = encodeURIComponent(name);

  return {
    Discord: 'https://discord.com/',
    Roblox:
      'https://www.roblox.com/search/users?keyword=' + n,
    Instagram:
      'https://www.instagram.com/' + n + '/',
    TikTok:
      'https://www.tiktok.com/@' + n,
    Telegram:
      'https://t.me/' + n,
    Minecraft:
      'https://www.minecraft.net/'
  }[platform] || '#';
}

function download(ext, content, mime) {
  const url = URL.createObjectURL(
    new Blob([content], { type: mime })
  );

  const a = document.createElement('a');
  a.href = url;

  a.download =
    'wintercc-' +
    $('platform').value.toLowerCase() +
    (resultTab === 'all' ? '' : '-' + resultTab) +
    '.' + ext;

  a.click();

  setTimeout(
    () => URL.revokeObjectURL(url),
    500
  );
}

// =====================================
// RESULTS
// =====================================

function resultCategory(username, pendingSet) {
  const status = statuses[username];

  if (
    status === 'available' ||
    status === 'claimed' ||
    status === 'invalid'
  ) {
    return status;
  }

  if (
    !status ||
    (pendingSet && pendingSet.has(username))
  ) {
    return 'pending';
  }

  return 'unknown';
}

function sortedNames() {
  const query = $('filter').value.toLowerCase();

  const pending = new Set(
    checkRun && Array.isArray(checkRun.pending)
      ? checkRun.pending
      : []
  );

  const arr = names.filter(n =>
    n.toLowerCase().includes(query) &&
    (
      resultTab === 'all' ||
      resultCategory(n, pending) === resultTab
    )
  );

  switch ($('sort').value) {
    case 'az':
      arr.sort();
      break;

    case 'za':
      arr.sort().reverse();
      break;

    case 'short':
      arr.sort(
        (a, b) => a.length - b.length
      );
      break;
  }

  return arr;
}

function resultCounts() {
  const tally = {
    all: names.length,
    available: 0,
    claimed: 0,
    unknown: 0,
    pending: 0,
    invalid: 0
  };

  const pending = new Set(
    checkRun && Array.isArray(checkRun.pending)
      ? checkRun.pending
      : []
  );

  for (const n of names) {
    tally[resultCategory(n, pending)]++;
  }

  return tally;
}

function installResultTabs() {
  if ($('winter-result-tabs')) return;

  const tabs = document.createElement('div');

  tabs.id = 'winter-result-tabs';
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute(
    'aria-label',
    'Filter usernames by availability'
  );

  for (const [key, label] of RESULT_TABS) {
    const button = document.createElement('button');

    button.type = 'button';
    button.className = 'winter-result-tab';
    button.dataset.category = key;

    button.setAttribute('role', 'tab');
    button.setAttribute(
      'aria-controls',
      'resultList'
    );

    button.onclick = () => {
      resultTab = key;
      render();
    };

    button.textContent = label;
    tabs.append(button);
  }

  $('resultList').parentNode.insertBefore(
    tabs,
    $('resultList')
  );
}

function updateResultTabs() {
  const tabs = $('winter-result-tabs');

  if (!tabs) return;

  const tally = resultCounts();

  for (const button of tabs.querySelectorAll('button')) {
    const key = button.dataset.category;

    const label = RESULT_TABS.find(
      ([category]) => category === key
    )[1];

    const active = resultTab === key;

    button.textContent =
      label +
      ' (' +
      tally[key].toLocaleString() +
      ')';

    button.classList.toggle(
      'winter-active-tab',
      active
    );

    button.setAttribute(
      'aria-selected',
      String(active)
    );
  }
}

function statusLabel(status) {
  return {
    available: 'Available',
    claimed: 'Taken',
    invalid: 'Invalid',
    unknown: 'Unknown',
    skipped: 'Skipped'
  }[status] || 'Not checked';
}

function friendlyReason(reason) {
  if (!reason) return '';

  const labels = {
    discord_reported_available:
      'Discord reports this username is not taken',

    discord_reported_taken:
      'Discord reports this username is taken',

    discord_rate_limited:
      'Discord rate limit reached; waiting for cooldown',

    discord_access_blocked:
      'Discord denied this server request',

    discord_paused:
      'Checking paused after Discord rate limit',

    discord_batch_limit:
      'Discord batch limit reached',

    rate_limited:
      'Provider rate limit; waiting for cooldown',

    minecraft_http_429:
      'Minecraft rate limit; waiting for cooldown',

    roblox_http_429:
      'Roblox rate limit; waiting for cooldown',

    discord_http_429:
      'Discord rate limit; waiting for cooldown',

    discord_unexpected_response:
      'Discord returned an unexpected response',

    discord_invalid_response:
      'Discord returned invalid response data',

    discord_timeout:
      'Discord did not respond in time',

    discord_network_error:
      'Could not connect to Discord',

    discord_ssl_error:
      'Secure connection to Discord failed',

    discord_dns_error:
      'Could not resolve Discord server address',

    discord_connection_refused:
      'Discord connection refused',

    already_recorded:
      'Previously saved result',

    no_existing_profile:
      'No existing Minecraft profile; availability unconfirmed',

    format_valid_but_availability_unverified:
      'Username format valid; availability not verified',

    roblox_signup_validation:
      'Roblox signup validation result',

    existing_profile:
      'Existing Minecraft profile found',

    not_checked:
      'Not checked by provider'
  };

  return (
    labels[reason] ||
    reason.replaceAll('_', ' ')
  );
}

// =====================================
// STYLES
// =====================================

function installStyles() {
  if (document.getElementById('winter-v6-style')) {
    return;
  }

  const style = document.createElement('style');
  style.id = 'winter-v6-style';

  style.textContent = `
    @keyframes winterRainbow {
      from { background-position: 0% 50%; }
      to { background-position: 200% 50%; }
    }

    @keyframes winterGlow {
      0%, 100% {
        filter: drop-shadow(0 0 3px #ff3b8d);
      }
      50% {
        filter: drop-shadow(0 0 6px #00dfff);
      }
    }

    #resultList .winter-rgb-name {
      display: inline-block;
      background-image: linear-gradient(
        90deg,
        #ff245f,
        #ffb300,
        #f8ff00,
        #00ff80,
        #00dcff,
        #5865ff,
        #c44dff,
        #ff245f
      );
      background-size: 200% 100%;
      background-clip: text;
      -webkit-background-clip: text;
      color: transparent;
      -webkit-text-fill-color: transparent;
      font-weight: 900;
      animation:
        winterRainbow 2.4s linear infinite,
        winterGlow 3s ease-in-out infinite;
    }

    #resultList .winter-available-row {
      border-color: rgba(177,90,255,.32) !important;
    }

    #resultList .winter-available-row .status {
      color: #54ffab;
      font-weight: 700;
    }

    #winter-v6-progress {
      margin: 12px 0;
      padding: 12px;
      border: 1px solid rgba(160,110,255,.3);
      border-radius: 12px;
    }

    #winter-v6-progress[hidden],
    #winter-v6-resume[hidden] {
      display: none !important;
    }

    #winter-v6-track {
      height: 8px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(150,150,180,.2);
      margin-top: 9px;
    }

    #winter-v6-fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(
        90deg,
        #893aff,
        #dc4cff,
        #00dfff
      );
      transition: width .3s;
    }

    #winter-v6-resume {
      margin-top: 10px;
    }

    #resultList .winter-reason {
      display: block;
      opacity: .8;
      font-size: 11px;
      max-width: 100%;
      overflow-wrap: anywhere;
    }

    #winter-result-tabs {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      max-width: 100%;
      padding: 8px 0 14px;
      -webkit-overflow-scrolling: touch;
    }

    #winter-result-tabs .winter-result-tab {
      flex: 0 0 auto;
      white-space: nowrap;
      border: 1px solid rgba(160,140,210,.35);
      border-radius: 999px;
      padding: 9px 12px;
      font-size: 12px;
      cursor: pointer;
      background: rgba(120,80,160,.10);
      color: inherit;
      font-weight: 600;
    }

    #winter-result-tabs .winter-active-tab {
      background: linear-gradient(
        100deg,
        #6030b5,
        #843dd0
      ) !important;
      color: white !important;
      border-color: #b18afe !important;
    }

    #resultList .winter-pending-status {
      opacity: .65;
    }

    @media (prefers-reduced-motion: reduce) {
      #resultList .winter-rgb-name {
        animation: none;
        filter: none;
      }
    }
  `;

  document.head.append(style);
}

// =====================================
// RENDER RESULTS
// =====================================

function render() {
  updateResultTabs();

  const list = $('resultList');
  list.replaceChildren();

  const filtered = sortedNames();

  const pending = new Set(
    checkRun && Array.isArray(checkRun.pending)
      ? checkRun.pending
      : []
  );

  for (const n of filtered.slice(0, 500)) {
    const row = document.createElement('div');
    row.className = 'result';

    const name = document.createElement('code');
    name.textContent = n;

    const status = document.createElement('span');
    status.className = 'status';

    const category = resultCategory(n, pending);

    status.textContent =
      category === 'pending'
        ? 'Pending'
        : statusLabel(statuses[n]);

    if (category === 'pending') {
      status.classList.add('winter-pending-status');
    }

    if (statuses[n] === 'available') {
      name.classList.add('winter-rgb-name');
      row.classList.add('winter-available-row');
    }

    const fav = document.createElement('button');
    fav.className = 'tiny';
    fav.textContent = '☆';
    fav.title = 'Favorite';

    fav.onclick = async () => {
      try {
        await api('favorites', {
          platform: $('platform').value,
          username: n
        });

        await loadFavorites();
      } catch (e) {
        notice(e.message);
      }
    };

    const open = document.createElement('a');
    open.textContent = '↗';
    open.href = link($('platform').value, n);
    open.target = '_blank';
    open.rel = 'noopener noreferrer';

    row.append(name, status, fav, open);

    if (
      searchId &&
      statuses[n] !== 'skipped'
    ) {
      const select = document.createElement('select');

      for (const x of [
        'unknown',
        'available',
        'claimed'
      ]) {
        const option = document.createElement('option');
        option.value = x;
        option.textContent =
          statusLabel(x) + ' (manual)';
        select.append(option);
      }

      select.value =
        ['available', 'claimed'].includes(statuses[n])
          ? statuses[n]
          : 'unknown';

      select.disabled = checkingInProgress;

      select.onchange = async () => {
        try {
          await api('mark', {
            search_id: searchId,
            username: n,
            status: select.value
          });

          statuses[n] = select.value;
          sources[n] = 'manual';
          checkReasons[n] =
            'Manually marked by user';

          saveCurrentResults();
          render();
        } catch (e) {
          notice(e.message);
          render();
        }
      };

      row.append(select);
    }

    if (sources[n] === 'manual') {
      const tag = document.createElement('small');
      tag.className = 'muted';
      tag.textContent = 'User-reported';
      row.append(tag);
    }

    if (checkReasons[n]) {
      const detail = document.createElement('small');
      detail.className = 'winter-reason muted';

      detail.textContent =
        friendlyReason(checkReasons[n]) +
        (
          sources[n] === 'automatic'
            ? ' · Automatic check'
            : ''
        );

      row.append(detail);
    }

    list.append(row);
  }

  if (!filtered.length && names.length) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent =
      'No usernames in this category yet.';
    list.append(p);
  }

  if (filtered.length > 500) {
    const p = document.createElement('p');
    p.className = 'muted';

    p.textContent =
      'Showing first 500 of ' +
      filtered.length +
      ' matching usernames.';

    list.append(p);
  }

  $('generatedStat').textContent =
    names.length.toLocaleString();
}

// =====================================
// FAVORITES
// =====================================

async function loadFavorites() {
  const favorites = await api('favorites');

  $('favStat').textContent = favorites.length;

  const box = $('favoriteList');
  box.replaceChildren();

  for (const x of favorites) {
    const row = document.createElement('div');
    row.className = 'result';

    const label = document.createElement('span');
    label.textContent =
      x.username + ' · ' + x.platform;

    const button = document.createElement('button');
    button.className = 'tiny';
    button.textContent = 'Remove';

    button.onclick = async () => {
      try {
        await api(
          'favorites/' +
          encodeURIComponent(x.platform) +
          '/' +
          encodeURIComponent(x.username),
          {},
          'DELETE'
        );

        await loadFavorites();
      } catch (e) {
        notice(e.message);
      }
    };

    row.append(label, button);
    box.append(row);
  }
}

// =====================================
// HISTORY
// =====================================

async function loadHistory() {
  const history = await api('history');

  const box = $('historyList');
  box.replaceChildren();

  for (const s of history) {
    const row = document.createElement('div');
    row.className = 'result';

    row.textContent =
      '#' + s.id +
      ' · ' + s.platform +
      ' · ' +
      String(s.created || '')
        .slice(0, 19)
        .replace('T', ' ') +
      ' · ' + s.results.length + ' shown';

    box.append(row);
  }
}

// =====================================
// PRESETS
// =====================================

async function loadPresets() {
  const presets = await api('presets');

  const box = $('presetList');
  box.replaceChildren();

  for (const p of presets) {
    const button = document.createElement('button');

    button.className = 'secondary';
    button.textContent = p.name;

    button.onclick = () => {
      const c = p.config;

      if (platforms[c.platform]) {
        $('platform').value = c.platform;
      }

      $('amount').value = c.amount || 100;
      $('prefix').value = c.prefix || '';
      $('suffix').value = c.suffix || '';

      updateLengths(
        (c.lengths || []).map(String)
      );

      checkboxes(
        'types',
        types,
        c.types || []
      );

      checkboxes(
        'characters',
        chars,
        c.characters || []
      );

      names = [];
      statuses = {};
      sources = {};
      checkReasons = {};

      resetChecks();
      render();

      notice('Preset loaded');
    };

    box.append(button);
  }
}

// =====================================
// OWNER DASHBOARD
// =====================================

function formatDate(value) {
  if (!value) return 'Never';

  const date = new Date(value);

  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString();
}

function makeButton(label, className, action) {
  const button = document.createElement('button');

  button.type = 'button';
  button.className = className;
  button.textContent = label;

  button.onclick = async () => {
    button.disabled = true;

    try {
      await action();
    } catch (e) {
      adminNotice(e.message, 'error');
    } finally {
      button.disabled = false;
    }
  };

  return button;
}

function makeEmpty(message) {
  const p = document.createElement('p');
  p.className = 'muted';
  p.textContent = message;
  return p;
}

function licenseStatus(license) {
  if (license.revoked) return 'Revoked';

  if (
    license.expires_at &&
    new Date(license.expires_at).getTime() <= Date.now()
  ) {
    return 'Expired';
  }

  return 'Active';
}

function licenseRow(license) {
  const row = document.createElement('div');
  row.className = 'result';

  const info = document.createElement('span');

  info.textContent =
    '#' + license.id +
    ' · ' +
    (license.username || 'Member') +
    ' · Expires: ' +
    formatDate(license.expires_at);

  const status = licenseStatus(license);
  const badge = document.createElement('strong');

  badge.className =
    'license-status ' +
    (
      status === 'Active'
        ? 'status-active'
        : status === 'Revoked'
          ? 'status-revoked'
          : 'status-expired'
    );

  badge.textContent = status;
  row.append(info, badge);

  if (!license.revoked) {
    row.append(
      makeButton(
        'Renew',
        'tiny secondary',
        async () => {
          const entered = prompt(
            'New expiration date (YYYY-MM-DD).\n' +
            'Leave blank for no expiration:',
            license.expires_at
              ? license.expires_at.slice(0, 10)
              : ''
          );

          if (entered === null) return;

          let expiresAt = null;

          if (entered.trim()) {
            const date = new Date(
              entered.trim() + 'T23:59:59'
            );

            if (Number.isNaN(date.getTime())) {
              throw Error('Invalid expiration date');
            }

            expiresAt = date.toISOString();
          }

          const result = await api(
            'admin/licenses/' +
            license.id +
            '/renew',
            { expires_at: expiresAt }
          );

          await loadAdmin();
          adminNotice(result.message);
        }
      )
    );
  }

  row.append(
    makeButton(
      license.revoked ? 'Reactivate' : 'Revoke',
      license.revoked
        ? 'tiny success-button'
        : 'tiny danger-button',
      async () => {
        const action = license.revoked
          ? 'reactivate'
          : 'revoke';

        if (
          !confirm(
            'Are you sure you want to ' +
            action +
            ' license #' +
            license.id +
            '?'
          )
        ) {
          return;
        }

        const result = await api(
          'admin/licenses/' +
          license.id +
          '/' +
          action,
          {}
        );

        await loadAdmin();
        adminNotice(result.message);
      }
    )
  );

  return row;
}

function renderAdminUsers() {
  const box = $('adminUsers');
  box.replaceChildren();

  const query = $('adminUserSearch')
    .value
    .trim()
    .toLowerCase();

  const users = adminUserCache.filter(u =>
    u.username.toLowerCase().includes(query)
  );

  if (!users.length) {
    box.append(
      makeEmpty('No matching members found.')
    );
    return;
  }

  for (const user of users) {
    const row = document.createElement('div');
    row.className = 'result';

    const info = document.createElement('span');

    info.textContent =
      '#' + user.id +
      ' · ' + user.username +
      (user.is_admin ? ' · Owner' : ' · Member') +
      (user.suspended ? ' · Suspended' : '') +
      ' · Logins: ' + (user.login_count || 0);

    row.append(
      info,
      makeButton(
        'Details',
        'tiny secondary',
        async () => showMemberDetails(user.id)
      )
    );

    if (!user.is_admin) {
      row.append(
        makeButton(
          user.suspended ? 'Unsuspend' : 'Suspend',
          user.suspended
            ? 'tiny success-button'
            : 'tiny danger-button',
          async () => {
            const suspend = !Boolean(
              user.suspended
            );

            if (
              !confirm(
                (suspend ? 'Suspend ' : 'Unsuspend ') +
                user.username +
                '?'
              )
            ) {
              return;
            }

            const result = await api(
              'admin/users/' +
              user.id +
              '/suspension',
              { suspended: suspend }
            );

            await loadAdmin();
            adminNotice(result.message);
          }
        )
      );

      row.append(
        makeButton(
          'Reset Password',
          'tiny secondary',
          async () => {
            const password = prompt(
              'Enter a NEW password for ' +
              user.username +
              ' (minimum 12 characters):'
            );

            if (password === null) return;

            if (password.length < 12) {
              throw Error(
                'Password must be at least 12 characters'
              );
            }

            if (
              !confirm(
                'Reset password for ' +
                user.username +
                '?'
              )
            ) {
              return;
            }

            const result = await api(
              'admin/users/' +
              user.id +
              '/password',
              { password }
            );

            adminNotice(result.message);
          }
        )
      );

      row.append(
        makeButton(
          'Delete User',
          'tiny danger-button',
          async () => {
            if (
              !confirm(
                'Permanently delete "' +
                user.username +
                '"?'
              )
            ) {
              return;
            }

            const result = await api(
              'admin/users/' + user.id,
              {},
              'DELETE'
            );

            if (
              selectedAdminMember === user.id
            ) {
              selectedAdminMember = null;
              $('adminMemberDetails').hidden = true;
            }

            $('newLicenseBox').hidden = true;
            $('newLicenseKey').value = '';

            await loadAdmin();
            adminNotice(result.message);
          }
        )
      );
    }

    box.append(row);
  }
}

async function showMemberDetails(userId) {
  const data = await api(
    'admin/users/' + userId
  );

  selectedAdminMember = userId;

  $('adminMemberDetails').hidden = false;
  $('adminMemberTitle').textContent =
    'Member: ' + data.member.username;

  const member = data.member;

  $('adminMemberInfo').replaceChildren();

  const info = document.createElement('p');
  info.className = 'muted';

  info.textContent =
    'Account ID: ' + member.id +
    ' · Created: ' + formatDate(member.created_at) +
    ' · Last login: ' + formatDate(member.last_login) +
    ' · Total logins: ' + member.login_count +
    ' · Status: ' +
    (member.suspended ? 'Suspended' : 'Enabled');

  $('adminMemberInfo').append(info);

  const licensesBox = $('adminMemberLicenses');
  licensesBox.replaceChildren();

  if (!data.licenses.length) {
    licensesBox.append(
      makeEmpty('No licenses for this member.')
    );
  } else {
    for (const license of data.licenses) {
      licensesBox.append(
        licenseRow({
          ...license,
          username: member.username
        })
      );
    }
  }

  const activityBox = $('adminMemberActivity');
  activityBox.replaceChildren();

  if (!data.activity.length) {
    activityBox.append(
      makeEmpty('No activity recorded yet.')
    );
  } else {
    for (const event of data.activity) {
      const row = document.createElement('div');
      row.className = 'result';

      row.textContent =
        formatDate(event.created) +
        ' · ' + event.action +
        (event.details ? ' · ' + event.details : '');

      activityBox.append(row);
    }
  }

  $('adminMemberDetails').scrollIntoView({
    behavior: 'smooth',
    block: 'start'
  });
}

async function loadAdminStats() {
  const s = await api('admin/stats');

  for (const [id, key] of [
    ['adminTotalUsers', 'total_users'],
    ['adminActiveLicenses', 'active_licenses'],
    ['adminGenerated', 'generated_usernames'],
    ['adminSuspended', 'suspended_users'],
    ['adminChecks', 'checked_usernames'],
    ['adminRevoked', 'revoked_licenses']
  ]) {
    $(id).textContent =
      Number(s[key]).toLocaleString();
  }
}

async function loadAdminActivity() {
  const limit =
    Number($('adminActivityLimit').value) || 50;

  const events = await api(
    'admin/activity?limit=' + limit
  );

  const box = $('adminActivity');
  box.replaceChildren();

  if (!events.length) {
    box.append(
      makeEmpty('No activity recorded yet.')
    );
    return;
  }

  for (const event of events) {
    const row = document.createElement('div');
    row.className = 'result';

    const info = document.createElement('span');

    info.textContent =
      formatDate(event.created) +
      ' · ' + event.username +
      ' · ' + event.action +
      (event.details ? ' · ' + event.details : '');

    row.append(info);
    box.append(row);
  }
}

async function loadAdmin() {
  if (!isAdmin) return;

  const [users, licenses] = await Promise.all([
    api('admin/users'),
    api('admin/licenses')
  ]);

  adminUserCache = users;

  const select = $('licenseUser');
  const previous = select.value;

  select.replaceChildren();

  for (const user of users) {
    const option = document.createElement('option');

    option.value = user.id;
    option.textContent =
      user.username +
      (user.is_admin ? ' (Owner)' : '');

    select.append(option);
  }

  if (
    users.some(
      u => String(u.id) === previous
    )
  ) {
    select.value = previous;
  }

  renderAdminUsers();

  const box = $('adminLicenses');
  box.replaceChildren();

  if (!licenses.length) {
    box.append(
      makeEmpty('No licenses found.')
    );
  } else {
    for (const license of licenses) {
      box.append(licenseRow(license));
    }
  }

  await Promise.all([
    loadAdminStats(),
    loadAdminActivity()
  ]);

  if (selectedAdminMember !== null) {
    if (
      users.some(
        u => u.id === selectedAdminMember
      )
    ) {
      await showMemberDetails(
        selectedAdminMember
      );
    } else {
      selectedAdminMember = null;
      $('adminMemberDetails').hidden = true;
    }
  }
}

// =====================================
// OWNER DASHBOARD EVENTS
// =====================================

$('adminUserSearch').oninput =
  renderAdminUsers;

$('adminActivityLimit').onchange = async () => {
  try {
    await loadAdminActivity();
  } catch (e) {
    adminNotice(e.message, 'error');
  }
};

$('adminCloseMember').onclick = () => {
  selectedAdminMember = null;
  $('adminMemberDetails').hidden = true;
};

$('createUserForm').onsubmit = async e => {
  e.preventDefault();

  const username =
    $('newUsername').value.trim();

  const password =
    $('newPassword').value;

  const button =
    e.submitter ||
    $('createUserForm').querySelector('button');

  if (
    adminUserCache.some(
      u =>
        u.username.toLowerCase() ===
        username.toLowerCase()
    )
  ) {
    adminNotice(
      'Username already exists!',
      'error'
    );
    return;
  }

  button.disabled = true;

  try {
    const user = await api(
      'admin/users',
      { username, password }
    );

    $('newUsername').value = '';
    $('newPassword').value = '';

    await loadAdmin();

    $('licenseUser').value =
      String(user.id);

    adminNotice(
      user.message ||
      'Member successfully created!'
    );
  } catch (e) {
    adminNotice(e.message, 'error');
  } finally {
    button.disabled = false;
  }
};

$('createLicenseForm').onsubmit = async e => {
  e.preventDefault();

  const button =
    e.submitter ||
    $('createLicenseForm').querySelector('button');

  button.disabled = true;

  try {
    const expiry = $('licenseExpiry').value;

    const result = await api(
      'admin/users/' +
      $('licenseUser').value +
      '/licenses',
      {
        expires_at: expiry
          ? new Date(expiry).toISOString()
          : null
      }
    );

    $('newLicenseKey').value =
      result.license_key;

    $('newLicenseBox').hidden = false;

    await loadAdmin();

    adminNotice(
      result.message ||
      'License successfully generated!'
    );
  } catch (e) {
    adminNotice(e.message, 'error');
  } finally {
    button.disabled = false;
  }
};

$('copyLicense').onclick = async () => {
  try {
    await navigator.clipboard.writeText(
      $('newLicenseKey').value
    );
    adminNotice('License key copied!');
  } catch {
    adminNotice(
      'Could not copy the license key.',
      'error'
    );
  }
};

$('refreshAdmin').onclick = async () => {
  try {
    await loadAdmin();
    adminNotice('Dashboard refreshed!');
  } catch (e) {
    adminNotice(e.message, 'error');
  }
};

// =====================================
// AUTO CHECK TOGGLE
// =====================================

function installAutoCheckToggle() {
  if ($('autoCheck')) return;

  const button = $('generateBtn');
  const wrapper = document.createElement('label');

  wrapper.className = 'auto-check-option';

  Object.assign(wrapper.style, {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '12px 14px',
    margin: '12px 0',
    border: '1px solid rgba(150,150,170,.3)',
    borderRadius: '12px',
    cursor: 'pointer'
  });

  const input = document.createElement('input');

  input.type = 'checkbox';
  input.id = 'autoCheck';
  input.checked = false;

  Object.assign(input.style, {
    width: '20px',
    height: '20px',
    accentColor: '#7c5cff'
  });

  const text = document.createElement('span');
  const title = document.createElement('strong');
  const description = document.createElement('small');

  title.textContent = 'Auto Check';
  description.textContent =
    'Check automatically where supported';

  Object.assign(description.style, {
    display: 'block',
    opacity: '.7',
    marginTop: '3px'
  });

  text.append(title, description);
  wrapper.append(input, text);

  button.parentNode.insertBefore(
    wrapper,
    button
  );
}

// =====================================
// PROGRESS
// =====================================

function installProgress() {
  if ($('winter-v6-progress')) return;

  const box = document.createElement('div');
  box.id = 'winter-v6-progress';
  box.hidden = true;

  const label = document.createElement('div');
  label.id = 'winter-v6-label';
  label.textContent = 'Ready';

  const track = document.createElement('div');
  track.id = 'winter-v6-track';

  const fill = document.createElement('div');
  fill.id = 'winter-v6-fill';

  track.append(fill);

  const resume = document.createElement('button');

  resume.id = 'winter-v6-resume';
  resume.type = 'button';
  resume.className = 'secondary';
  resume.textContent =
    'Resume unfinished checks';
  resume.hidden = true;

  resume.onclick = () => {
    if (!checkRun) return;

    const wait = Math.ceil(
      ((checkRun.resumeAt || 0) - Date.now()) / 1000
    );

    if (wait > 0) {
      notice(
        'Provider cooldown: ' +
        wait +
        ' seconds remaining.'
      );
      return;
    }

    checkRun.autoResumes = 0;
    checkRun.resumeAt = 0;

    saveCheckRun();

    runChecks().catch(
      e => notice(
        'Resume error: ' + e.message
      )
    );
  };

  box.append(label, track, resume);

  $('checkBtn').parentNode.insertBefore(
    box,
    $('checkBtn').nextSibling
  );
}

function progress(processed, total, label) {
  $('winter-v6-progress').hidden = false;

  const percent = total
    ? Math.round(processed / total * 100)
    : 100;

  $('winter-v6-label').textContent =
    label +
    ' · ' +
    processed.toLocaleString() +
    '/' +
    total.toLocaleString() +
    ' (' +
    percent +
    '%)';

  $('winter-v6-fill').style.width =
    percent + '%';

  $('checkedStat').textContent =
    processed.toLocaleString();
}

function updateCheckButtons() {
  $('checkBtn').disabled =
    checkingInProgress ||
    generatingInProgress;

  $('generateBtn').disabled =
    checkingInProgress ||
    generatingInProgress;

  if ($('winter-v6-resume')) {
    $('winter-v6-resume').disabled =
      checkingInProgress ||
      generatingInProgress;
  }
}

function sleep(ms) {
  return new Promise(
    resolve => setTimeout(resolve, ms)
  );
}

// =====================================
// RATE-LIMIT COOLDOWNS
// =====================================

function clearResumeTimers() {
  if (resumeTimer !== null) {
    clearTimeout(resumeTimer);
  }

  if (countdownTimer !== null) {
    clearTimeout(countdownTimer);
  }

  resumeTimer = null;
  countdownTimer = null;
}

function rateLimited(reason) {
  return RATE_LIMIT_REASONS.has(reason);
}

function accessBlocked(reason) {
  return ACCESS_BLOCK_REASONS.has(reason);
}

function retryDelay(run, item) {
  const failures = Math.min(
    Number(run.cooldowns || 0) + 1,
    6
  );

  const base =
    run.platform === 'Discord'
      ? 90000
      : 45000;

  const hinted = Number(
    item && item.retry_after
  );

  const providerMs =
    Number.isFinite(hinted) && hinted > 0
      ? Math.min(
          hinted * 1000 + 1000,
          3600000
        )
      : 0;

  return Math.max(
    providerMs,
    Math.min(
      base * (2 ** (failures - 1)),
      900000
    )
  );
}

function scheduleResume() {
  clearResumeTimers();

  if (
    !checkRun ||
    !checkRun.pending.length ||
    !checkRun.resumeAt
  ) {
    return;
  }

  const version = generationVersion;
  const runId = checkRun.id;

  function tick() {
    if (
      !checkRun ||
      generationVersion !== version ||
      checkRun.id !== runId
    ) {
      return;
    }

    const seconds = Math.max(
      0,
      Math.ceil(
        (checkRun.resumeAt - Date.now()) / 1000
      )
    );

    const automatic =
      (checkRun.autoResumes || 0) <
      MAX_AUTO_RESUMES;

    const label =
      'Provider cooldown: ' +
      seconds +
      's · ' +
      checkRun.pending.length +
      ' pending · ' +
      (
        automatic
          ? 'Auto-resume enabled'
          : 'Manual resume required'
      );

    const el = $('winter-v6-label');

    if (el) {
      el.textContent = label;
    }

    if (seconds > 0) {
      countdownTimer = setTimeout(
        tick,
        1000
      );
      return;
    }

    if (!automatic) {
      notice(
        'Cooldown finished. Tap Resume unfinished checks.'
      );
      return;
    }

    if (
      checkingInProgress ||
      generatingInProgress
    ) {
      resumeTimer = setTimeout(
        () => scheduleResume(),
        2000
      );
      return;
    }

    checkRun.autoResumes =
      (checkRun.autoResumes || 0) + 1;

    checkRun.resumeAt = 0;
    saveCheckRun();

    runChecks().catch(
      e => notice(
        'Auto-resume error: ' + e.message
      )
    );
  }

  tick();
}

// =====================================
// RETRIES AND SAVED CHECKS
// =====================================

function temporary(error) {
  return /failed to fetch|load failed|network|timeout|unexpected response|temporarily unavailable/i
    .test(
      String(error.message || error)
    );
}

async function requestWithRetry(path, body) {
  let error;

  for (let i = 0; i < 3; i++) {
    try {
      return await api(path, body);
    } catch (e) {
      error = e;

      if (!temporary(e) || i === 2) {
        throw e;
      }

      await sleep(
        1200 * Math.pow(2, i)
      );
    }
  }

  throw error;
}

function saveCheckRun() {
  try {
    if (checkRun) {
      sessionStorage.setItem(
        'winter_check_run',
        JSON.stringify({
          ...checkRun,
          statuses: { ...statuses },
          reasons: { ...checkReasons },
          sources: { ...sources }
        })
      );
    } else {
      sessionStorage.removeItem(
        'winter_check_run'
      );
    }
  } catch (e) {
    console.warn(e);
  }
}

function saveCurrentResults() {
  if (checkRun) {
    saveCheckRun();
  }
}

function resetChecks() {
  clearResumeTimers();

  checkRun = null;
  saveCheckRun();

  searchId = null;
  resultTab = 'all';

  $('winter-v6-progress').hidden = true;
  $('winter-v6-resume').hidden = true;
}

function counts() {
  return resultCounts();
}

// =====================================
// RUN CHECKS
// =====================================

async function runChecks() {
  if (
    checkingInProgress ||
    generatingInProgress
  ) {
    return;
  }

  if (!checkRun) {
    throw Error('No search to resume');
  }

  if (
    checkRun.platform !==
    $('platform').value
  ) {
    throw Error('Platform changed');
  }

  const remainingCooldown = Math.ceil(
    (
      (checkRun.resumeAt || 0) -
      Date.now()
    ) / 1000
  );

  if (remainingCooldown > 0) {
    notice(
      'Provider cooldown: ' +
      remainingCooldown +
      ' seconds remaining.'
    );

    scheduleResume();
    return;
  }

  const version = generationVersion;
  const runId = checkRun.id;
  const platform = checkRun.platform;
  const resume = $('winter-v6-resume');

  clearResumeTimers();

  checkingInProgress = true;
  updateCheckButtons();

  resume.hidden = true;

  let pausedReason = '';
  let pauseItem = null;
  let stopForAccess = false;

  try {
    while (
      checkRun &&
      checkRun.id === runId &&
      checkRun.pending.length
    ) {
      if (
        version !== generationVersion
      ) {
        return;
      }

      const total =
        checkRun.done.length +
        checkRun.pending.length;

      const size =
        platform === 'Discord'
          ? DISCORD_BATCH_SIZE
          : platform === 'Minecraft'
            ? MINECRAFT_BATCH_SIZE
            : platform === 'Roblox'
              ? ROBLOX_BATCH_SIZE
              : DEFAULT_BATCH_SIZE;

      const batch =
        checkRun.pending.slice(0, size);

      progress(
        checkRun.done.length,
        total,
        'Checking ' + platform
      );

      let response;

      try {
        response = await requestWithRetry(
          'check/auto',
          {
            platform,
            search_id: checkRun.id,
            usernames: batch
          }
        );
      } catch (error) {
        pausedReason =
          'Connection interrupted: ' +
          error.message;

        stopForAccess = true;
        break;
      }

      if (
        version !== generationVersion ||
        !checkRun ||
        checkRun.id !== runId
      ) {
        return;
      }

      const returned = new Map(
        (
          Array.isArray(response.results)
            ? response.results
            : []
        )
          .filter(
            item =>
              item &&
              typeof item.username === 'string'
          )
          .map(
            item => [
              item.username.toLowerCase(),
              item
            ]
          )
      );

      const completed = new Set();

      for (const name of batch) {
        const item = returned.get(
          name.toLowerCase()
        );

        if (!item) {
          pausedReason =
            'Server response was incomplete';

          stopForAccess = true;
          break;
        }

        const reason =
          typeof item.reason === 'string'
            ? item.reason
            : 'no_reason_returned';

        const status =
          [
            'available',
            'claimed',
            'invalid',
            'unknown',
            'skipped'
          ].includes(item.status)
            ? item.status
            : 'unknown';

        statuses[name] = status;
        checkReasons[name] = reason;

        if (rateLimited(reason)) {
          pauseItem = item;
          pausedReason = friendlyReason(reason);

          // Rate-limited names stay Pending.
          continue;
        }

        if (accessBlocked(reason)) {
          stopForAccess = true;
          pausedReason = friendlyReason(reason);

          // Access-denied names need manual retry.
          continue;
        }

        if (
          ['available', 'claimed'].includes(status) &&
          reason !== 'already_recorded'
        ) {
          sources[name] = 'automatic';
        }

        completed.add(name);
      }

      // Save results independently.
      // One rate limit doesn't erase other results.

      if (completed.size) {
        const doneSet = new Set(
          checkRun.done
        );

        for (const name of batch) {
          if (
            completed.has(name) &&
            !doneSet.has(name)
          ) {
            checkRun.done.push(name);
            doneSet.add(name);
          }
        }

        checkRun.pending =
          checkRun.pending.filter(
            n => !completed.has(n)
          );
      }

      saveCurrentResults();
      render();

      progress(
        checkRun.done.length,
        checkRun.done.length +
        checkRun.pending.length,
        'Checking ' + platform
      );

      if (
        pauseItem ||
        stopForAccess
      ) {
        break;
      }

      if (!completed.size) {
        pausedReason =
          'No completed results returned';

        stopForAccess = true;
        break;
      }

      if (
        platform === 'Discord' &&
        checkRun.pending.length
      ) {
        await sleep(DISCORD_DELAY_MS);
      }
    }

    if (
      version !== generationVersion ||
      !checkRun ||
      checkRun.id !== runId
    ) {
      return;
    }

    try {
      await loadHistory();
    } catch (e) {
      console.warn(e);
    }

    const tally = counts();

    if (pauseItem) {
      checkRun.cooldowns =
        (checkRun.cooldowns || 0) + 1;

      checkRun.resumeAt =
        Date.now() +
        retryDelay(
          {
            ...checkRun,
            cooldowns:
              checkRun.cooldowns - 1
          },
          pauseItem
        );

      saveCheckRun();
    } else {
      checkRun.resumeAt = 0;
      saveCheckRun();
    }

    const remaining =
      checkRun.pending.length;

    const intro = remaining
      ? (
        'Check paused. ' +
        remaining +
        ' pending. ' +
        (pausedReason ? pausedReason + '. ' : '')
      )
      : 'Check finished. ';

    const validationOnly =
      [
        'Instagram',
        'TikTok',
        'Telegram'
      ].includes(platform)
        ? ' This platform currently supports format validation only.'
        : '';

    notice(
      intro +
      'Available: ' + tally.available +
      ' · Taken: ' + tally.claimed +
      ' · Invalid: ' + tally.invalid +
      ' · Unknown: ' + tally.unknown +
      ' · Pending: ' + tally.pending +
      '.' +
      validationOnly
    );

    resume.hidden = !remaining;

    if (!remaining) {
      clearResumeTimers();

      checkRun = null;
      saveCheckRun();
    }

  } finally {
    checkingInProgress = false;

    updateCheckButtons();
    render();

    if (
      checkRun &&
      checkRun.pending.length &&
      pauseItem &&
      !stopForAccess
    ) {
      scheduleResume();
    }
  }
}

// =====================================
// CREATE A NEW CHECK
// =====================================

async function beginChecks() {
  if (
    checkingInProgress ||
    generatingInProgress
  ) {
    return;
  }

  if (!names.length) {
    throw Error('Generate usernames first');
  }

  if (
    checkRun &&
    checkRun.pending.length &&
    checkRun.platform === $('platform').value
  ) {
    await runChecks();
    return;
  }

  checkingInProgress = true;
  updateCheckButtons();

  let data;

  try {
    notice(
      'Creating availability search...'
    );

    data = await requestWithRetry(
      'check',
      {
        platform: $('platform').value,
        usernames: [...names]
      }
    );
  } finally {
    checkingInProgress = false;
    updateCheckButtons();
  }

  searchId = data.search_id;

  statuses = Object.fromEntries(
    data.results.map(
      x => [x.username, x.status]
    )
  );

  checkReasons = {};
  sources = {};

  checkRun = {
    id: searchId,
    platform: $('platform').value,

    pending: data.results
      .filter(
        x => x.status === 'unknown'
      )
      .map(
        x => x.username
      ),

    done: data.results
      .filter(
        x => x.status !== 'unknown'
      )
      .map(
        x => x.username
      ),

    cooldowns: 0,
    autoResumes: 0,
    resumeAt: 0,

    statuses: { ...statuses },
    reasons: {},
    sources: {}
  };

  saveCheckRun();
  render();

  if (!checkRun.pending.length) {
    progress(
      0,
      0,
      'No usernames to check'
    );

    notice(
      'No unprocessed usernames.'
    );

    checkRun = null;
    saveCheckRun();

    return;
  }

  await runChecks();
}

// =====================================
// APP INITIALIZATION
// =====================================

async function start() {
  try {
    const me = await api('me');

    $('who').textContent = me.username;

    isAdmin = Boolean(me.is_admin);

    $('ownerNav').hidden = !isAdmin;
    $('owner').hidden = !isAdmin;

    platforms = await api('platforms');

    $('platform').replaceChildren(
      ...Object.keys(platforms).map(name => {
        const option =
          document.createElement('option');

        option.value = name;
        option.textContent = name;

        return option;
      })
    );

    updateLengths(['3', '4', '5']);

    checkboxes(
      'types',
      types,
      [
        'Random letters',
        'Letters + numbers'
      ]
    );

    checkboxes(
      'characters',
      chars,
      [
        'Lowercase',
        'Numbers'
      ]
    );

    installAutoCheckToggle();
    installProgress();
    installStyles();
    installResultTabs();

    showApp();

    try {
      const saved = JSON.parse(
        sessionStorage.getItem(
          'winter_check_run'
        ) || 'null'
      );

      if (
        saved &&
        platforms[saved.platform] &&
        Array.isArray(saved.pending) &&
        Array.isArray(saved.done)
      ) {
        $('platform').value = saved.platform;

        updateLengths(['3', '4', '5']);

        checkRun = saved;
        searchId = saved.id;

        names = [
          ...saved.done,
          ...saved.pending
        ];

        statuses = {
          ...Object.fromEntries(
            names.map(
              n => [n, 'unknown']
            )
          ),
          ...(saved.statuses || {})
        };

        sources = {
          ...(saved.sources || {})
        };

        checkReasons = {
          ...(saved.reasons || {})
        };

        $('winter-v6-resume').hidden =
          !saved.pending.length;

        progress(
          saved.done.length,
          names.length,
          'Paused ' + saved.platform
        );

        render();

        if (
          saved.resumeAt &&
          saved.pending.length
        ) {
          scheduleResume();
        }
      }
    } catch (e) {
      console.warn(
        'Saved check could not be restored',
        e
      );
    }

    await Promise.all([
      loadFavorites(),
      loadHistory(),
      loadPresets(),
      ...(isAdmin ? [loadAdmin()] : [])
    ]);

  } catch (e) {
    console.error(
      'Initialization failed:',
      e
    );

    showLogin();
  }
}

// =====================================
// LOGIN / LOGOUT
// =====================================

$('loginForm').onsubmit = async e => {
  e.preventDefault();

  const button = $('loginForm')
    .querySelector('button[type="submit"]');

  button.disabled = true;
  $('loginError').textContent = '';

  try {
    const remember =
      $('rememberLogin').checked;

    const data = await api(
      'login',
      {
        username: $('user').value,
        password: $('password').value,
        license_key: $('key').value,
        remember
      }
    );

    if (remember) {
      token = '';

      sessionStorage.removeItem(
        'winter_token'
      );
    } else {
      token = data.token || '';

      if (!token) {
        throw Error(
          'Login response did not include a session'
        );
      }

      sessionStorage.setItem(
        'winter_token',
        token
      );
    }

    $('password').value = '';
    $('key').value = '';

    await start();

  } catch (e) {
    $('loginError').textContent = e.message;
  } finally {
    button.disabled = false;
  }
};

$('logout').onclick = async () => {
  try {
    await api('logout', {});
  } catch (e) {
    console.error(e);
  }

  resetChecks();
  showLogin();
};

// =====================================
// PLATFORM CHANGES
// =====================================

$('platform').onchange = () => {
  if (
    checkingInProgress ||
    generatingInProgress
  ) {
    return;
  }

  generationVersion++;

  names = [];
  statuses = {};
  sources = {};
  checkReasons = {};

  resetChecks();

  updateLengths(['3', '4', '5']);

  $('checkedStat').textContent = '0';

  render();

  notice(
    'Platform changed. Generate a new batch.'
  );
};

// =====================================
// GENERATE
// =====================================

$('generateBtn').onclick = async () => {
  if (
    generatingInProgress ||
    checkingInProgress
  ) {
    return;
  }

  generatingInProgress = true;
  updateCheckButtons();

  let shouldAuto = false;

  try {
    notice(
      'Generating usernames...'
    );

    const data = await api(
      'generate',
      config()
    );

    generationVersion++;

    names = data.usernames;
    statuses = {};
    sources = {};
    checkReasons = {};

    resetChecks();

    $('checkedStat').textContent = '0';
    render();

    notice(
      'Generated ' +
      data.generated.toLocaleString() +
      ' unique usernames' +
      (
        data.generated < data.requested
          ? ' (combination space exhausted)'
          : ''
      ) +
      '.'
    );

    shouldAuto = Boolean(
      $('autoCheck')?.checked &&
      names.length
    );

  } catch (e) {
    notice(
      'Error: ' + e.message
    );
  } finally {
    generatingInProgress = false;
    updateCheckButtons();
  }

  if (shouldAuto) {
    try {
      await beginChecks();
    } catch (e) {
      notice(
        'Checking error: ' + e.message
      );
    }
  }
};

$('checkBtn').onclick = async () => {
  try {
    await beginChecks();
  } catch (e) {
    notice(
      'Checking error: ' + e.message
    );
  }
};

// =====================================
// PRESETS
// =====================================

$('savePreset').onclick = async () => {
  const name = prompt(
    'Preset name:'
  );

  if (!name) return;

  try {
    await api(
      'presets',
      {
        name,
        config: config()
      }
    );

    await loadPresets();

    notice(
      'Preset saved'
    );
  } catch (e) {
    notice(e.message);
  }
};

// =====================================
// FILTERING AND EXPORTS
// =====================================

$('filter').oninput = render;
$('sort').onchange = render;

$('copy').onclick = () => {
  const selected = sortedNames();

  if (!selected.length) {
    notice(
      'No usernames in this tab to copy'
    );
    return;
  }

  navigator.clipboard.writeText(
    selected.join('\n')
  ).then(
    () => notice(
      'Copied ' +
      selected.length +
      ' usernames from ' +
      resultTab +
      ' tab'
    ),

    () => notice(
      'Unable to copy usernames'
    )
  );
};

$('txt').onclick = () => {
  download(
    'txt',
    sortedNames().join('\n'),
    'text/plain'
  );
};

$('csv').onclick = () => {
  download(
    'csv',
    'username,status,platform\n' +
    sortedNames()
      .map(n =>
        [
          n,
          statuses[n] || 'not checked',
          $('platform').value
        ]
          .map(x =>
            '"' +
            String(x).replaceAll('"', '""') +
            '"'
          )
          .join(',')
      )
      .join('\n'),
    'text/csv'
  );
};

$('json').onclick = () => {
  download(
    'json',
    JSON.stringify(
      sortedNames().map(n => ({
        username: n,
        status:
          statuses[n] || 'not checked',
        platform:
          $('platform').value
      })),
      null,
      2
    ),
    'application/json'
  );
};

// =====================================
// WORDLIST
// =====================================

$('wordlist').onchange = async e => {
  const file = e.target.files[0];

  if (!file) return;

  if (file.size > 2000000) {
    notice(
      'Wordlist must be under 2 MB'
    );
    return;
  }

  wordlist = (
    await file.text()
  )
    .split(/[\r\n,]+/)
    .map(x => x.trim())
    .filter(Boolean)
    .slice(0, 20000);

  notice(
    'Imported ' +
    wordlist.length +
    ' words'
  );
};

// =====================================
// RESUME WHEN PAGE BECOMES ACTIVE
// =====================================

document.addEventListener(
  'visibilitychange',
  () => {
    if (
      !document.hidden &&
      checkRun &&
      checkRun.resumeAt
    ) {
      scheduleResume();
    }
  }
);

// =====================================
// KEYBOARD SHORTCUT
// =====================================

document.addEventListener(
  'keydown',
  e => {
    if (
      (e.ctrlKey || e.metaKey) &&
      e.key === 'Enter' &&
      !$('app').hidden
    ) {
      e.preventDefault();
      $('generateBtn').click();
    }
  }
);

// =====================================
// START WINTER.CC
// =====================================

start();
