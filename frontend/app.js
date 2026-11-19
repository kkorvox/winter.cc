
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
