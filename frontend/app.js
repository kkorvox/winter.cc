
const $ = id => document.getElementById(id);

let token = sessionStorage.getItem('winter_token') || '';
let names = [];
let statuses = {};
let searchId = null;
let wordlist = null;
let platforms = {};
let isAdmin = false;
let adminUserCache = [];
let selectedAdminMember = null;
let checkingInProgress = false;
let generatingInProgress = false;
let generationVersion = 0;
let activeCheckVersion = 0;

const types = [
  'Random letters', 'Letters + numbers',
  'Real words / OG', 'Pronounceable',
  'Aesthetic', 'Numbers only'
];

const chars = [
  'Lowercase', 'Uppercase', 'Numbers',
  'Underscores', 'Periods'
];

async function api(path, body, method = 'POST') {
  const response = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: 'Bearer ' + token } : {})
    },
    ...(body === undefined ? {} : {
      body: JSON.stringify(body)
    })
  });

  let data;

  try {
    data = await response.json();
  } catch {
    throw Error('Server returned an unexpected response');
  }

  if (!response.ok) {
    if (response.status === 401 && path !== 'login') {
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
    (kind === 'error'
      ? 'feedback-error'
      : 'feedback-success');
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
  $(id).replaceChildren(...values.map(value => {
    const label = document.createElement('label');
    label.className = 'check';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = value;
    checkbox.checked = selected.includes(value);

    label.append(
      checkbox,
      document.createTextNode(' ' + value)
    );

    return label;
  }));
}

function link(platform, name) {
  const n = encodeURIComponent(name);

  return {
    Discord: 'https://discord.com/',
    Roblox: 'https://www.roblox.com/users/profile',
    Instagram: 'https://www.instagram.com/' + n + '/',
    TikTok: 'https://www.tiktok.com/@' + n,
    Telegram: 'https://t.me/' + n,
    Minecraft: 'https://www.minecraft.net/'
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
    '.' + ext;

  a.click();

  setTimeout(() => URL.revokeObjectURL(url), 500);
}

function sortedNames() {
  const arr = names.filter(n =>
    n.toLowerCase().includes(
      $('filter').value.toLowerCase()
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
      arr.sort((a, b) => a.length - b.length);
      break;
  }

  return arr;
}

function statusLabel(status) {
  switch (status) {
    case 'available':
      return 'Available';
    case 'claimed':
      return 'Taken';
    case 'unknown':
      return 'Unknown';
    case 'skipped':
      return 'Skipped';
    default:
      return 'Not checked';
  }
}

function render() {
  const list = $('resultList');
  list.replaceChildren();

  const filtered = sortedNames();
  const shown = filtered.slice(0, 500);

  for (const n of shown) {
    const row = document.createElement('div');
    row.className = 'result';

    const name = document.createElement('code');
    name.textContent = n;

    const status = document.createElement('span');
    status.className = 'status';
    status.textContent = statusLabel(statuses[n]);

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
    open.title = 'Open profile / platform';
    open.href = link($('platform').value, n);
    open.target = '_blank';
    open.rel = 'noopener noreferrer';

    row.append(name, status, fav, open);

    if (searchId && statuses[n] !== 'skipped') {
      const select = document.createElement('select');

      for (const x of [
        'unknown', 'available', 'claimed'
      ]) {
        const option = document.createElement('option');
        option.value = x;
        option.textContent = x + ' (manual)';
        select.append(option);
      }

      select.value = statuses[n] || 'unknown';
      select.disabled = checkingInProgress;

      select.onchange = async () => {
        const chosen = select.value;

        try {
          await api('mark', {
            search_id: searchId,
            username: n,
            status: chosen
          });

          statuses[n] = chosen;
          render();
        } catch (e) {
          notice(e.message);
          render();
        }
      };

      row.append(select);
    }

    list.append(row);
  }

  if (filtered.length > 500) {
    const p = document.createElement('p');
    p.className = 'muted';

    p.textContent =
      'Showing first 500 filtered results. ' +
      'Export includes all ' +
      names.length + ' names.';

    list.append(p);
  }

  $('generatedStat').textContent =
    names.length.toLocaleString();
}

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
      String(s.created || '').slice(0, 19).replace('T', ' ') +
      ' · ' + s.results.length + ' shown';

    box.append(row);
  }
}

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

      $('platform').value = c.platform;
      $('amount').value = c.amount;
      $('prefix').value = c.prefix;
      $('suffix').value = c.suffix;

      checkboxes(
        'lengths',
        Array.from(
          { length: 31 },
          (_, i) => String(i + 2)
        ),
        (c.lengths || []).map(String)
      );

      checkboxes('types', types, c.types || []);
      checkboxes('characters', chars, c.characters || []);

      notice('Preset loaded');
    };

    box.append(button);
  }
}

// OWNER DASHBOARD V3

function formatDate(value) {
  if (!value) return 'Never';

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return date.toLocaleString();
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
    ' · ' + (license.username || 'Member') +
    ' · Expires: ' + formatDate(license.expires_at);

  const status = licenseStatus(license);

  const badge = document.createElement('strong');
  badge.className =
    'license-status ' +
    (status === 'Active'
      ? 'status-active'
      : status === 'Revoked'
        ? 'status-revoked'
        : 'status-expired');
  badge.textContent = status;

  row.append(info, badge);

  if (!license.revoked) {
    row.append(
      makeButton('Renew', 'tiny secondary', async () => {
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
          'admin/licenses/' + license.id + '/renew',
          { expires_at: expiresAt }
        );

        await loadAdmin();
        adminNotice(result.message);
      })
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

        if (!confirm(
          'Are you sure you want to ' +
          action + ' license #' + license.id + '?'
        )) return;

        const result = await api(
          'admin/licenses/' +
          license.id + '/' + action,
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
    .value.trim().toLowerCase();

  const users = adminUserCache.filter(user =>
    user.username.toLowerCase().includes(query)
  );

  if (!users.length) {
    box.append(makeEmpty('No matching members found.'));
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

    row.append(info);

    row.append(
      makeButton('Details', 'tiny secondary', async () => {
        await showMemberDetails(user.id);
      })
    );

    if (!user.is_admin) {
      row.append(
        makeButton(
          user.suspended ? 'Unsuspend' : 'Suspend',
          user.suspended
            ? 'tiny success-button'
            : 'tiny danger-button',
          async () => {
            const suspend = !Boolean(user.suspended);

            if (!confirm(
              (suspend ? 'Suspend ' : 'Unsuspend ') +
              user.username + '?'
            )) return;

            const result = await api(
              'admin/users/' + user.id + '/suspension',
              { suspended: suspend }
            );

            await loadAdmin();
            adminNotice(result.message);
          }
        )
      );

      row.append(
        makeButton('Reset Password', 'tiny secondary', async () => {
          const password = prompt(
            'Enter a NEW password for ' +
            user.username + ' (minimum 12 characters):'
          );

          if (password === null) return;

          if (password.length < 12) {
            throw Error(
              'Password must be at least 12 characters'
            );
          }

          if (!confirm(
            'Reset password for ' + user.username +
            '? Their existing sessions will be signed out.'
          )) return;

          const result = await api(
            'admin/users/' + user.id + '/password',
            { password }
          );

          adminNotice(result.message);
        })
      );

      row.append(
        makeButton('Delete User', 'tiny danger-button', async () => {
          if (!confirm(
            'Permanently delete "' + user.username +
            '"?\n\nThis removes their account, licenses, ' +
            'favorites, history, and saved data.'
          )) return;

          const result = await api(
            'admin/users/' + user.id,
            {},
            'DELETE'
          );

          if (selectedAdminMember === user.id) {
            selectedAdminMember = null;
            $('adminMemberDetails').hidden = true;
          }

          $('newLicenseBox').hidden = true;
          $('newLicenseKey').value = '';

          await loadAdmin();
          adminNotice(result.message);
        })
      );
    }

    box.append(row);
  }
}

async function showMemberDetails(userId) {
  const data = await api('admin/users/' + userId);

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
    licensesBox.append(makeEmpty('No licenses for this member.'));
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
    activityBox.append(makeEmpty('No activity recorded yet.'));
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
  const stats = await api('admin/stats');

  $('adminTotalUsers').textContent =
    Number(stats.total_users).toLocaleString();

  $('adminActiveLicenses').textContent =
    Number(stats.active_licenses).toLocaleString();

  $('adminGenerated').textContent =
    Number(stats.generated_usernames).toLocaleString();

  $('adminSuspended').textContent =
    Number(stats.suspended_users).toLocaleString();

  $('adminChecks').textContent =
    Number(stats.checked_usernames).toLocaleString();

  $('adminRevoked').textContent =
    Number(stats.revoked_licenses).toLocaleString();
}

async function loadAdminActivity() {
  const limit = Number($('adminActivityLimit').value) || 50;

  const events = await api(
    'admin/activity?limit=' + limit
  );

  const box = $('adminActivity');
  box.replaceChildren();

  if (!events.length) {
    box.append(makeEmpty('No activity recorded yet.'));
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

  const userSelect = $('licenseUser');
  const previousSelection = userSelect.value;

  userSelect.replaceChildren();

  for (const user of users) {
    const option = document.createElement('option');
    option.value = user.id;
    option.textContent =
      user.username +
      (user.is_admin ? ' (Owner)' : '');

    userSelect.append(option);
  }

  if (users.some(
    user => String(user.id) === previousSelection
  )) {
    userSelect.value = previousSelection;
  }

  renderAdminUsers();

  const licensesBox = $('adminLicenses');
  licensesBox.replaceChildren();

  if (!licenses.length) {
    licensesBox.append(makeEmpty('No licenses found.'));
  } else {
    for (const license of licenses) {
      licensesBox.append(licenseRow(license));
    }
  }

  await Promise.all([
    loadAdminStats(),
    loadAdminActivity()
  ]);

  if (selectedAdminMember !== null) {
    const stillExists = users.some(
      user => user.id === selectedAdminMember
    );

    if (stillExists) {
      await showMemberDetails(selectedAdminMember);
    } else {
      selectedAdminMember = null;
      $('adminMemberDetails').hidden = true;
    }
  }
}

$('adminUserSearch').oninput = renderAdminUsers;

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

  const username = $('newUsername').value.trim();
  const password = $('newPassword').value;

  const button =
    e.submitter ||
    $('createUserForm').querySelector('button');

  if (adminUserCache.some(
    user => user.username.toLowerCase() ===
      username.toLowerCase()
  )) {
    adminNotice('Username already exists!', 'error');
    return;
  }

  button.disabled = true;

  try {
    const user = await api('admin/users', {
      username,
      password
    });

    $('newUsername').value = '';
    $('newPassword').value = '';

    await loadAdmin();

    $('licenseUser').value = String(user.id);

    adminNotice(
      user.message || 'Member successfully created!'
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

    const expiresAt = expiry
      ? new Date(expiry).toISOString()
      : null;

    const result = await api(
      'admin/users/' +
      $('licenseUser').value +
      '/licenses',
      { expires_at: expiresAt }
    );

    $('newLicenseKey').value = result.license_key;
    $('newLicenseBox').hidden = false;

    await loadAdmin();

    adminNotice(
      result.message || 'License successfully generated!'
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

// AUTO CHECK UI

function installAutoCheckToggle() {
  if ($('autoCheck')) return;

  const generateButton = $('generateBtn');

  const wrapper = document.createElement('label');
  wrapper.className = 'auto-check-option';

  wrapper.style.display = 'flex';
  wrapper.style.alignItems = 'center';
  wrapper.style.gap = '12px';
  wrapper.style.padding = '12px 14px';
  wrapper.style.margin = '12px 0';
  wrapper.style.border = '1px solid rgba(150,150,170,.3)';
  wrapper.style.borderRadius = '12px';
  wrapper.style.cursor = 'pointer';

  const input = document.createElement('input');
  input.type = 'checkbox';
  input.id = 'autoCheck';
  input.checked = false;
  input.style.width = '20px';
  input.style.height = '20px';
  input.style.accentColor = '#7c5cff';

  const text = document.createElement('span');

  const title = document.createElement('strong');
  title.textContent = 'Auto Check';

  const description = document.createElement('small');
  description.textContent =
    'Automatically check availability after generating';
  description.style.display = 'block';
  description.style.opacity = '.7';
  description.style.marginTop = '3px';

  text.append(title, description);
  wrapper.append(input, text);

  generateButton.parentNode.insertBefore(
    wrapper,
    generateButton
  );
}

function updateCheckButtons() {
  $('checkBtn').disabled =
    checkingInProgress || generatingInProgress;

  $('generateBtn').disabled =
    checkingInProgress || generatingInProgress;
}

function updateCheckProgress(processed, total) {
  $('checkedStat').textContent =
    processed.toLocaleString();

  const percent = total
    ? Math.round((processed / total) * 100)
    : 100;

  notice(
    'Checking availability... ' +
    processed.toLocaleString() +
    '/' +
    total.toLocaleString() +
    ' (' + percent + '%)'
  );
}

// LOGIN AND GENERATOR

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
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        return option;
      })
    );

    checkboxes(
      'lengths',
      Array.from(
        { length: 31 },
        (_, i) => String(i + 2)
      ),
      ['3', '4', '5']
    );

    checkboxes('types', types, [
      'Random letters',
      'Letters + numbers'
    ]);

    checkboxes('characters', chars, [
      'Lowercase',
      'Numbers'
    ]);

    installAutoCheckToggle();
    showApp();

    await Promise.all([
      loadFavorites(),
      loadHistory(),
      loadPresets(),
      ...(isAdmin ? [loadAdmin()] : [])
    ]);

  } catch (e) {
    console.error('Initialization failed:', e);
    showLogin();
  }
}

$('loginForm').onsubmit = async e => {
  e.preventDefault();

  const button = $('loginForm')
    .querySelector('button[type="submit"]');

  button.disabled = true;
  $('loginError').textContent = '';

  try {
    const remember = $('rememberLogin').checked;

    const data = await api('login', {
      username: $('user').value,
      password: $('password').value,
      license_key: $('key').value,
      remember
    });

    if (remember) {
      token = '';
      sessionStorage.removeItem('winter_token');
    } else {
      token = data.token || '';

      if (!token) {
        throw Error(
          'Login response did not include a session'
        );
      }

      sessionStorage.setItem('winter_token', token);
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
    console.error('Logout request failed:', e);
  }

  showLogin();
};

$('platform').onchange = () => {
  if (checkingInProgress || generatingInProgress) {
    notice('Wait for the current operation to finish.');
    return;
  }

  generationVersion++;
  names = [];
  statuses = {};
  searchId = null;

  $('checkedStat').textContent = '0';
  render();

  notice('Platform changed. Generate a new batch.');
};

async function runAvailabilityCheck() {
  if (checkingInProgress) return;

  if (!names.length) {
    throw Error('Generate usernames first');
  }

  checkingInProgress = true;
  activeCheckVersion = generationVersion;
  updateCheckButtons();

  const platform = $('platform').value;
  const batchNames = [...names];
  const batchSize = 25;

  let processed = 0;
  let available = 0;
  let taken = 0;
  let unknown = 0;
  let rateLimited = false;
  let stoppedEarly = false;

  try {
    notice('Creating availability search...');

    const data = await api('check', {
      platform,
      usernames: batchNames
    });

    if (activeCheckVersion !== generationVersion) {
      throw Error('Generated usernames changed during check');
    }

    searchId = data.search_id;

    statuses = Object.fromEntries(
      data.results.map(row => [
        row.username,
        row.status
      ])
    );

    render();

    const pending = data.results
      .filter(row => row.status === 'unknown')
      .map(row => row.username);

    const total = pending.length;

    $('checkedStat').textContent = '0';

    if (
      platform !== 'Roblox' &&
      platform !== 'Minecraft'
    ) {
      notice(
        platform +
        ': automatic availability verification is ' +
        'not supported. Results remain Unknown. ' +
        'You can review usernames manually.'
      );

      await loadHistory();
      return;
    }

    for (
      let offset = 0;
      offset < pending.length;
      offset += batchSize
    ) {
      const batch = pending.slice(
        offset,
        offset + batchSize
      );

      updateCheckProgress(processed, total);

      const result = await api('check/auto', {
        platform,
        search_id: searchId,
        usernames: batch
      });

      if (activeCheckVersion !== generationVersion) {
        throw Error('Generated usernames changed during check');
      }

      const returnedNames = new Set();

      for (const item of result.results || []) {
        if (!batch.includes(item.username)) continue;
        if (returnedNames.has(item.username)) continue;

        returnedNames.add(item.username);

        const status = [
          'available',
          'claimed',
          'unknown'
        ].includes(item.status)
          ? item.status
          : 'unknown';

        statuses[item.username] = status;

        if (status === 'available') {
          available++;
        } else if (status === 'claimed') {
          taken++;
        } else {
          unknown++;
        }

        processed++;

        if (item.reason === 'rate_limited') {
          rateLimited = true;
        }
      }

      render();
      updateCheckProgress(processed, total);

      if (
        rateLimited ||
        Number(result.remaining || 0) > 0 ||
        returnedNames.size < batch.length
      ) {
        stoppedEarly = true;
        break;
      }
    }

    await loadHistory();

    const remaining = Math.max(
      0,
      total - processed
    );

    notice(
      'Availability check finished. ' +
      'Available: ' + available +
      ' · Taken: ' + taken +
      ' · Unknown: ' + (unknown + remaining) +
      ' · Processed: ' + processed + '/' + total +
      (rateLimited
        ? ' · Rate limit reached. Checking stopped.'
        : stoppedEarly
          ? ' · Checking stopped early.'
          : '') +
      ' · Unknown does not mean available.'
    );

  } finally {
    checkingInProgress = false;
    updateCheckButtons();
    render();
  }
}

$('generateBtn').onclick = async () => {
  if (generatingInProgress || checkingInProgress) return;

  generatingInProgress = true;
  updateCheckButtons();

  try {
    notice('Generating usernames...');

    const data = await api(
      'generate',
      config()
    );

    generationVersion++;
    names = data.usernames;
    statuses = {};
    searchId = null;

    $('checkedStat').textContent = '0';
    render();

    notice(
      'Generated ' +
      data.generated.toLocaleString() +
      ' unique usernames' +
      (data.generated < data.requested
        ? ' (combination space exhausted)'
        : '') +
      '.'
    );

    const autoCheck = $('autoCheck');

    if (autoCheck && autoCheck.checked && names.length) {
      notice(
        'Generated ' +
        names.length.toLocaleString() +
        ' usernames. Starting Auto Check...'
      );

      generatingInProgress = false;
      updateCheckButtons();

      await runAvailabilityCheck();
    }

  } catch (e) {
    notice('Error: ' + e.message);
  } finally {
    generatingInProgress = false;
    updateCheckButtons();
  }
};

$('checkBtn').onclick = async () => {
  if (checkingInProgress || generatingInProgress) return;

  try {
    await runAvailabilityCheck();
  } catch (e) {
    notice('Checking error: ' + e.message);
  }
};

$('savePreset').onclick = async () => {
  const name = prompt('Preset name:');

  if (!name) return;

  try {
    await api('presets', {
      name,
      config: config()
    });

    await loadPresets();
    notice('Preset saved');
  } catch (e) {
    notice(e.message);
  }
};

$('filter').oninput = render;
$('sort').onchange = render;

$('copy').onclick = () => {
  navigator.clipboard
    .writeText(sortedNames().join('\n'))
    .then(
      () => notice('Copied usernames'),
      () => notice('Unable to copy usernames')
    );
};

$('txt').onclick = () => download(
  'txt',
  sortedNames().join('\n'),
  'text/plain'
);

$('csv').onclick = () => download(
  'csv',
  'username,status,platform\n' +
  sortedNames().map(n =>
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
  ).join('\n'),
  'text/csv'
);

$('json').onclick = () => download(
  'json',
  JSON.stringify(
    sortedNames().map(n => ({
      username: n,
      status: statuses[n] || 'not checked',
      platform: $('platform').value
    })),
    null,
    2
  ),
  'application/json'
);

$('wordlist').onchange = async e => {
  const file = e.target.files[0];

  if (!file) return;

  if (file.size > 2000000) {
    notice('Wordlist must be under 2 MB');
    return;
  }

  wordlist = (await file.text())
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

document.addEventListener('keydown', e => {
  if (
    (e.ctrlKey || e.metaKey) &&
    e.key === 'Enter' &&
    !$('app').hidden
  ) {
    e.preventDefault();
    $('generateBtn').click();
  }
});

// Restore an existing login or remembered cookie.
start();


/* winter.cc — Animated RGB Available Usernames */

(function installRainbowAvailable() {
  if (document.getElementById('winter-rgb-style')) {
    return;
  }

  const style = document.createElement('style');
  style.id = 'winter-rgb-style';

  style.textContent = `
    @keyframes winterRainbowMove {
      0% {
        background-position: 0% 50%;
      }

      50% {
        background-position: 100% 50%;
      }

      100% {
        background-position: 0% 50%;
      }
    }

    #resultList .winter-rgb-name {
      background-image: linear-gradient(
        90deg,
        #ff003c,
        #ff8c00,
        #ffff00,
        #00ff66,
        #00d9ff,
        #5555ff,
        #bd00ff,
        #ff003c
      ) !important;

      background-size: 400% 100% !important;
      background-clip: text !important;
      -webkit-background-clip: text !important;

      color: transparent !important;
      -webkit-text-fill-color: transparent !important;

      animation: winterRainbowMove 3s linear infinite !important;

      font-weight: 800 !important;
    }

    @media (prefers-reduced-motion: reduce) {
      #resultList .winter-rgb-name {
        animation: none !important;
      }
    }
  `;

  document.head.appendChild(style);

  const originalRender = render;

  render = function (...args) {
    const result = originalRender.apply(this, args);

    const rows = document.querySelectorAll(
      '#resultList .result'
    );

    for (const row of rows) {
      const name = row.querySelector('code');
      const status = row.querySelector('.status');

      if (!name || !status) continue;

      const isAvailable =
        statuses[name.textContent] === 'available';

      name.classList.toggle(
        'winter-rgb-name',
        isAvailable
      );
    }

    return result;
  };

  render();
})();


/*
 * winter.cc V5
 * Reliable availability checks + premium RGB glow
 * Compatible with existing V4 frontend and V5 backend.
 */

(function installWinterV5() {
  if (window.winterV5Installed) return;
  window.winterV5Installed = true;

  const V5_BATCH_SIZE = 25;
  const V5_RETRIES = 3;
  const V5_RETRY_DELAY = 1200;

  let currentRun = 0;
  let retryQueue = [];
  let completedNames = new Set();
  let activeSearchId = null;
  let activePlatform = null;

  const sleep = ms =>
    new Promise(resolve => setTimeout(resolve, ms));

  const isTemporaryError = error => {
    const message = String(
      error && error.message || error
    ).toLowerCase();

    return (
      message.includes('load failed') ||
      message.includes('failed to fetch') ||
      message.includes('network') ||
      message.includes('unexpected response') ||
      message.includes('timeout') ||
      message.includes('temporarily unavailable')
    );
  };

  async function requestWithRetry(
    path,
    body,
    attempts = V5_RETRIES
  ) {
    let lastError;

    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        return await api(path, body);
      } catch (error) {
        lastError = error;

        if (
          !isTemporaryError(error) ||
          attempt === attempts - 1
        ) {
          throw error;
        }

        const delay =
          V5_RETRY_DELAY * Math.pow(2, attempt);

        notice(
          'Connection interrupted. Retrying ' +
          (attempt + 1) + '/' +
          (attempts - 1) + '...'
        );

        await sleep(delay);
      }
    }

    throw lastError;
  }

  function installRGB() {
    const oldStyle = document.getElementById(
      'winter-rgb-style'
    );

    if (oldStyle) oldStyle.remove();

    const existing = document.getElementById(
      'winter-v5-rgb'
    );

    if (existing) return;

    const style = document.createElement('style');
    style.id = 'winter-v5-rgb';

    style.textContent = `
      @keyframes winterV5Rainbow {
        0% {
          background-position: 0% 50%;
        }

        100% {
          background-position: 200% 50%;
        }
      }

      @keyframes winterV5Glow {
        0%, 100% {
          filter:
            drop-shadow(0 0 3px #ff3b8d)
            drop-shadow(0 0 7px #9c40ff);
        }

        33% {
          filter:
            drop-shadow(0 0 4px #00eaff)
            drop-shadow(0 0 9px #00ffa3);
        }

        66% {
          filter:
            drop-shadow(0 0 4px #ffdc00)
            drop-shadow(0 0 9px #ff3b8d);
        }
      }

      #resultList .winter-rgb-name,
      #resultList .winter-v5-available {
        display: inline-block !important;

        background-image: linear-gradient(
          90deg,
          #ff245f 0%,
          #ffb300 12.5%,
          #f8ff00 25%,
          #00ff80 37.5%,
          #00dcff 50%,
          #5865ff 62.5%,
          #c44dff 75%,
          #ff245f 87.5%,
          #ffb300 100%
        ) !important;

        background-size: 200% 100% !important;
        background-clip: text !important;
        -webkit-background-clip: text !important;

        color: transparent !important;
        -webkit-text-fill-color: transparent !important;

        font-weight: 900 !important;

        animation:
          winterV5Rainbow 2.4s linear infinite,
          winterV5Glow 3s ease-in-out infinite
          !important;

        will-change: background-position, filter;
      }

      #resultList .winter-v5-available-row {
        border-color: rgba(
          177, 90, 255, 0.32
        ) !important;
      }

      #resultList .winter-v5-available-row
      .status {
        color: #54ffab !important;
        font-weight: 700 !important;
        text-shadow: 0 0 8px
          rgba(0, 255, 150, 0.45);
      }

      #winter-v5-progress {
        margin: 12px 0;
        padding: 12px;
        border: 1px solid
          rgba(160, 110, 255, 0.25);
        border-radius: 12px;
      }

      #winter-v5-progress[hidden] {
        display: none !important;
      }

      #winter-v5-progress-track {
        height: 8px;
        overflow: hidden;
        border-radius: 999px;
        background: rgba(
          150, 150, 180, 0.2
        );
        margin-top: 9px;
      }

      #winter-v5-progress-fill {
        height: 100%;
        width: 0%;
        border-radius: inherit;
        background: linear-gradient(
          90deg,
          #893aff,
          #dc4cff,
          #00dfff
        );
        transition: width 0.3s ease;
      }

      #winter-v5-resume {
        margin-top: 10px;
      }

      @media (
        prefers-reduced-motion: reduce
      ) {
        #resultList .winter-rgb-name,
        #resultList .winter-v5-available {
          animation: none !important;
          filter: none !important;
        }
      }
    `;

    document.head.appendChild(style);
  }

  function paintAvailableNames() {
    const rows = document.querySelectorAll(
      '#resultList .result'
    );

    for (const row of rows) {
      const name = row.querySelector('code');

      if (!name) continue;

      const available =
        statuses[name.textContent] ===
        'available';

      name.classList.toggle(
        'winter-v5-available',
        available
      );

      row.classList.toggle(
        'winter-v5-available-row',
        available
      );
    }
  }

  // The previous RGB addition already wraps render().
  // This wrapper runs after it, preserving the original
  // result rendering and all existing controls.
  const previousRender = render;

  render = function (...args) {
    const value = previousRender.apply(
      this,
      args
    );

    paintAvailableNames();

    return value;
  };

  function installProgressUI() {
    if ($('winter-v5-progress')) return;

    const box = document.createElement('div');
    box.id = 'winter-v5-progress';
    box.hidden = true;

    const label = document.createElement('div');
    label.id = 'winter-v5-progress-label';
    label.textContent = 'Ready to check';

    const track = document.createElement('div');
    track.id = 'winter-v5-progress-track';

    const fill = document.createElement('div');
    fill.id = 'winter-v5-progress-fill';

    track.append(fill);

    const resume = document.createElement('button');
    resume.id = 'winter-v5-resume';
    resume.type = 'button';
    resume.className = 'secondary';
    resume.textContent = 'Resume unfinished checks';
    resume.hidden = true;

    box.append(label, track, resume);

    const anchor = $('checkBtn');

    anchor.parentNode.insertBefore(
      box,
      anchor.nextSibling
    );

    resume.onclick = async () => {
      try {
        await resumeChecks();
      } catch (error) {
        notice(
          'Resume error: ' + error.message
        );
      }
    };
  }

  function showProgress(
    processed,
    total,
    label
  ) {
    const box = $('winter-v5-progress');

    if (!box) return;

    box.hidden = false;

    const percent = total
      ? Math.min(
          100,
          Math.round(
            processed / total * 100
          )
        )
      : 100;

    $('winter-v5-progress-label')
      .textContent =
        label + ' · ' +
        processed.toLocaleString() +
        '/' +
        total.toLocaleString() +
        ' (' + percent + '%)';

    $('winter-v5-progress-fill')
      .style.width = percent + '%';

    $('checkedStat').textContent =
      processed.toLocaleString();
  }

  function setResumeVisible(visible) {
    const button = $('winter-v5-resume');

    if (button) {
      button.hidden = !visible;
      button.disabled = checkingInProgress;
    }
  }

  function getRemaining() {
    return retryQueue.filter(
      name => !completedNames.has(name)
    );
  }

  async function processChecks(
    platform,
    id,
    pending
  ) {
    const runId = ++currentRun;
    const total = pending.length;

    let processed = 0;
    let available = 0;
    let claimed = 0;
    let unknown = 0;
    let interrupted = false;

    checkingInProgress = true;
    updateCheckButtons();
    setResumeVisible(false);

    try {
      for (
        let offset = 0;
        offset < pending.length;
        offset += V5_BATCH_SIZE
      ) {
        if (runId !== currentRun) {
          interrupted = true;
          break;
        }

        const batch = pending.slice(
          offset,
          offset + V5_BATCH_SIZE
        ).filter(
          name => !completedNames.has(name)
        );

        if (!batch.length) continue;

        showProgress(
          processed,
          total,
          'Checking ' + platform
        );

        let response;

        try {
          response = await requestWithRetry(
            'check/auto',
            {
              platform,
              search_id: id,
              usernames: batch
            }
          );
        } catch (error) {
          interrupted = true;

          notice(
            'Connection interrupted: ' +
            error.message +
            '. Your progress is preserved. ' +
            'Tap Resume to continue.'
          );

          break;
        }

        const returned = new Map(
          (response.results || []).map(
            item => [item.username, item]
          )
        );

        for (const username of batch) {
          const item = returned.get(username);

          if (!item) {
            interrupted = true;
            continue;
          }

          const status = [
            'available',
            'claimed',
            'unknown'
          ].includes(item.status)
            ? item.status
            : 'unknown';

          statuses[username] = status;

          // An Unknown result is still a completed
          // attempt, but NOT confirmed availability.
          completedNames.add(username);

          processed++;

          if (status === 'available') {
            available++;
          } else if (status === 'claimed') {
            claimed++;
          } else {
            unknown++;
          }
        }

        render();

        showProgress(
          processed,
          total,
          'Checking ' + platform
        );

        // If a batch was only partially returned,
        // preserve missing names for resumption.
        if (
          returned.size < batch.length
        ) {
          interrupted = true;
          break;
        }
      }

      try {
        await loadHistory();
      } catch (error) {
        console.warn(
          'History refresh failed:',
          error
        );
      }

      const remaining = getRemaining();

      setResumeVisible(
        interrupted && remaining.length > 0
      );

      if (remaining.length > 0) {
        notice(
          'Check paused. ' +
          remaining.length +
          ' usernames remain. ' +
          'Tap Resume unfinished checks.'
        );
      } else {
        notice(
          'Checking finished. ' +
          'Available: ' + available +
          ' · Taken: ' + claimed +
          ' · Unknown: ' + unknown +
          '. Unknown does not mean available.'
        );
      }

    } finally {
      checkingInProgress = false;
      updateCheckButtons();

      const button = $('winter-v5-resume');

      if (button) {
        button.disabled = false;
      }

      render();
    }
  }

  async function beginChecks() {
    if (checkingInProgress) return;

    if (!names.length) {
      throw Error(
        'Generate usernames first'
      );
    }

    const platform = $('platform').value;

    // If an unfinished search already exists,
    // resume it instead of creating duplicates.
    if (
      activeSearchId &&
      activePlatform === platform &&
      getRemaining().length > 0
    ) {
      await resumeChecks();
      return;
    }

    checkingInProgress = true;
    updateCheckButtons();

    let data;

    try {
      notice(
        'Saving usernames for checking...'
      );

      data = await requestWithRetry(
        'check',
        {
          platform,
          usernames: [...names]
        }
      );
    } finally {
      checkingInProgress = false;
      updateCheckButtons();
    }

    searchId = data.search_id;
    activeSearchId = searchId;
    activePlatform = platform;

    statuses = Object.fromEntries(
      data.results.map(
        row => [
          row.username,
          row.status
        ]
      )
    );

    completedNames = new Set();

    retryQueue = data.results
      .filter(
        row => row.status === 'unknown'
      )
      .map(
        row => row.username
      );

    render();

    if (
      platform !== 'Roblox' &&
      platform !== 'Minecraft'
    ) {
      notice(
        platform +
        ' does not have an integrated ' +
        'supported availability checker. ' +
        'Results remain Unknown.'
      );

      showProgress(
        0,
        retryQueue.length,
        'Verification unavailable'
      );

      setResumeVisible(false);

      return;
    }

    await processChecks(
      platform,
      activeSearchId,
      retryQueue
    );
  }

  async function resumeChecks() {
    if (checkingInProgress) return;

    if (!activeSearchId) {
      throw Error(
        'No unfinished search to resume'
      );
    }

    if (
      $('platform').value !== activePlatform
    ) {
      throw Error(
        'Switch back to ' +
        activePlatform +
        ' to resume this search'
      );
    }

    const remaining = getRemaining();

    if (!remaining.length) {
      notice(
        'All submitted usernames have ' +
        'already been processed.'
      );
      setResumeVisible(false);
      return;
    }

    await processChecks(
      activePlatform,
      activeSearchId,
      remaining
    );
  }

  // Replace the original button handlers.
  $('checkBtn').onclick = async () => {
    if (
      checkingInProgress ||
      generatingInProgress
    ) return;

    try {
      await beginChecks();
    } catch (error) {
      notice(
        'Checking error: ' +
        error.message
      );
    }
  };

  // Preserve the existing Generate button handler.
  // Auto Check is temporarily disabled during the
  // original handler so that V5 can take over.
  const originalGenerate =
    $('generateBtn').onclick;

  $('generateBtn').onclick = async () => {
    if (
      generatingInProgress ||
      checkingInProgress
    ) return;

    const toggle = $('autoCheck');
    const autoEnabled = Boolean(
      toggle && toggle.checked
    );

    if (toggle) toggle.checked = false;

    try {
      await originalGenerate();

      if (!names.length) return;

      activeSearchId = null;
      activePlatform = null;
      retryQueue = [];
      completedNames = new Set();
      setResumeVisible(false);

      const progress = $('winter-v5-progress');

      if (progress) progress.hidden = true;

      if (autoEnabled) {
        await beginChecks();
      }

    } catch (error) {
      notice(
        'Error: ' + error.message
      );
    } finally {
      if (toggle) {
        toggle.checked = autoEnabled;
      }
    }
  };

  // A platform change invalidates unfinished
  // checks for the previously selected platform.
  const originalPlatformChange =
    $('platform').onchange;

  $('platform').onchange = function (...args) {
    currentRun++;
    activeSearchId = null;
    activePlatform = null;
    retryQueue = [];
    completedNames = new Set();

    setResumeVisible(false);

    const progress = $('winter-v5-progress');

    if (progress) progress.hidden = true;

    if (originalPlatformChange) {
      return originalPlatformChange.apply(
        this,
        args
      );
    }
  };

  installRGB();
  installProgressUI();
  render();
})();


