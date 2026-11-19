/* winter.cc V7.8: account registration and safe download notice.
   This is an ADDITIONAL script, not a replacement for frontend/app.js.
   It leaves the live result counter, generators and Owner Dashboard unchanged. */
(() => {
  const $ = id => document.getElementById(id);
  const APPROVED_WINDOWS_EXE = true; // change only after review of a newly built EXE
  const authTabs = ['login', 'create'];

  function showTab(mode) {
    mode = authTabs.includes(mode) ? mode : 'login';
    const login = $('loginForm');
    const register = $('winter-register-form');
    if (!login || !register) return;
    login.hidden = mode !== 'login';
    register.hidden = mode !== 'create';
    login.style.display = login.hidden ? 'none' : '';
    register.style.display = register.hidden ? 'none' : '';
    document.querySelectorAll('[data-winter-auth-tab]').forEach(button => {
      const selected = button.dataset.winterAuthTab === mode;
      button.setAttribute('aria-selected', String(selected));
      button.style.background = selected ? 'linear-gradient(95deg,#8c33d7,#6a24b7)' : '#231a31';
      button.style.color = selected ? '#fff' : '#d7c6e7';
      button.style.borderColor = selected ? '#b67dff' : '#655078';
    });
  }

  function makeTabs() {
    const tabs = document.createElement('div');
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', 'Login or create an account');
    Object.assign(tabs.style, { display: 'flex', gap: '8px', margin: '14px 0 18px' });
    for (const [mode, label] of [['login', 'Log in'], ['create', 'Create account']]) {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.textContent = label;
      tab.dataset.winterAuthTab = mode;
      tab.setAttribute('role', 'tab');
      tab.onclick = () => showTab(mode);
      Object.assign(tab.style, {
        flex: '1', fontSize: '13px', padding: '9px 10px', cursor: 'pointer',
        border: '1px solid #655078', borderRadius: '10px'
      });
      tabs.appendChild(tab);
    }
    return tabs;
  }

  function installRegistration() {
    const loginForm = $('loginForm');
    if (!loginForm || $('winter-register-form')) return;
    const register = document.createElement('form');
    register.id = 'winter-register-form';
    register.className = loginForm.className;
    register.hidden = true;
    register.style.display = 'none';
    register.innerHTML = `
      <h1>❄ winter<span>.cc</span></h1>
      <p>Create an account · A license is needed to unlock the dashboard</p>
      <label>Username<input id="winter-register-user" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}" autocomplete="username" placeholder="Your username"></label>
      <label>Email<input id="winter-register-email" type="email" required maxlength="254" autocomplete="email" placeholder="you@example.com"></label>
      <label>Password<input id="winter-register-password" type="password" required minlength="12" maxlength="72" autocomplete="new-password" placeholder="At least 12 characters"></label>
      <button type="submit">Create account</button>
      <p style="font-size:12px;opacity:.8;line-height:1.5">Creating an account does not issue a license. Request a key from the owner after signing up.</p>
      <div id="winter-register-error" class="error" role="status"></div>
    `;
    loginForm.parentElement.appendChild(register);
    for (const form of [loginForm, register]) {
      const subtitle = form.querySelector('p');
      if (subtitle) subtitle.insertAdjacentElement('afterend', makeTabs());
      else form.prepend(makeTabs());
    }
    register.addEventListener('submit', async event => {
      event.preventDefault();
      const submit = register.querySelector('button[type="submit"]');
      const error = $('winter-register-error');
      submit.disabled = true;
      error.textContent = '';
      const username = $('winter-register-user').value.trim();
      try {
        const response = await fetch('/api/register', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            username,
            email: $('winter-register-email').value.trim(),
            password: $('winter-register-password').value
          })
        });
        let result = {};
        try { result = await response.json(); } catch (_) {}
        if (!response.ok) {
          throw Error(typeof result.detail === 'string' ? result.detail : 'Registration failed');
        }
        register.reset();
        $('user').value = username;
        $('password').value = '';
        $('key').value = '';
        showTab('login');
        $('loginError').textContent = result.message ||
          'Account created. Get a license key from the owner before signing in.';
      } catch (e) {
        error.textContent = e.message || 'Registration could not be completed';
      } finally {
        submit.disabled = false;
      }
    });
    showTab('login');
  }

  function pauseUnreviewedDownload() {
    // Don't serve the previously flagged binary to new visitors by default.
    if (APPROVED_WINDOWS_EXE) return;
    document.querySelectorAll('a[href*="wintercc.exe"]').forEach(link => {
      link.removeAttribute('href');
      link.setAttribute('aria-disabled', 'true');
      link.title = 'Windows download paused while the build is reviewed';
      link.textContent = 'Windows download paused — security review';
      link.style.opacity = '.7';
      link.style.cursor = 'not-allowed';
    });
  }

  function install() {
    installRegistration();
    pauseUnreviewedDownload();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install);
  } else {
    install();
  }
})();
