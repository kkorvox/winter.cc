
const $ = id => document.getElementById(id);

let token = sessionStorage.getItem('winter_token') || '';
let names = [];
let statuses = {};
let searchId = null;
let wordlist = null;
let platforms = {};
let isAdmin = false;

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

async function api(path, body, method = 'POST') {
  const response = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: 'Bearer ' + token } : {})
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  });

  const data = await response.json();

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

function adminNotice(message) {
  $('adminMessage').textContent = message;
}

function checked(id) {
  return [...document.querySelectorAll('#' + id + ' input:checked')]
    .map(x => x.value);
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

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = value;
      checkbox.checked = selected.includes(value);

      label.append(
        checkbox,
        document.createTextNode(' ' + value)
      );

      return label;
    })
  );
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
    'wintercc-' + $('platform').value.toLowerCase() + '.' + ext;
  a.click();

  setTimeout(() => URL.revokeObjectURL(url), 500);
}

function sortedNames() {
  const arr = names.filter(n =>
    n.toLowerCase().includes($('filter').value.toLowerCase())
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

  const shown = sortedNames().slice(0, 500);

  for (const n of shown) {
    const row = document.createElement('div');
    row.className = 'result';

    const name = document.createElement('code');
    name.textContent = n;

    const status = document.createElement('span');
    status.className = 'status';
    status.textContent = statuses[n] || 'Not checked';

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

      for (const x of ['unknown', 'available', 'claimed']) {
        const option = document.createElement('option');
        option.value = x;
        option.textContent = x + ' (manual)';
        select.append(option);
      }

      select.value = statuses[n] || 'unknown';

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

  if (sortedNames().length > 500) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent =
      'Showing first 500 filtered results. Export includes all ' +
      names.length + ' names.';
    list.append(p);
  }

  $('generatedStat').textContent = names.length.toLocaleString();
}

async function loadFavorites() {
  const favorites = await api('favorites');
  $('favStat').textContent = favorites.length;

  const box = $('favoriteList');
  box.replaceChildren();

  for (const x of favorites) {
    const row = document.createElement('div');
    row.className = 'result';

    const text = document.createElement('span');
    text.textContent = x.username + ' · ' + x.platform;

    const button = document.createElement('button');
    button.className = 'tiny';
    button.textContent = 'Remove';

    button.onclick = async () => {
      try {
        await api(
          'favorites/' +
          encodeURIComponent(x.platform) + '/' +
          encodeURIComponent(x.username),
          {},
          'DELETE'
        );
        await loadFavorites();
      } catch (e) {
        notice(e.message);
      }
    };

    row.append(text, button);
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
      '#' + s.id + ' · ' +
      s.platform + ' · ' +
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
        Array.from({ length: 31 }, (_, i) => String(i + 2)),
        (c.lengths || []).map(String)
      );

      checkboxes('types', types, c.types || []);
      checkboxes('characters', chars, c.characters || []);

      notice('Preset loaded');
    };

    box.append(button);
  }
}

// OWNER DASHBOARD

async function loadAdmin() {
  if (!isAdmin) return;

  const [users, licenses] = await Promise.all([
    api('admin/users'),
    api('admin/licenses')
  ]);

  const userSelect = $('licenseUser');
  const previousSelection = userSelect.value;
  userSelect.replaceChildren();

  for (const user of users) {
    const option = document.createElement('option');
    option.value = user.id;
    option.textContent = user.username +
      (user.is_admin ? ' (Owner)' : '');
    userSelect.append(option);
  }

  if (users.some(u => String(u.id) === previousSelection)) {
    userSelect.value = previousSelection;
  }

  const usersBox = $('adminUsers');
  usersBox.replaceChildren();

  for (const user of users) {
    const row = document.createElement('div');
    row.className = 'result';

    const text = document.createElement('span');
    text.textContent =
      '#' + user.id + ' · ' + user.username +
      (user.is_admin ? ' · Owner' : ' · Member');

    row.append(text);
    usersBox.append(row);
  }

  const licensesBox = $('adminLicenses');
  licensesBox.replaceChildren();

  for (const license of licenses) {
    const row = document.createElement('div');
    row.className = 'result';

    const info = document.createElement('span');

    const expired = license.expires_at &&
      new Date(license.expires_at).getTime() <= Date.now();

    const status = license.revoked
      ? 'Revoked'
      : expired
        ? 'Expired'
        : 'Active';

    info.textContent =
      '#' + license.id +
      ' · ' + license.username +
      ' · ' + status +
      ' · Expires: ' +
      (license.expires_at || 'Never');

    const button = document.createElement('button');
    button.className = 'tiny';
    button.textContent = license.revoked
      ? 'Reactivate'
      : 'Revoke';

    button.onclick = async () => {
      const action = license.revoked
        ? 'reactivate'
        : 'revoke';

      if (!confirm(
        'Are you sure you want to ' + action +
        ' license #' + license.id + '?'
      )) return;

      try {
        await api(
          'admin/licenses/' + license.id + '/' + action,
          {}
        );

        adminNotice('License updated successfully.');
        await loadAdmin();
      } catch (e) {
        adminNotice(e.message);
      }
    };

    row.append(info, button);
    licensesBox.append(row);
  }
}

$('createUserForm').onsubmit = async e => {
  e.preventDefault();

  try {
    const user = await api('admin/users', {
      username: $('newUsername').value.trim(),
      password: $('newPassword').value
    });

    $('newUsername').value = '';
    $('newPassword').value = '';

    adminNotice(
      'Member "' + user.username +
      '" created. Generate a license for them next.'
    );

    await loadAdmin();
    $('licenseUser').value = String(user.id);
  } catch (err) {
    adminNotice(err.message);
  }
};

$('createLicenseForm').onsubmit = async e => {
  e.preventDefault();

  try {
    const expiry = $('licenseExpiry').value;

    const expiresAt = expiry
      ? new Date(expiry).toISOString()
      : null;

    const data = await api(
      'admin/users/' + $('licenseUser').value + '/licenses',
      { expires_at: expiresAt }
    );

    $('newLicenseKey').value = data.license_key;
    $('newLicenseBox').hidden = false;

    adminNotice(
      'License generated. Copy and save the key now.'
    );

    await loadAdmin();
  } catch (err) {
    adminNotice(err.message);
  }
};

$('copyLicense').onclick = async () => {
  try {
    await navigator.clipboard.writeText(
      $('newLicenseKey').value
    );
    adminNotice('License key copied.');
  } catch (e) {
    adminNotice('Copy failed. Select and copy the key manually.');
  }
};

$('refreshAdmin').onclick = async () => {
  try {
    await loadAdmin();
    adminNotice('Dashboard refreshed.');
  } catch (e) {
    adminNotice(e.message);
  }
};

// LOGIN AND INITIALIZATION

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
      Array.from({ length: 31 }, (_, i) => String(i + 2)),
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
    if (token) {
      console.error('Initialization failed:', e);
    }
    showLogin();
  }
}

$('loginForm').onsubmit = async e => {
  e.preventDefault();

  try {
    const data = await api('login', {
      username: $('user').value,
      password: $('password').value,
      license_key: $('key').value
    });

    token = data.token;
    sessionStorage.setItem('winter_token', token);

    $('password').value = '';
    $('key').value = '';
    $('loginError').textContent = '';

    await start();
  } catch (err) {
    $('loginError').textContent = err.message;
  }
};

$('logout').onclick = async () => {
  try {
    await api('logout', {});
  } catch (e) {}

  showLogin();
};

$('platform').onchange = () => {
  names = [];
  statuses = {};
  searchId = null;

  render();
  notice('Platform changed. Generate a new batch.');
};

$('generateBtn').onclick = async () => {
  try {
    notice('Generating...');

    const data = await api('generate', config());

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
        : '') + '.'
    );
  } catch (e) {
    notice(e.message);
  }
};

$('checkBtn').onclick = async () => {
  try {
    if (!names.length) {
      throw Error('Generate usernames first');
    }

    const data = await api('check', {
      platform: $('platform').value,
      usernames: names
    });

    searchId = data.search_id;
    statuses = Object.fromEntries(
      data.results.map(r => [r.username, r.status])
    );

    $('checkedStat').textContent = data.results.length;

    render();
    await loadHistory();

    notice(
      'Manual review started. Unknown is not verified.'
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
  navigator.clipboard.writeText(
    sortedNames().join('\n')
  ).then(
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
    [n, statuses[n] || 'not checked', $('platform').value]
      .map(x => '"' + String(x).replaceAll('"', '""') + '"')
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

  notice('Imported ' + wordlist.length + ' words');
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

if (token) start();
