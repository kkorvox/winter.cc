
const $ = id => document.getElementById(id);

let token = sessionStorage.getItem('winter_token') || '';
let names = [];
let statuses = {};
let searchId = null;
let wordlist = null;
let platforms = {};
let isAdmin = false;
let adminUserCache = [];

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
    status.textContent =
      statuses[n] || 'Not checked';

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

      select.value =
        statuses[n] || 'unknown';

      select.onchange = async () => {
        try {
          await api('mark', {
            search_id: searchId,
            username: n,
            status: select.value
          });

          statuses[n] = select.value;
          render();
        } catch (e) {
          notice(e.message);
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
      s.created.slice(0, 19).replace('T', ' ') +
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

      notice('Preset loaded');
    };

    box.append(button);
  }
}

// OWNER DASHBOARD V2

function renderAdminUsers() {
  const box = $('adminUsers');
  box.replaceChildren();

  const query = $('adminUserSearch')
    .value.trim().toLowerCase();

  const users = adminUserCache.filter(user =>
    user.username.toLowerCase().includes(query)
  );

  if (!users.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No matching members found.';
    box.append(empty);
    return;
  }

  for (const user of users) {
    const row = document.createElement('div');
    row.className = 'result';

    const info = document.createElement('span');

    info.textContent =
      '#' + user.id +
      ' · ' + user.username +
      (user.is_admin
        ? ' · Owner'
        : ' · Member');

    row.append(info);

    if (!user.is_admin) {
      const button = document.createElement('button');
      button.className = 'tiny danger-button';
      button.textContent = 'Delete User';

      button.onclick = async () => {
        const confirmed = confirm(
          'Permanently delete "' +
          user.username +
          '"?\n\n' +
          'This removes the account, licenses, ' +
          'and saved data. ' +
          'This cannot be undone.'
        );

        if (!confirmed) return;

        button.disabled = true;

        try {
          const result = await api(
            'admin/users/' + user.id,
            {},
            'DELETE'
          );

          $('newLicenseBox').hidden = true;
          $('newLicenseKey').value = '';

          await loadAdmin();

          adminNotice(
            result.message ||
            'Member successfully deleted!',
            'success'
          );
        } catch (e) {
          adminNotice(e.message, 'error');
          button.disabled = false;
        }
      };

      row.append(button);
    }

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
    u => String(u.id) === previousSelection
  )) {
    userSelect.value = previousSelection;
  }

  renderAdminUsers();

  const licensesBox = $('adminLicenses');
  licensesBox.replaceChildren();

  if (!licenses.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No licenses found.';
    licensesBox.append(empty);
  }

  for (const license of licenses) {
    const row = document.createElement('div');
    row.className = 'result';

    const expired = license.expires_at &&
      new Date(license.expires_at).getTime() <=
      Date.now();

    const status = license.revoked
      ? 'Revoked'
      : expired
        ? 'Expired'
        : 'Active';

    const info = document.createElement('span');

    info.textContent =
      '#' + license.id +
      ' · ' + license.username + ' · ';

    const statusLabel = document.createElement('strong');

    statusLabel.className =
      'license-status ' +
      (status === 'Active'
        ? 'status-active'
        : status === 'Revoked'
          ? 'status-revoked'
          : 'status-expired');

    statusLabel.textContent = status;

    const expiry = document.createElement('span');
    expiry.className = 'license-expiry';

    expiry.textContent =
      'Expires: ' +
      (license.expires_at || 'Never');

    const button = document.createElement('button');

    button.className =
      'tiny ' +
      (license.revoked
        ? 'success-button'
        : 'danger-button');

    button.textContent =
      license.revoked
        ? 'Reactivate'
        : 'Revoke';

    button.onclick = async () => {
      const action =
        license.revoked
          ? 'reactivate'
          : 'revoke';

      if (!confirm(
        'Are you sure you want to ' +
        action +
        ' license #' +
        license.id + '?'
      )) return;

      button.disabled = true;

      try {
        const result = await api(
          'admin/licenses/' +
          license.id +
          '/' +
          action,
          {}
        );

        await loadAdmin();

        adminNotice(
          result.message ||
          'License updated successfully!',
          'success'
        );
      } catch (e) {
        adminNotice(e.message, 'error');
        button.disabled = false;
      }
    };

    row.append(
      info,
      statusLabel,
      expiry,
      button
    );

    licensesBox.append(row);
  }
}

$('adminUserSearch').oninput = renderAdminUsers;

$('createUserForm').onsubmit = async e => {
  e.preventDefault();

  const username = $('newUsername').value.trim();
  const password = $('newPassword').value;

  const button =
    e.submitter ||
    $('createUserForm').querySelector('button');

  if (adminUserCache.some(
    u => u.username.toLowerCase() ===
      username.toLowerCase()
  )) {
    adminNotice(
      'Username already exists!',
      'error'
    );
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
      user.message ||
      'Member successfully created!',
      'success'
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
      result.message ||
      'License successfully generated!',
      'success'
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

    adminNotice(
      'License key copied!',
      'success'
    );
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

    adminNotice(
      'Dashboard refreshed!',
      'success'
    );
  } catch (e) {
    adminNotice(e.message, 'error');
  }
};

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

    showApp();

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
      remember: remember
    });

    if (remember) {
      // The backend sets an HttpOnly cookie.
      // No password or license key is stored here.
      token = '';
      sessionStorage.removeItem('winter_token');
    } else {
      // Short session for this browser tab.
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
    console.error('Logout request failed:', e);
  }

  showLogin();
};

$('platform').onchange = () => {
  names = [];
  statuses = {};
  searchId = null;

  render();

  notice(
    'Platform changed. Generate a new batch.'
  );
};

$('generateBtn').onclick = async () => {
  try {
    notice('Generating...');

    const data = await api(
      'generate',
      config()
    );

    names = data.usernames;
    statuses = {};
    searchId = null;

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
  } catch (e) {
    notice(e.message);
  }
};

$('checkBtn').onclick = async () => {
  try {
    if (!names.length) {
      throw Error(
        'Generate usernames first'
      );
    }

    const data = await api('check', {
      platform: $('platform').value,
      usernames: names
    });

    searchId = data.search_id;

    statuses = Object.fromEntries(
      data.results.map(r => [
        r.username,
        r.status
      ])
    );

    $('checkedStat').textContent =
      data.results.length;

    render();
    await loadHistory();

    notice(
      'Manual review started. ' +
      'Unknown is not verified.'
    );
  } catch (e) {
    notice(e.message);
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
    notice(
      'Wordlist must be under 2 MB'
    );
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

// Try restoring an existing login.
// The backend will recognize a remembered cookie.
start();
