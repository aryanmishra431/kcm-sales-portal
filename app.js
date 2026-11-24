'use strict';
/* ============================== helpers ============================== */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => (Number(n) || 0).toLocaleString('en-IN', {maximumFractionDigits: 2});
const money = n => '₹' + fmt(n);
const addDays = (iso, n) => { const d = new Date(iso + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const niceDate = iso => iso ? new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'}) : '';
let CATS = [];    // expense categories, sent by the server (e.g. Petrol, Food, Other)
let NOTE_CAT = 'Other';   // category that always needs a note
const CAT_ICONS = {petrol: '⛽', fuel: '⛽', diesel: '⛽', food: '🍽️', travel: '🚌', 'business expense': '💼', business: '💼',
                   hotel: '🏨', lodging: '🏨', phone: '📱', 'mobile recharge': '📱', mobile: '📱', parking: '🅿️', toll: '🛣️', other: '🧾'};
const catIcon = c => CAT_ICONS[String(c).toLowerCase()] || '🧾';
const needsNote = c => String(c).toLowerCase() === NOTE_CAT.toLowerCase();
let SIZES = [];
let REWARDS = {earn: {}, pack_cost: {}, start_date: '9999-12-31'};   // product sizes, sent by the server (e.g. BIG, SMALL, 20 BIG)
const sizeCls = s => 'sz' + Math.max(0, SIZES.indexOf(s));
const pill = (s, small) => `<span class="pill ${sizeCls(s)}"${small ? ' style="font-size:10px;padding:1px 6px"' : ''}>${esc(s)}</span>`;
const zeroUnits = () => Object.fromEntries(SIZES.map(s => [s, 0]));
const kpi = (label, value, cls = '') => `<div class="kpi"><div class="l">${esc(label)}</div><div class="v ${cls}">${value}</div></div>`;
const th = (label, num) => `<th${num ? ' class="num"' : ''}>${esc(label)}</th>`;
const unitCells = u => SIZES.map(s => `<td class="num">${fmt((u || {})[s])}</td>`).join('');

let toastTimer;
function toast(msg, isError = false) {
  const t = $('toast'); t.textContent = msg; t.classList.toggle('error', isError); t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), isError ? 4500 : 2500);
}

async function api(path, {method = 'GET', body} = {}) {
  const res = await fetch(path, {
    method, credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== '/api/login') { showLogin(); throw new Error('Your session expired. Please log in again.'); }
  if (!res.ok) throw new Error(data.error || 'Something went wrong (' + res.status + ')');
  return data;
}
async function run(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (btn) btn.disabled = false; }
}

/* ============================== auth / shell ============================== */
let me = null, serverToday = null, currentPage = null;
const PAGES = {
  sales: [['mydash', '📊 डैशबोर्ड'], ['mysales', 'My Sales'], ['stock', '📦 स्टॉक'], ['rwShops', '🌶️ मसाला परिवार'], ['myexp', 'Expenses'], ['history', 'History'], ['account', 'Account']],
  admin: [['dash', 'Dashboard'], ['rwAdmin', '🌶️ मसाला परिवार'], ['teamCal', '📅 Calendar'], ['expenses', 'Expenses'], ['stockAdmin', '📦 Stock'], ['shops', 'Shops'], ['prods', 'Products'], ['users', 'Users'], ['account', 'Account']],
};

function showLogin() {
  me = null; sf = null; $('appView').classList.add('hidden'); $('loginView').classList.remove('hidden'); $('p').value = '';
}
function showApp(data) {
  me = data.user; serverToday = data.today; SIZES = data.sizes || ['BIG', 'SMALL']; CATS = data.expense_categories || []; REWARDS = data.rewards || REWARDS; NOTE_CAT = data.note_required_category || 'Other';
  $('loginView').classList.add('hidden'); $('appView').classList.remove('hidden');
  $('whoName').textContent = me.full_name;
  $('whoRole').textContent = me.role === 'admin' ? 'Admin' : 'Salesperson';
  $('nav').innerHTML = PAGES[me.role].map(([id, label]) => `<button data-page="${id}">${label}</button>`).join('');
  $('msDate').value = $('msDate').max = $('sfDate').max = serverToday;
  $('mdMonth').value = $('mdMonth').max = $('tcMonth').value = $('tcMonth').max = serverToday.slice(0, 7);
  $('dTo').value = serverToday; $('dFrom').value = addDays(serverToday, -6);
  $('meDate').value = $('meDate').max = $('efDate').max = serverToday;
  $('aeTo').value = serverToday; $('aeFrom').value = addDays(serverToday, -6);
  $('raTo').value = serverToday; $('raFrom').value = addDays(serverToday, -29);
  go(PAGES[me.role][0][0]);
}

$('loginForm').addEventListener('submit', e => {
  e.preventDefault(); $('loginErr').textContent = '';
  run($('loginBtn'), async () => {
    try { showApp(await api('/api/login', {method: 'POST', body: {username: $('u').value.trim(), password: $('p').value}})); }
    catch (err) { $('loginErr').textContent = err.message; }
  });
});
$('logoutBtn').addEventListener('click', async () => {
  if (!leaveSaleForm()) return;
  await api('/api/logout', {method: 'POST'}).catch(() => {}); showLogin();
});
$('nav').addEventListener('click', e => { const b = e.target.closest('button[data-page]'); if (b) go(b.dataset.page); });

const RENDER = {mydash: loadMyDash, teamCal: loadTeamCal, shopHist: async () => {}, stock: loadStock, stockLimits: async () => {}, stockAdmin: loadStockAdmin, rwShops: loadRwShops, rwPanel: async () => {}, rwAdmin: loadRwAdmin, mysales: loadMySales, myexp: loadMyExp, expenses: loadAdminExp, history: loadHistory, dash: loadDash, shops: loadShopsAdmin,
                prods: loadProducts, users: loadUsers, account: async () => $('pwForm').reset(), saleForm: async () => {}};
function go(page) {
  // a sale being entered is kept while peeking at the shop's history, and only left when going elsewhere
  const inSale = currentPage === 'saleForm' || (currentPage === 'shopHist' && sf);
  if (inSale && page !== 'saleForm' && page !== 'shopHist' && !leaveSaleForm()) return;
  currentPage = page;
  document.querySelectorAll('.page').forEach(s => s.classList.toggle('hidden', s.id !== page));
  const navPage = page === 'shopHist' ? (sh && sh.navFrom) || 'mysales' : page === 'saleForm' ? 'mysales' : page === 'stockLimits' ? 'stock' : page === 'rwPanel' ? (me.role === 'admin' ? 'rwAdmin' : 'rwShops') : page;
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.page === navPage));
  window.scrollTo(0, 0);
  return run(null, RENDER[page]);
}

/* ============================== salesperson: My Sales ============================== */
let dayOrders = [];
function itemLines(o) {
  return o.items.map(i => `${esc(i.product)} ${pill(i.size, true)} × ${fmt(i.qty)} @ ₹${fmt(i.price)}`).join('<br>');
}
async function loadMySales() {
  const date = $('msDate').value || serverToday;
  const d = await api('/api/my/orders?date=' + encodeURIComponent(date));
  dayOrders = d.orders;
  const units = zeroUnits(); let total = 0;
  dayOrders.forEach(o => { SIZES.forEach(s => units[s] += o.units[s] || 0); total += o.total; });
  $('msKpis').innerHTML = kpi('Shops', new Set(dayOrders.map(o => o.shop.id)).size)
    + SIZES.map(s => kpi(s + ' units', fmt(units[s]), sizeCls(s))).join('') + kpi('Total', money(total));
  $('orderList').innerHTML = dayOrders.length ? dayOrders.map(o => `
    <div class="order" data-oid="${o.id}">
      <div><div class="shop">${esc(o.shop.name)}</div>
        <div class="meta">${esc(o.shop.area || '')}${o.shop.area && o.shop.phone ? ' · ' : ''}${esc(o.shop.phone || '')}</div>
        <div class="lines">${itemLines(o)}</div></div>
      <div><div class="amt">${money(o.total)}</div><div class="meta" style="text-align:right">Tap to edit</div>
        <button class="link sh-link" data-hist="${o.shop.id}">🏪 इतिहास · History</button></div>
    </div>`).join('')
    : `<div class="empty-card">No sales recorded on ${esc(niceDate(date))}.<br>Tap <b>+ New Sale</b> when you sell at a shop.</div>`;
}
$('msDate').addEventListener('change', () => run(null, loadMySales));
$('newSaleBtn').addEventListener('click', () => openSaleForm(null));
$('orderList').addEventListener('click', e => {
  const h = e.target.closest('[data-hist]'); if (h) { openShopHistory(Number(h.dataset.hist)); return; }
  const c = e.target.closest('[data-oid]'); if (!c) return;
  openSaleForm(dayOrders.find(o => o.id === Number(c.dataset.oid)));
});

/* ============================== salesperson: New / Edit Sale ============================== */
let shops = [], products = [], sf = null;
// sf = {id, shop, items: {vid: {qty, price, touched}}, list: [{name, removed, variants:{BIG:vid, SMALL:vid}}], dirty}

async function openSaleForm(order, presetShop) {
  const [s, p] = await Promise.all([api('/api/shops'), api('/api/products')]).catch(e => { toast(e.message, true); return []; });
  if (!s) return;
  shops = s.shops; products = p.products;
  sfStock = {};
  if (me.role === 'sales') {
    try { (await api('/api/my/stock')).items.forEach(it => { sfStock[it.variant_id] = it.have; }); } catch (e) { /* stock is optional */ }
    if (order) order.items.forEach(it => { if (sfStock[it.variant_id] != null) sfStock[it.variant_id] += it.qty; });
  }
  sf = {id: order ? order.id : null, shop: order ? order.shop : null, items: {}, dirty: false, list: []};
  for (const pr of products) {
    sf.list.push({name: pr.name, removed: false, variants: pr.variants});
    SIZES.forEach(size => { sf.items[pr.variants[size]] = {qty: 0, price: 0, touched: false}; });
  }
  if (order) {
    for (const it of order.items) {
      if (!sf.items[it.variant_id]) {   // product was removed by admin after this sale — keep it visible
        let entry = sf.list.find(x => x.removed && x.pid === it.product_id);
        if (!entry) { entry = {name: it.product, removed: true, pid: it.product_id, variants: {}}; sf.list.push(entry); }
        entry.variants[it.size] = it.variant_id;
      }
      sf.items[it.variant_id] = {qty: it.qty, price: it.price, touched: true};
    }
  }
  $('sfTitle').textContent = order ? 'Edit Sale' : 'New Sale';
  $('sfDate').value = order ? order.date : ($('msDate').value || serverToday);
  $('sfDelete').classList.toggle('hidden', !order);
  $('prodSearch').value = ''; $('shopSearch').value = '';
  $('newShopForm').classList.add('hidden'); $('newShopForm').reset();
  showShop();
  drawItems();
  await go('saleForm');
  if (presetShop && !order) { await pickShop(presetShop); sf.dirty = false; }
  if (!sf.shop) $('shopSearch').focus();
}

function leaveSaleForm() {
  if (sf && sf.dirty && !confirm('This sale is not saved. Leave without saving?')) return false;
  sf = null; return true;
}

/* --- shop picker --- */
function showShop() {
  const picked = !!sf.shop;
  $('shopPicked').classList.toggle('hidden', !picked);
  $('shopPicker').classList.toggle('hidden', picked);
  if (picked) {
    $('spName').textContent = sf.shop.name;
    $('spInfo').textContent = [sf.shop.area, sf.shop.phone].filter(Boolean).join(' · ');
  } else drawShopResults();
}
function drawShopResults() {
  const q = $('shopSearch').value.trim().toLowerCase();
  const btn = s => `<button type="button" data-sid="${s.id}"><b>${esc(s.name)}</b>${s.area ? ` <span class="muted">· ${esc(s.area)}</span>` : ''}${s.my_last_sale ? `<div class="muted">Last sale: ${esc(niceDate(s.my_last_sale))}</div>` : ''}</button>`;
  let html;
  if (!q) {
    const mine = shops.filter(s => s.mine).sort((a, b) => (b.my_last_sale || '').localeCompare(a.my_last_sale || ''));
    html = mine.length ? '<div class="grp">My shops</div>' + mine.slice(0, 30).map(btn).join('')
                       : '<p class="muted" style="margin:4px 0">Type to search, or add the shop if it is new.</p>';
  } else {
    const hits = shops.filter(s => (s.name + ' ' + s.area).toLowerCase().includes(q))
                      .sort((a, b) => (b.mine - a.mine) || a.name.localeCompare(b.name)).slice(0, 40);
    html = hits.length ? hits.map(btn).join('') : `<p class="muted" style="margin:4px 0">No shop found for "${esc(q)}". Add it as a new shop below.</p>`;
  }
  $('shopResults').innerHTML = html;
}
async function pickShop(shop) {
  sf.shop = shop; sf.dirty = true; showShop();
  // pre-fill prices from the last time this salesperson sold (at this shop if possible)
  try {
    const {prices} = await api('/api/my/prices?shop_id=' + shop.id);
    for (const [vid, r] of Object.entries(sf.items)) if (!r.touched && prices[vid]) r.price = prices[vid];
    drawItems();
  } catch (e) { /* prices are only a convenience */ }
}
$('shopSearch').addEventListener('input', drawShopResults);
$('shopResults').addEventListener('click', e => {
  const b = e.target.closest('[data-sid]'); if (!b) return;
  pickShop(shops.find(s => s.id === Number(b.dataset.sid)));
});
$('spHist').addEventListener('click', () => { if (sf && sf.shop) openShopHistory(sf.shop.id); });
$('spChange').addEventListener('click', () => { sf.shop = null; showShop(); $('shopSearch').focus(); });
$('newShopBtn').addEventListener('click', () => {
  $('newShopForm').classList.remove('hidden'); $('nsName').value = $('shopSearch').value.trim(); $('nsName').focus();
});
$('nsCancel').addEventListener('click', () => $('newShopForm').classList.add('hidden'));
$('newShopForm').addEventListener('submit', e => {
  e.preventDefault();
  run(e.submitter, async () => {
    const {shop} = await api('/api/shops', {method: 'POST', body: {name: $('nsName').value, area: $('nsArea').value, phone: $('nsPhone').value}});
    shop.mine = true; shops.push(shop);
    $('newShopForm').reset(); $('newShopForm').classList.add('hidden'); $('shopSearch').value = '';
    toast('✔ Shop added'); await pickShop(shop);
  });
});

/* --- products --- */
function drawItems() {
  const q = $('prodSearch').value.trim().toLowerCase();
  const rows = sf.list.filter(p => !q || p.name.toLowerCase().includes(q));
  $('sfItems').innerHTML = rows.length ? rows.map(p => {
    const has = SIZES.some(s => p.variants[s] && sf.items[p.variants[s]].qty > 0);
    return `<div class="pcard ${has ? 'has' : ''}"><div class="pname">${esc(p.name)}${p.removed ? ' <span class="muted">(removed product)</span>' : ''}</div>
      ${SIZES.filter(s => p.variants[s]).map(s => { const vid = p.variants[s], r = sf.items[vid]; return `
      <div class="vrow">${pill(s)}
        <input type="number" inputmode="decimal" min="0" step="0.01" data-vid="${vid}" data-f="price" value="${r.price || ''}" placeholder="price">
        <input type="number" inputmode="numeric" min="0" step="1" data-vid="${vid}" data-f="qty" value="${r.qty || ''}" placeholder="qty">
        <span class="amt" id="amt${vid}">${r.qty ? money(r.qty * r.price) : ''}</span></div>${stockHint(vid)}`; }).join('')}
    </div>`;
  }).join('') : (sf.list.length ? '<p class="muted">No product matches your search.</p>' : '<p class="muted">No products yet. Ask your admin to add products.</p>');
  calcSale();
}
function calcSale() {
  const units = zeroUnits(); let total = 0;
  for (const p of sf.list) for (const s of SIZES) {
    const vid = p.variants[s]; if (!vid) continue;
    const r = sf.items[vid]; units[s] += r.qty; total += r.qty * r.price;
  }
  const pts = ($('sfDate').value || serverToday) >= REWARDS.start_date ? SIZES.reduce((a, s) => a + units[s] * (REWARDS.earn[s] || 0), 0) : 0;
  $('sfTotals').innerHTML = SIZES.map(s => `<span>${esc(s)} <b>${fmt(units[s])}</b></span>`).join('') + `<span>Total <b>${money(total)}</b></span>`
    + (pts ? `<span class="pts">🌶️ +${pts} पॉइंट</span>` : '');
}
$('prodSearch').addEventListener('input', drawItems);
$('sfItems').addEventListener('input', e => {
  const el = e.target; if (!el.dataset.vid) return;
  const r = sf.items[el.dataset.vid];
  r[el.dataset.f] = Math.max(0, Number(el.value) || 0);
  if (el.dataset.f === 'qty') r.qty = Math.floor(r.qty);
  r.touched = true; sf.dirty = true;
  $('amt' + el.dataset.vid).textContent = r.qty ? money(r.qty * r.price) : '';
  const hint = $('stk' + el.dataset.vid); if (hint) hint.outerHTML = stockHint(el.dataset.vid);
  const card = el.closest('.pcard');
  card.classList.toggle('has', [...card.querySelectorAll('input[data-f="qty"]')].some(i => Number(i.value) > 0));
  calcSale();
});
$('sfDate').addEventListener('change', () => { if (sf) sf.dirty = true; });
$('sfBack').addEventListener('click', () => go('mysales'));

$('sfSave').addEventListener('click', () => run($('sfSave'), async () => {
  if (!sf.shop) { $('shopSearch').focus(); return toast('Choose the shop first', true); }
  const items = Object.entries(sf.items).filter(([, r]) => r.qty > 0).map(([vid, r]) => ({variant_id: Number(vid), qty: r.qty, price: r.price}));
  if (!items.length) return toast('Enter the quantity for at least one product', true);
  const date = $('sfDate').value;
  const body = {date, shop_id: sf.shop.id, items};
  const res = sf.id ? await api('/api/my/orders/' + sf.id, {method: 'PUT', body}) : await api('/api/my/orders', {method: 'POST', body});
  toast(`✔ Sale saved for ${sf.shop.name}`);
  sf.dirty = false; $('msDate').value = date;
  await openRewards(res.shop_id, {msg: 'order', orderId: res.id, back: 'mysales'});
}));
$('sfDelete').addEventListener('click', () => {
  if (!sf.id || !confirm(`Delete this sale to ${sf.shop.name}? This cannot be undone.`)) return;
  run($('sfDelete'), async () => {
    await api('/api/my/orders/' + sf.id, {method: 'DELETE'});
    toast('Sale deleted'); sf.dirty = false; go('mysales');
  });
});
window.addEventListener('beforeunload', e => { if (sf && sf.dirty) { e.preventDefault(); e.returnValue = ''; } });

/* ============================== Salesperson: monthly dashboard ============================== */
const shortMoney = n => n >= 100000 ? '₹' + (n / 100000).toFixed(1).replace(/\.0$/, '') + 'L' : n >= 1000 ? '₹' + (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : '₹' + Math.round(n);
async function loadMyDash() {
  const d = await api('/api/my/dashboard?month=' + encodeURIComponent($('mdMonth').value || serverToday.slice(0, 7)));
  const monthName = new Date(d.first + 'T00:00:00').toLocaleDateString('en-IN', {month: 'long', year: 'numeric'});
  const current = d.month === serverToday.slice(0, 7);
  $('mdSub').textContent = current ? `${monthName} · 1 to ${new Date(d.upto + 'T00:00:00').getDate()} (${d.days_so_far} days so far)` : monthName;
  $('mdTotal').textContent = money(d.revenue);
  if (d.prev_revenue > 0) {
    const pct = Math.round((d.revenue - d.prev_revenue) / d.prev_revenue * 100);
    $('mdCmp').innerHTML = `${pct >= 0 ? '▲' : '▼'} ${Math.abs(pct)}% पिछले महीने से · vs last month (${money(d.prev_revenue)}${current ? ', same days' : ''})`;
    $('mdCmp').className = 'md-cmp ' + (pct >= 0 ? 'up' : 'down');
  } else { $('mdCmp').textContent = 'पिछले महीने कोई बिक्री नहीं · No sales last month to compare'; $('mdCmp').className = 'md-cmp'; }
  $('mdKpis').innerHTML = kpi('बिक्री वाले दिन · Days with sales', `${d.days_worked}<small class="muted"> / ${d.working_days} working days</small>`)
    + kpi('ऑर्डर · Orders', fmt(d.orders)) + kpi('दुकानें · Shops', fmt(d.shops))
    + kpi('औसत/दिन · Avg per sales day', money(d.avg_per_day_worked))
    + kpi('पैक · Packs', SIZES.map(z => `${esc(z)} ${fmt(d.units[z])}`).join('<br>'), 'sm')
    + kpi('खर्च · Expenses', money(d.expenses))
    + (d.best_day ? kpi('सबसे अच्छा दिन · Best day', `${money(d.best_day.revenue)}<div class="muted" style="font-size:12px;font-weight:600">${esc(niceDate(d.best_day.date))}</div>`) : '');
  const z = d.zero_days.length;
  $('mdZero').className = 'md-zero ' + (z ? 'has' : 'none');
  $('mdZero').innerHTML = z ? `<div class="md-zero-n">${z}</div><div><b>दिन बिना बिक्री · days with zero sales</b>${d.sundays_off ? '<div class="muted" style="font-size:12px">रविवार नहीं गिने · Sundays not counted</div>' : ''}
      <div class="md-chips">${d.zero_days.map(x => `<button class="md-chip" data-day="${x}">${esc(new Date(x + 'T00:00:00').toLocaleDateString('en-IN', {weekday: 'short', day: 'numeric'}))}</button>`).join('')}</div></div>`
    : `<div class="md-zero-n">0</div><div><b>🎉 कोई दिन खाली नहीं · No zero-sale days ${current ? 'so far' : 'this month'}!</b></div>`;
  $('mdCal').innerHTML = calendarHTML(d.days);
}
function calendarHTML(days, extra = '') {
  const heads = ['सोम Mon', 'मंगल Tue', 'बुध Wed', 'गुरु Thu', 'शुक्र Fri', 'शनि Sat', 'रवि Sun'];
  const max = Math.max(1, ...days.map(x => x.revenue));
  return heads.map(h => `<div class="md-wd">${h.split(' ')[0]}<br><small>${h.split(' ')[1]}</small></div>`).join('')
    + '<div></div>'.repeat(days[0].weekday)
    + days.map(x => `<button class="md-day ${x.status}${x.date === serverToday ? ' today' : ''}" data-day="${x.status === 'future' || x.status === 'off' ? '' : x.date}" ${extra}
        ${x.status === 'sale' ? `style="--a:${(0.25 + 0.75 * x.revenue / max).toFixed(2)}"` : ''} title="${x.date}">
        <span class="n">${x.day}</span><span class="v">${x.status === 'sale' ? shortMoney(x.revenue) : x.status === 'zero' ? '✕' : x.status === 'off' ? 'छुट्टी' : ''}</span></button>`).join('');
}
async function loadTeamCal() {
  const r = await api('/api/admin/team-calendar?month=' + encodeURIComponent($('tcMonth').value || serverToday.slice(0, 7)));
  $('tcSundays').checked = !!r.work.sundays_off;
  $('tcGrid').innerHTML = r.people.length ? r.people.map(p => {
    const pct = p.prev_revenue > 0 ? Math.round((p.revenue - p.prev_revenue) / p.prev_revenue * 100) : null;
    const z = p.zero_days.length;
    return `<div class="tc-card">
      <div class="tc-head"><div><div class="tc-name">${esc(p.name)}</div>
          <div class="muted" style="font-size:13px">${p.days_worked} / ${p.working_days} working days with sales · ${fmt(p.orders)} orders · ${fmt(p.shops)} shops</div></div>
        <div style="text-align:right"><div class="tc-total">${money(p.revenue)}</div>
          ${pct == null ? '<div class="muted" style="font-size:12px">no sales last month</div>' : `<div class="tc-cmp ${pct >= 0 ? 'up' : 'down'}">${pct >= 0 ? '▲' : '▼'} ${Math.abs(pct)}% vs last month</div>`}</div></div>
      <div class="tc-zero ${z ? 'has' : 'none'}"><b>${z}</b> ${z ? 'zero-sale days: ' + p.zero_days.map(x => new Date(x + 'T00:00:00').getDate()).join(', ') : 'zero-sale days 🎉'}</div>
      <div class="md-cal tc-cal" data-uid="${p.id}">${calendarHTML(p.days)}</div>
    </div>`;
  }).join('') : '<div class="empty-card">No active salespeople.</div>';
}
$('tcMonth').addEventListener('change', () => run(null, loadTeamCal));
$('tcSundays').addEventListener('change', () => run(null, async () => {
  await api('/api/admin/work-settings', {method: 'PUT', body: {sundays_off: $('tcSundays').checked}});
  toast($('tcSundays').checked ? '✔ Sundays are a day off' : '✔ Sundays count as working days'); await loadTeamCal();
}));
$('tcGrid').addEventListener('click', e => {
  const b = e.target.closest('[data-day]'), cal = e.target.closest('[data-uid]'); if (!b || !b.dataset.day || !cal) return;
  $('dFrom').value = $('dTo').value = b.dataset.day; $('dUser').value = cal.dataset.uid;
  if ($('dUser').value !== cal.dataset.uid) $('dUser').innerHTML += `<option value="${cal.dataset.uid}" selected></option>`;
  $('dShop').value = ''; go('dash');
});
$('mdMonth').addEventListener('change', () => run(null, loadMyDash));
$('mdNewSale').addEventListener('click', () => openSaleForm(null));
$('mydash').addEventListener('click', e => {
  const b = e.target.closest('[data-day]'); if (!b || !b.dataset.day) return;
  $('msDate').value = b.dataset.day; go('mysales');
});

/* ============================== Shop history ============================== */
let sh = null;
const shortDate = iso => iso ? new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', {day: 'numeric', month: 'short'}) : '';
const ago = n => n == null ? '–' : n === 0 ? 'आज · today' : n === 1 ? 'कल · yesterday' : `${n} दिन पहले · ${n} days ago`;
async function openShopHistory(shopId) {
  const data = await api(`/api/shops/${shopId}/history`).catch(e => { toast(e.message, true); return null; });
  if (!data) return;
  const from = currentPage === 'shopHist' ? (sh && sh.back) : currentPage;
  const navFrom = document.querySelector('nav button.active')?.dataset.page;
  sh = {id: shopId, data, back: from || (me.role === 'admin' ? 'shops' : 'mysales'), navFrom};
  drawShopHistory();
  await go('shopHist');
}
function drawShopHistory() {
  const d = sh.data, s = d.shop, m = d.summary;
  $('shName').innerHTML = `${esc(s.name)}${s.active ? '' : ' <span class="st-tag zero">removed</span>'}`;
  $('shMeta').innerHTML = [s.area && `📍 ${esc(s.area)}`, m.first && `पहला ऑर्डर · First order: ${esc(niceDate(m.first))}`,
                           s.added_by && `Added by ${esc(s.added_by)}`].filter(Boolean).join(' · ');
  $('shContact').innerHTML = s.phone ? `<a class="btn sm sh-call" href="tel:${esc(s.phone.replace(/[^\d+]/g, ''))}">📞 ${esc(s.phone)}</a>
      <a class="btn sm wa" href="https://wa.me/${esc(s.wa_phone)}" target="_blank" rel="noopener">🟢 WhatsApp</a>` : '<span class="muted">No phone number saved</span>';
  $('shKpis').innerHTML = kpi('कुल खरीद · Total bought', money(m.revenue)) + kpi('ऑर्डर · Orders', fmt(m.orders))
    + kpi('औसत ऑर्डर · Avg order', money(Math.round(m.avg))) + kpi('आखिरी ऑर्डर · Last order', esc(ago(m.days_since)), m.days_since >= 14 ? 'st-bad sm' : 'sm')
    + kpi('🌶️ पॉइंट · Points', fmt(m.points)) + kpi('पैक · Packs', SIZES.map(z => `${esc(z)} ${fmt(m.units[z])}`).join('<br>'), 'sm');
  $('shPoints').classList.toggle('hidden', false);
  $('shNewSale').classList.toggle('hidden', me.role !== 'sales' || !s.active);
  $('shTop').innerHTML = d.top.length ? d.top.map(t => `<tr><td>${esc(t.product)} ${pill(t.size, true)}</td><td class="num"><b>${fmt(t.qty)}</b></td>
    <td class="num">${t.times}</td><td class="num">${fmt(t.last_price)}</td><td style="white-space:nowrap">${esc(shortDate(t.last_date))}</td></tr>`).join('')
    : '<tr><td colspan="5" class="empty">No sales yet.</td></tr>';
  const events = [
    ...d.orders.map(o => ({date: o.date, sort: 'b' + String(o.id).padStart(9, '0'), html: `
      <div class="sh-ev">
        <div class="sh-ev-head"><span>🛒 <b>${esc(niceDate(o.date))}</b> · ${esc(o.user_name)}</span><b>${money(o.total)}</b></div>
        <div class="sh-lines">${o.items.map(i => `<div>${esc(i.product)} ${pill(i.size, true)} × ${fmt(i.qty)} @ ₹${fmt(i.price)} <span class="muted">= ${money(i.amount)}</span></div>`).join('')}</div>
        ${me.role === 'sales' && o.user_id === me.id ? `<button class="link sh-link" data-edit="${o.id}">✏️ Edit</button>` : ''}
      </div>`})),
    ...d.redemptions.map(r => ({date: r.date, sort: 'a' + String(r.id).padStart(9, '0'), html: `
      <div class="sh-ev gift ${r.status === 'cancelled' ? 'cancelled' : ''}">
        <div class="sh-ev-head"><span>🎁 <b>${esc(niceDate(r.date))}</b> · ${esc(r.by || '')}</span><span class="muted">−${r.points} पॉइंट</span></div>
        <div class="sh-lines">${r.kind === 'pack' ? `फ्री पैक · Free ${r.quantity} × ${esc(r.size)} ${esc(r.product || '')}` : `गिफ्ट · Gift: ${esc(r.gift_hi)} · ${esc(r.gift_en)}`}
          <span class="muted">(${esc(r.status)})</span></div>
      </div>`})),
  ].sort((a, b) => b.date.localeCompare(a.date) || b.sort.localeCompare(a.sort));
  $('shTimeline').innerHTML = events.length ? events.map(e => e.html).join('') + (d.truncated ? '<p class="muted">Showing the latest 300 sales.</p>' : '')
    : '<div class="empty-card">इस दुकान का अभी कोई इतिहास नहीं · No history for this shop yet.</div>';
}
$('shBack').addEventListener('click', () => go(sh ? sh.back : 'mysales'));
$('shNewSale').addEventListener('click', () => {
  if (sf && !leaveSaleForm()) return;
  const s = sh.data.shop;
  openSaleForm(null, {id: s.id, name: s.name, area: s.area, phone: s.phone});
});
$('shPoints').addEventListener('click', () => openRewards(sh.id, {back: 'shopHist'}));
$('shTimeline').addEventListener('click', e => {
  const b = e.target.closest('[data-edit]'); if (!b) return;
  if (sf && !leaveSaleForm()) return;
  const o = sh.data.orders.find(x => x.id === Number(b.dataset.edit)); if (o) openSaleForm(o);
});

/* ============================== Salesperson stock ============================== */
let sfStock = {}, stItems = [], stProducts = [], stLimits = {};
function stockHint(vid) {
  const have = sfStock[vid]; if (have == null) return '';
  const q = sf.items[vid].qty;
  if (q > have) return `<div class="stk warn" id="stk${vid}">⚠️ सिर्फ ${Math.max(0, have)} बचे · only ${Math.max(0, have)} left in your stock</div>`;
  return `<div class="stk" id="stk${vid}">📦 स्टॉक ${have}${q ? ` → <b>${have - q}</b> बचेंगे · left after sale` : ' · in stock'}</div>`;
}
async function loadStock() {
  const d = await api('/api/my/stock');
  stProducts = d.products; stLimits = d.limits;
  drawStock(d);
}
function drawStock(d) {
  stItems = d.items;
  const left = stItems.reduce((a, it) => a + Math.max(0, it.have), 0);
  const zero = stItems.filter(it => it.level === 'zero').length, low = stItems.filter(it => it.level === 'low').length;
  const sold = stItems.reduce((a, it) => a + it.sold_today, 0);
  $('stKpis').innerHTML = kpi('बचे पैक · Packs left', fmt(left)) + kpi('आज बिका · Sold today', fmt(sold))
    + kpi('खत्म · Finished', zero, zero ? 'st-bad' : '') + kpi('कम · Low', low, low ? 'st-amb' : '');
  const need = stItems.filter(it => it.refill_qty > 0).length;
  $('stRefillAll').classList.toggle('hidden', !need);
  $('stRefillAll').textContent = `🔄 सब रिफिल करें · Refill all (${need})`;
  $('stList').innerHTML = stItems.length ? stItems.map(it => {
    const pct = it.limit ? Math.min(100, Math.max(0, it.have / it.limit * 100)) : 0;
    const color = it.level === 'zero' ? 'var(--bad)' : it.level === 'low' ? 'var(--tur)' : 'var(--leaf)';
    return `<div class="st-item ${it.level}">
      <div class="st-head"><div class="st-name">${esc(it.product)} ${pill(it.size)}
          ${it.level === 'zero' ? '<span class="st-tag zero">खत्म · 0 left</span>' : it.level === 'low' ? '<span class="st-tag low">कम · low</span>' : ''}</div>
        <div class="st-count ${it.level}">${it.have}<small> / ${it.limit}</small></div></div>
      <div class="st-bar"><span style="width:${pct}%;background:${color}"></span></div>
      ${it.sold_today || it.free ? `<div class="st-meta">आज बिका · sold today: ${it.sold_today}</div>` : ''}
      <div class="st-acts">
        <button class="st-minus" data-step="-1" data-vid="${it.variant_id}" ${it.have <= 0 ? 'disabled' : ''} title="Remove one">−</button>
        <button class="st-plus" data-step="1" data-vid="${it.variant_id}" title="Add one">＋</button>
        <button class="st-refill" data-refill="${it.variant_id}" ${it.refill_qty <= 0 ? 'disabled' : ''}>
          ${it.refill_qty <= 0 ? '✔ भरा है · Full' : `🔄 रिफिल · Refill <small>(+${it.refill_qty})</small>`}</button>
      </div></div>`;
  }).join('') : `<div class="empty-card">अभी कोई लिमिट नहीं · No stock limits yet.<br>Tap <b>⚙️ Set limits</b> and enter how many packs of each product you carry, e.g. Garam Masala BIG = 10.</div>`;
  $('stLog').innerHTML = d.log.length ? d.log.map(l => `<div class="st-logrow"><span class="muted">${esc(l.time)}</span> ${esc(l.text)}</div>`).join('')
    : '<p class="muted" style="margin:0">आज अभी कुछ नहीं · Nothing yet today.</p>';
}
$('stList').addEventListener('click', e => {
  const st = e.target.closest('[data-step]'), rf = e.target.closest('[data-refill]');
  if (st && !st.disabled) run(st, async () => {
    const d = await api(`/api/my/stock/${st.dataset.vid}/step`, {method: 'POST', body: {qty: Number(st.dataset.step)}});
    drawStock(d); const it = d.items.find(x => x.variant_id === Number(st.dataset.vid)); if (it) toast(`${it.product} ${it.size}: ${it.have} / ${it.limit}`);
  });
  if (rf && !rf.disabled) run(rf, async () => {
    const d = await api(`/api/my/stock/${rf.dataset.refill}/refill`, {method: 'POST'});
    drawStock(d); const it = d.items.find(x => x.variant_id === Number(rf.dataset.refill));
    if (it) toast(`✔ ${it.product} ${it.size} भरा · refilled (+${d.added}) → ${it.limit}`);
  });
});
$('stRefillAll').addEventListener('click', () => {
  if (!confirm('सब प्रोडक्ट लिमिट तक भरें? · Refill every product to its limit?')) return;
  run($('stRefillAll'), async () => { const d = await api('/api/my/stock/refill-all', {method: 'POST'}); drawStock(d); toast(`✔ ${d.refilled} products refilled`); });
});
$('stLimitsBtn').addEventListener('click', () => { drawLimits(); go('stockLimits'); });
$('slBack').addEventListener('click', () => go('stock'));
let slDraft = {};
function drawLimits() {
  if (!$('slSearch').dataset.keep) { slDraft = {...stLimits}; $('slSearch').value = ''; }
  const q = $('slSearch').value.trim().toLowerCase();
  const list = stProducts.filter(p => !q || p.name.toLowerCase().includes(q));
  $('slList').innerHTML = list.map(p => `<div class="sl-prod"><div class="sl-name">${esc(p.name)}</div>${SIZES.filter(s => p.variants[s]).map(s => {
    const vid = p.variants[s];
    return `<div class="sl-row">${pill(s)}<div class="stepper"><button type="button" data-lim="-1" data-vid="${vid}">−</button>
      <input type="number" min="0" inputmode="numeric" data-limin="${vid}" value="${slDraft[vid] || 0}"><button type="button" data-lim="1" data-vid="${vid}">+</button></div></div>`;
  }).join('')}</div>`).join('') || '<p class="muted">No product matches.</p>';
  $('slCount').innerHTML = `<span>प्रोडक्ट · Items carried: <b>${Object.values(slDraft).filter(v => v > 0).length}</b></span>`;
}
$('slSearch').addEventListener('input', () => { $('slSearch').dataset.keep = '1'; drawLimits(); delete $('slSearch').dataset.keep; });
$('slList').addEventListener('click', e => {
  const b = e.target.closest('[data-lim]'); if (!b) return;
  const vid = b.dataset.vid; slDraft[vid] = Math.max(0, (Number(slDraft[vid]) || 0) + Number(b.dataset.lim));
  b.parentElement.querySelector('input').value = slDraft[vid];
  $('slCount').innerHTML = `<span>प्रोडक्ट · Items carried: <b>${Object.values(slDraft).filter(v => v > 0).length}</b></span>`;
});
$('slList').addEventListener('input', e => {
  const el = e.target; if (!el.dataset.limin) return;
  slDraft[el.dataset.limin] = Math.max(0, Math.floor(Number(el.value) || 0));
});
$('slSave').addEventListener('click', () => run($('slSave'), async () => {
  const limits = Object.entries(slDraft).filter(([vid, v]) => (stLimits[vid] || 0) !== v).map(([vid, v]) => ({variant_id: Number(vid), limit: v}));
  if (!limits.length) { go('stock'); return; }
  await api('/api/my/stock/limits', {method: 'PUT', body: {limits}});
  toast('✔ लिमिट सेव · Limits saved'); await go('stock');
}));

async function loadStockAdmin() {
  const d = await api('/api/admin/stock');
  $('saHead').innerHTML = '<tr>' + th('Product · Size') + d.salespeople.map(u => th(u.name, 1)).join('') + '</tr>';
  $('saBody').innerHTML = d.rows.length ? d.rows.map(r => `<tr><td>${esc(r.product)} ${pill(r.size, true)}</td>${d.salespeople.map(u => {
    const c = r.by_user[u.id]; if (!c) return '<td class="num muted">–</td>';
    return `<td class="num st-cell ${c.level}"><b>${c.have}</b> <span class="muted">/ ${c.limit}</span></td>`;
  }).join('')}</tr>`).join('') : `<tr><td colspan="${d.salespeople.length + 1}" class="empty">No salesperson has set stock limits yet.</td></tr>`;
  const kinds = {refill: '🔄 Refill', add: '＋ Added'};
  $('saMoves').innerHTML = d.moves.length ? d.moves.map(m => `<tr><td>${esc(m.date)}</td><td>${esc(m.time)}</td><td>${esc(m.who)}</td>
    <td>${esc(m.item)}</td><td>${kinds[m.kind] || m.kind}</td><td class="num">+${m.qty}</td></tr>`).join('')
    : '<tr><td colspan="6" class="empty">No refills yet.</td></tr>';
}

/* ============================== Masala Parivaar ============================== */
let rw = null, rwProducts = [];
async function loadRwShops() {
  shops = (await api('/api/shops')).shops;
  drawRwShops();
}
function drawRwShops() {
  const q = $('rwSearch').value.trim().toLowerCase();
  const list = shops.filter(s => !q || (s.name + ' ' + s.area).toLowerCase().includes(q))
    .sort((a, b) => (b.mine - a.mine) || (b.my_last_sale || '').localeCompare(a.my_last_sale || '') || a.name.localeCompare(b.name)).slice(0, 60);
  $('rwShopList').innerHTML = list.length ? list.map(s => `<button type="button" data-sid="${s.id}"><b>${esc(s.name)}</b>${s.area ? ` <span class="muted">· ${esc(s.area)}</span>` : ''}</button>`).join('')
    : '<p class="muted">कोई दुकान नहीं मिली · No shop found.</p>';
}
$('rwSearch').addEventListener('input', drawRwShops);
$('rwShopList').addEventListener('click', e => {
  const b = e.target.closest('[data-sid]'); if (b) openRewards(Number(b.dataset.sid), {back: 'rwShops'});
});

async function openRewards(shopId, opts = {}) {
  const q = new URLSearchParams();
  if (opts.msg) q.set('msg', opts.msg);
  if (opts.orderId) q.set('order_id', opts.orderId);
  const data = await api(`/api/rewards/shops/${shopId}?${q}`).catch(e => { toast(e.message, true); return null; });
  if (!data) return;
  if (!rwProducts.length) rwProducts = (await api('/api/products')).products;
  rw = {shopId, back: opts.back || (me.role === 'admin' ? 'rwAdmin' : 'rwShops'), data, size: null, qty: 1,
        todayPts: opts.msg === 'order' ? (data.history.find(h => h.ref === 'order:' + opts.orderId) || {}).points || 0 : 0};
  drawRewards();
  await go('rwPanel');
}
function drawRewards() {
  const d = rw.data, bal = d.balance, c = d.config;
  $('rwShop').innerHTML = `${esc(d.shop.name)}<small>${esc(d.shop.area || '')}</small>`;
  $('rwToday').classList.toggle('hidden', !rw.todayPts);
  $('rwToday').innerHTML = `🎉 आज जुड़े <b>+${rw.todayPts}</b> पॉइंट · Earned today`;
  $('rwBal').textContent = fmt(bal);
  const takeable = d.packs.filter(p => p.can_take > 0);
  $('rwEq').innerHTML = takeable.length ? '= ' + takeable.map(p => `<b>${p.can_take} × ${esc(p.size)}</b>`).join(' या ') + ' फ्री · free'
    : d.packs.length ? `अगला फ्री ${esc(d.packs[0].size)}: <b>${d.packs[0].cost - bal}</b> पॉइंट और · more points` : '';
  if (d.next_bonus_in != null) {
    const done = c.bonus_every - d.next_bonus_in;
    $('rwBar').style.width = (done / c.bonus_every * 100) + '%';
    $('rwBonusTxt').innerHTML = `💰 बचत बोनस · Saving bonus: <b>${done}/${c.bonus_every}</b> — ${d.next_bonus_in} पॉइंट और बचाएं, <b>+${c.bonus_points}</b> मुफ़्त`;
  } else { $('rwBar').style.width = '0'; $('rwBonusTxt').textContent = ''; }
  $('rwExp').classList.toggle('hidden', !d.expiring_30_days);
  $('rwExp').textContent = d.expiring_30_days ? `⏳ ${d.expiring_30_days} पॉइंट ${niceDate(d.next_expiry)} तक इस्तेमाल करें · expire soon` : '';
  $('rwPackForm').classList.add('hidden'); $('rwGiftList').classList.add('hidden');
  $('rwPending').innerHTML = d.pending_gifts.length ? '<div class="rw-pending">' + d.pending_gifts.map(p =>
    `🎁 ${esc(p.name_hi)} · ${esc(p.name_en)} — <b>${p.status === 'approved' ? 'मंज़ूर · approved' : 'इंतज़ार · requested'}</b>`).join('<br>') + '</div>' : '';
  // WhatsApp
  $('rwMsg').textContent = d.whatsapp.text;
  $('rwWa').href = d.whatsapp.url;
  $('rwPhoneRow').classList.toggle('hidden', !!d.shop.phone);
  $('rwAdjust').classList.toggle('hidden', me.role !== 'admin');
  // passbook
  const kindTxt = {earn: '🛒', bonus: '💰', redeem: '🎁', adjust: '✏️', expire: '⏳'};
  $('rwHist').innerHTML = d.history.length ? d.history.map(h => `<tr><td>${esc(h.date)}</td><td>${kindTxt[h.kind] || ''} ${esc(h.label || h.kind)}</td>
    <td class="num ${h.points < 0 ? 'neg' : 'posv'}">${h.points > 0 ? '+' : ''}${h.points}</td><td class="num">${h.balance}</td></tr>`).join('')
    : `<tr><td colspan="4" class="empty">अभी कोई पॉइंट नहीं · No points yet. Points start from ${esc(niceDate(c.start_date))}.</td></tr>`;
}
$('rwHistBtn').addEventListener('click', () => { if (rw) openShopHistory(rw.shopId); });
$('rwBack').addEventListener('click', () => go(rw ? rw.back : 'mysales'));
$('rwSaveBtn').addEventListener('click', () => { toast('💰 पॉइंट बचाए गए · Points saved for later'); go(rw.back); });
$('rwPackBtn').addEventListener('click', () => {
  const d = rw.data;
  if (!d.packs.some(p => p.can_take > 0)) return toast(`अभी पॉइंट कम हैं · Not enough points yet (${d.balance})`, true);
  $('rwGiftList').classList.add('hidden'); $('rwPackForm').classList.remove('hidden');
  rw.size = (d.packs.find(p => p.can_take > 0) || {}).size; rw.qty = 1;
  $('rwSizes').innerHTML = d.packs.map(p => `<button type="button" class="chip ${p.size === rw.size ? 'on' : ''}" data-size="${esc(p.size)}" ${p.can_take ? '' : 'disabled'}>
    ${esc(p.size)} <small>(${p.cost} पॉइंट)</small></button>`).join('');
  $('rwProduct').innerHTML = '<option value="">— फ्लेवर चुनें · choose —</option>' + rwProducts.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  drawPackCost();
});
function drawPackCost() {
  const p = rw.data.packs.find(x => x.size === rw.size); if (!p) return;
  rw.qty = Math.max(1, Math.min(rw.qty, p.can_take));
  $('rwQty').textContent = rw.qty;
  $('rwPackCost').innerHTML = `${rw.qty} × ${esc(p.size)} = <b>${rw.qty * p.cost}</b> पॉइंट · बाकी बचेंगे <b>${rw.data.balance - rw.qty * p.cost}</b> · left`;
}
$('rwSizes').addEventListener('click', e => {
  const b = e.target.closest('[data-size]'); if (!b || b.disabled) return;
  rw.size = b.dataset.size; $('rwSizes').querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x === b)); drawPackCost();
});
$('rwQtyMinus').addEventListener('click', () => { rw.qty--; drawPackCost(); });
$('rwQtyPlus').addEventListener('click', () => { rw.qty++; drawPackCost(); });
$('rwPackConfirm').addEventListener('click', () => {
  if (!$('rwProduct').value) return toast('फ्लेवर चुनें · Choose the flavour', true);
  run($('rwPackConfirm'), async () => {
    const r = await api(`/api/rewards/shops/${rw.shopId}/redeem`, {method: 'POST', body: {kind: 'pack', size: rw.size, quantity: rw.qty, product_id: Number($('rwProduct').value)}});
    rw.data = r.statement; rw.todayPts = 0; drawRewards();
    toast('✔ फ्री पैक दिया गया · Free pack given. Send the WhatsApp message below.');
    $('rwMsg').scrollIntoView({behavior: 'smooth', block: 'center'});
  });
});
$('rwGiftBtn').addEventListener('click', () => {
  const d = rw.data;
  $('rwPackForm').classList.add('hidden'); $('rwGiftList').classList.remove('hidden');
  $('rwGiftList').innerHTML = d.gifts.map(gf => `<div class="gift ${gf.affordable ? 'ok' : ''}">
      <div><b>${esc(gf.name_hi)}</b><div class="muted">${esc(gf.name_en)}</div></div>
      <div class="gift-r"><b>${gf.points}</b> पॉइंट<br>${gf.affordable ? `<button class="btn sm" data-gift="${gf.id}">लें · Request</button>` : `<span class="muted">${gf.points - d.balance} और · more</span>`}</div>
    </div>`).join('') || '<p class="muted">No gifts yet.</p>';
});
$('rwGiftList').addEventListener('click', e => {
  const b = e.target.closest('[data-gift]'); if (!b) return;
  const gf = rw.data.gifts.find(x => x.id === Number(b.dataset.gift));
  if (!confirm(`${gf.name_hi} (${gf.name_en}) के लिए ${gf.points} पॉइंट इस्तेमाल करें?\nUse ${gf.points} points for this gift? The admin will approve and deliver it.`)) return;
  run(b, async () => {
    const r = await api(`/api/rewards/shops/${rw.shopId}/redeem`, {method: 'POST', body: {kind: 'gift', gift_id: gf.id}});
    rw.data = r.statement; rw.todayPts = 0; drawRewards(); toast('✔ गिफ्ट की रिक्वेस्ट भेजी · Gift requested');
  });
});
$('rwPhoneSave').addEventListener('click', () => run($('rwPhoneSave'), async () => {
  await api(`/api/shops/${rw.shopId}/phone`, {method: 'PATCH', body: {phone: $('rwPhone').value}});
  rw.data = await api(`/api/rewards/shops/${rw.shopId}`); drawRewards(); toast('✔ Number saved');
}));
$('rwCopy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(rw.data.whatsapp.text); toast('✔ Copied'); }
  catch (e) { toast('Copy failed: select the text and copy it', true); }
});
$('rwAdjust').addEventListener('submit', e => {
  e.preventDefault();
  run(e.submitter, async () => {
    const r = await api('/api/admin/rewards/adjust', {method: 'POST', body: {shop_id: rw.shopId, points: Number($('rwAdjPts').value), reason: $('rwAdjReason').value}});
    e.target.reset(); rw.data = r.statement; drawRewards(); toast('✔ Points adjusted');
  });
});

/* --- admin overview --- */
let raData = null;
const STATUS_TXT = {requested: 'Requested', approved: 'Approved', delivered: 'Delivered', cancelled: 'Cancelled'};
async function loadRwAdmin() {
  raData = await api(`/api/admin/rewards?from=${$('raFrom').value}&to=${$('raTo').value}`);
  const r = raData, s = r.summary;
  $('raKpis').innerHTML = kpi('Points with shops', fmt(s.outstanding)) + kpi('Shops with points', fmt(s.shops_with_points))
    + kpi('Points redeemed', fmt(s.redeemed_points))
    + kpi('Reward cost', `${s.cost_pct}%<div class="muted" style="font-size:12px;font-weight:600">${money(s.pack_cost + s.gift_cost)} of ${money(s.sales)} sales · target 5–7%</div>`);
  const pend = r.redemptions.filter(x => x.kind === 'gift' && (x.status === 'requested' || x.status === 'approved'));
  $('raPending').innerHTML = pend.length ? pend.map(x => `<tr><td>${esc(x.date)}</td><td>${esc(x.shop_name)} <span class="muted">${esc(x.area || '')}</span></td>
    <td>${esc(x.gift_hi)} · ${esc(x.gift_en)}</td><td class="num">${x.points}</td><td>${STATUS_TXT[x.status]}</td>
    <td class="num" style="white-space:nowrap">${x.status === 'requested' ? `<button class="btn ghost sm" data-rs="approved" data-rid="${x.id}">Approve</button>` : ''}
      <button class="btn sm" data-rs="delivered" data-rid="${x.id}">Delivered</button>
      <button class="btn ghost sm danger" data-rs="cancelled" data-rid="${x.id}">Cancel</button></td></tr>`).join('')
    : '<tr><td colspan="6" class="empty">No gift requests waiting.</td></tr>';
  drawRaShops();
  $('raReds').innerHTML = r.redemptions.length ? r.redemptions.map(x => `<tr><td>${esc(x.date)}</td><td>${esc(x.shop_name)}</td>
    <td>${x.kind === 'pack' ? `Free ${x.quantity} × ${esc(x.size)} ${esc(x.product || '')}` : `🎁 ${esc(x.gift_en)}`}</td><td class="num">${x.points}</td>
    <td>${STATUS_TXT[x.status]}</td><td>${esc(x.by || '')}</td>
    <td class="num">${x.status !== 'cancelled' ? `<button class="btn ghost sm danger" data-rs="cancelled" data-rid="${x.id}">Cancel</button>` : ''}</td></tr>`).join('')
    : '<tr><td colspan="7" class="empty">Nothing redeemed in this period.</td></tr>';
  $('raAdj').innerHTML = r.adjustments.length ? r.adjustments.map(a => `<tr><td>${esc(a.date)}</td><td>${esc(a.shop_name)}</td>
    <td class="num ${a.points < 0 ? 'neg' : 'posv'}">${a.points > 0 ? '+' : ''}${a.points}</td><td>${esc(a.reason)}</td><td>${esc(a.by)}</td></tr>`).join('')
    : '<tr><td colspan="5" class="empty">No adjustments yet. Open a shop\'s passbook to adjust points.</td></tr>';
  $('raGifts').innerHTML = r.gifts.map(gf => `<tr data-gid="${gf.id}">
    <td><input class="cell" data-f="name_hi" value="${esc(gf.name_hi)}"></td><td><input class="cell" data-f="name_en" value="${esc(gf.name_en)}"></td>
    <td class="num"><input class="cell n" data-f="points" type="number" min="1" value="${gf.points}" style="width:90px;min-width:0"></td>
    <td class="num"><input class="cell n" data-f="cost" type="number" min="0" value="${gf.cost}" style="width:100px;min-width:0"></td>
    <td><input type="checkbox" data-f="active" ${gf.active ? 'checked' : ''} style="width:auto"></td></tr>`).join('');
  const c = r.config, numIn = (id, v, label, extra = '') => `<div><label for="${id}">${label}</label><input id="${id}" type="number" min="0" step="1" value="${v}" ${extra}></div>`;
  $('raCfgFields').innerHTML = `
    <div class="cfg-grid"><b>Points earned per pack</b>${SIZES.map(s => numIn('cfgE_' + sizeCls(s), c.earn[s] ?? 0, s)).join('')}</div>
    <div class="cfg-grid"><b>Points for 1 free pack <span class="muted">(0 = not offered)</span></b>${SIZES.map(s => numIn('cfgP_' + sizeCls(s), c.pack_cost[s] ?? 0, 'Free ' + s)).join('')}</div>
    <div class="cfg-grid"><b>Saving bonus</b>${numIn('cfgBE', c.bonus_every, 'Every N points saved')}${numIn('cfgBP', c.bonus_points, 'Bonus points')}</div>
    <div class="cfg-grid"><b>Other</b>${numIn('cfgEx', c.expiry_months, 'Points expire after (months, 0 = never)')}
      <div><label for="cfgStart">Orders earn points from</label><input id="cfgStart" type="date" value="${esc(c.start_date)}"></div>
      <div><label for="cfgCC">WhatsApp country code</label><input id="cfgCC" value="${esc(c.country_code || '91')}" maxlength="4"></div></div>`;
}
function drawRaShops() {
  const q = $('raShopFilter').value.trim().toLowerCase();
  const list = raData.shops.filter(s => !q || (s.name + ' ' + s.area + ' ' + (s.phone || '')).toLowerCase().includes(q));
  $('raShops').innerHTML = list.length ? list.map(s => `<tr><td>${esc(s.name)}</td><td>${esc(s.area)}</td><td class="num"><b>${fmt(s.balance)}</b></td>
    <td class="num">${s.next_bonus_in ?? '–'}</td><td class="num">${s.expiring || '–'}</td><td>${esc(s.last_order || '–')}</td>
    <td class="num" style="white-space:nowrap"><button class="btn ghost sm" data-hist="${s.id}">🏪 History</button> <button class="btn ghost sm" data-open="${s.id}">Passbook · WhatsApp</button></td></tr>`).join('')
    : '<tr><td colspan="7" class="empty">No shops.</td></tr>';
}
['raFrom', 'raTo'].forEach(id => $(id).addEventListener('change', () => run(null, loadRwAdmin)));
$('raShopFilter').addEventListener('input', drawRaShops);
$('raShops').addEventListener('click', e => { const h = e.target.closest('[data-hist]'); if (h) return openShopHistory(Number(h.dataset.hist)); });
$('raShops').addEventListener('click', e => { const b = e.target.closest('[data-open]'); if (b) openRewards(Number(b.dataset.open), {back: 'rwAdmin'}); });
$('rwAdmin').addEventListener('click', e => {
  const b = e.target.closest('[data-rs]'); if (!b) return;
  const status = b.dataset.rs;
  if (status === 'cancelled' && !confirm('Cancel this? The points go back to the shop.')) return;
  run(b, async () => {
    const r = await api('/api/admin/redemptions/' + b.dataset.rid, {method: 'PATCH', body: {status}});
    toast('✔ ' + STATUS_TXT[status]);
    if (status === 'delivered' && r.statement) {
      if (confirm('🎉 Gift delivered! Send the congratulations message on WhatsApp now?')) window.open(r.statement.whatsapp.url, '_blank');
    }
    await loadRwAdmin();
  });
});
$('raGiftForm').addEventListener('submit', e => {
  e.preventDefault();
  run(e.submitter, async () => {
    await api('/api/admin/gifts', {method: 'POST', body: {name_hi: $('ngHi').value, name_en: $('ngEn').value, points: $('ngPts').value, cost: $('ngCost').value || 0}});
    e.target.reset(); toast('✔ Gift added'); await loadRwAdmin();
  });
});
$('raGifts').addEventListener('change', e => {
  const tr = e.target.closest('tr[data-gid]'); if (!tr) return;
  const v = f => tr.querySelector(`[data-f="${f}"]`);
  run(null, async () => {
    try {
      await api('/api/admin/gifts/' + tr.dataset.gid, {method: 'PATCH', body: {name_hi: v('name_hi').value, name_en: v('name_en').value,
        points: v('points').value, cost: v('cost').value || 0, active: v('active').checked}});
      toast('✔ Gift updated');
    } catch (err) { await loadRwAdmin(); throw err; }
  });
});
$('raConfig').addEventListener('submit', e => {
  e.preventDefault();
  const val = id => $(id).value;
  const body = {earn: Object.fromEntries(SIZES.map(s => [s, val('cfgE_' + sizeCls(s))])),
                pack_cost: Object.fromEntries(SIZES.map(s => [s, val('cfgP_' + sizeCls(s))])),
                bonus_every: val('cfgBE'), bonus_points: val('cfgBP'), expiry_months: val('cfgEx'), start_date: val('cfgStart'), country_code: val('cfgCC')};
  if (!confirm('Save the new reward rules? Every shop\'s points will be recalculated.')) return;
  run(e.submitter, async () => {
    const r = await api('/api/admin/rewards/config', {method: 'PUT', body});
    REWARDS = {earn: r.config.earn, pack_cost: r.config.pack_cost, start_date: r.config.start_date};
    toast('✔ Rules saved'); await loadRwAdmin();
  });
});

/* ============================== salesperson: History ============================== */
async function loadHistory() {
  const {days} = await api('/api/my/history');
  $('histHead').innerHTML = '<tr>' + th('Date') + th('Shops', 1) + SIZES.map(s => th(s, 1)).join('') + th('Sales ₹', 1) + th('Expenses ₹', 1) + '</tr>';
  $('histBody').innerHTML = days.length ? days.map(d => `<tr class="clickable" data-date="${esc(d.date)}" style="cursor:pointer">
    <td>${esc(niceDate(d.date))}</td><td class="num">${d.shops}</td>${unitCells(d.units)}<td class="num">${money(d.amount)}</td><td class="num">${d.expenses ? money(d.expenses) : '–'}</td></tr>`).join('')
    : `<tr><td colspan="${SIZES.length + 4}" class="empty">You have not saved any sales yet.</td></tr>`;
}
$('histBody').addEventListener('click', e => {
  const r = e.target.closest('[data-date]'); if (!r) return;
  $('msDate').value = r.dataset.date; go('mysales');
});

/* ============================== admin: Dashboard ============================== */
function reportQuery() {
  const q = new URLSearchParams({from: $('dFrom').value, to: $('dTo').value});
  if ($('dUser').value) q.set('user_id', $('dUser').value);
  if ($('dShop').value) q.set('shop_id', $('dShop').value);
  return q.toString();
}
function fillSelect(sel, allLabel, options) {
  const cur = sel.value;
  sel.innerHTML = `<option value="">${allLabel}</option>` + options.map(o => `<option value="${o.id}">${esc(o.label)}</option>`).join('');
  if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
}
async function loadDash() {
  const r = await api('/api/admin/report?' + reportQuery());
  fillSelect($('dUser'), 'All salespeople', r.salespeople.map(u => ({id: u.id, label: u.name})));
  fillSelect($('dShop'), 'All shops', r.shop_options.map(s => ({id: s.id, label: s.name + (s.area ? ' · ' + s.area : '')})));
  $('dashKpis').innerHTML = kpi('Total Revenue', money(r.totals.revenue)) + kpi('Expenses', money(r.totals.expenses)) + kpi('Shops sold to', fmt(r.totals.shops))
    + SIZES.map(s => kpi(s + ' units', fmt(r.totals.units[s]), sizeCls(s))).join('')
    + kpi('No sales today', esc(r.missing_today.length ? r.missing_today.join(', ') : 'Everyone has sales ✔'), 'sm');
  const sizeTh = SIZES.map(s => th(s, 1)).join('');
  $('perUserHead').innerHTML = '<tr>' + th('Salesperson') + th('Days', 1) + th('Shops', 1) + sizeTh + th('Revenue ₹', 1) + th('Expenses ₹', 1) + th('Zero-sale days', 1) + '</tr>';
  $('shopReportHead').innerHTML = '<tr>' + th('Shop') + th('Area') + th('Sales', 1) + sizeTh + th('Revenue ₹', 1) + th('Last sale') + '</tr>';
  $('prodReportHead').innerHTML = '<tr>' + th('Product') + SIZES.map(s => th(s + ' qty', 1) + th(s + ' avg ₹', 1)).join('') + th('Revenue ₹', 1) + '</tr>';
  $('legend').innerHTML = SIZES.map(s => `<span><i class="bg-${sizeCls(s)}"></i>${esc(s)}</span>`).join('');

  $('perUser').innerHTML = r.users.length ? r.users.map(u => `<tr><td>${esc(u.name)}</td><td class="num">${u.days}</td>
    <td class="num">${u.shops}</td>${unitCells(u.units)}<td class="num">${fmt(u.revenue)}</td><td class="num">${fmt(u.expenses)}</td>
    <td class="num">${$('dShop').value ? '–' : `<b class="${u.zero_days ? 'zero-days' : ''}">${u.zero_days}</b>`}</td></tr>`).join('')
    : `<tr><td colspan="${SIZES.length + 6}" class="empty">No salespeople yet. Add them on the Users page.</td></tr>`;

  $('shopReport').innerHTML = r.shops.length ? r.shops.map(s => `<tr><td>${esc(s.name)}</td><td>${esc(s.area)}</td>
    <td class="num">${s.visits}</td>${unitCells(s.units)}
    <td class="num">${fmt(s.revenue)}</td><td>${esc(s.last_sale)}</td></tr>`).join('')
    : `<tr><td colspan="${SIZES.length + 5}" class="empty">No sales in this period.</td></tr>`;

  const qtyOf = p => SIZES.reduce((a, s) => a + p[s].qty, 0);
  const sold = r.products.filter(p => qtyOf(p) > 0);
  const max = Math.max(1, ...sold.map(qtyOf));
  $('bars').innerHTML = sold.length ? sold.map(p => `<div class="bar"><span title="${esc(p.name)}">${esc(p.name)}</span>
    <div class="track">${SIZES.map(s => `<span class="bg-${sizeCls(s)}" style="width:${p[s].qty / max * 100}%" title="${esc(s)} ${p[s].qty}"></span>`).join('')}</div>
    <span style="text-align:right">${SIZES.map(s => fmt(p[s].qty)).join(' / ')}</span></div>`).join('')
    : '<p class="muted">No sales in this period.</p>';

  const avg = v => v == null ? '<span class="muted">–</span>' : fmt(v);
  $('prodReport').innerHTML = r.products.length ? r.products.map(p => `<tr><td>${esc(p.name)}</td>
    ${SIZES.map(s => `<td class="num">${fmt(p[s].qty)}</td><td class="num">${avg(p[s].avg_price)}</td>`).join('')}
    <td class="num">${fmt(SIZES.reduce((a, s) => a + p[s].revenue, 0))}</td></tr>`).join('')
    : `<tr><td colspan="${SIZES.length * 2 + 2}" class="empty">No products yet.</td></tr>`;

  const dmax = Math.max(1, ...r.by_day.map(d => d.revenue));
  $('dayBars').innerHTML = r.by_day.length ? r.by_day.map(d => `<div class="bar"><span>${esc(d.date)}</span>
    <div class="track"><span class="rev" style="width:${d.revenue / dmax * 100}%"></span></div>
    <span style="text-align:right">${money(d.revenue)}</span></div>`).join('')
    : '<p class="muted">No sales in this period.</p>';

  $('recent').innerHTML = r.recent.length ? r.recent.map(o => `
    <div class="order static"><div style="flex:1">
      <div class="shop">${esc(o.shop.name)} <span class="muted" style="font-weight:400">${esc(o.shop.area || '')}</span></div>
      <div class="meta">${esc(niceDate(o.date))} · ${esc(o.user_name)} · ${SIZES.filter(s => o.units[s]).map(s => esc(s) + ' ' + fmt(o.units[s])).join(' · ')}</div>
      <details><summary>${o.items.length} item${o.items.length === 1 ? '' : 's'}</summary><div class="lines">${itemLines(o)}</div></details>
    </div><div class="amt">${money(o.total)}</div></div>`).join('')
    : '<p class="muted">No sales in this period.</p>';
}
['dFrom', 'dTo', 'dUser', 'dShop'].forEach(id => $(id).addEventListener('change', () => run(null, loadDash)));
$('exportBtn').addEventListener('click', () => { window.location = '/api/admin/export.csv?' + reportQuery(); });

/* ============================== salesperson: Expenses ============================== */
let myExps = [], ef = null;
async function loadMyExp() {
  closeExpForm();
  const date = $('meDate').value || serverToday;
  const d = await api('/api/my/expenses?date=' + encodeURIComponent(date));
  myExps = d.expenses; CATS = d.categories || CATS;
  const shown = [...CATS, ...new Set(myExps.map(e => e.category).filter(c => !CATS.includes(c)))];
  const by = Object.fromEntries(shown.map(c => [c, 0])); let total = 0;
  myExps.forEach(e => { by[e.category] += e.amount; total += e.amount; });
  $('meKpis').innerHTML = shown.map(c => kpi(catIcon(c) + ' ' + c, money(by[c]))).join('') + kpi('Total', money(total));
  $('expList').innerHTML = myExps.length ? myExps.map(e => `
    <div class="order" data-eid="${e.id}">
      <div><div class="shop">${catIcon(e.category)} ${esc(e.category)}</div><div class="meta">${esc(e.note || '')}</div></div>
      <div><div class="amt">${money(e.amount)}</div><div class="meta" style="text-align:right">Tap to edit</div></div>
    </div>`).join('')
    : `<div class="empty-card">No expenses on ${esc(niceDate(date))}.<br>Tap <b>+ Add Expense</b> when you spend on petrol, food or anything else for work.</div>`;
}
function openExpForm(e) {
  ef = {id: e ? e.id : null, category: e ? e.category : null};
  $('efTitle').textContent = e ? 'Edit Expense' : 'Add Expense';
  const choices = e && !CATS.includes(e.category) ? [...CATS, e.category] : CATS;
  $('efCats').innerHTML = choices.map(c => `<button type="button" class="chip ${ef.category === c ? 'on' : ''}" data-cat="${esc(c)}">${catIcon(c)} ${esc(c)}</button>`).join('');
  $('efAmount').value = e ? e.amount : '';
  $('efDate').value = e ? e.date : ($('meDate').value || serverToday);
  $('efNote').value = e ? e.note : '';
  $('efDelete').classList.toggle('hidden', !e);
  updateNoteHint();
  $('expForm').classList.remove('hidden');
  $('expForm').scrollIntoView({behavior: 'smooth', block: 'start'});
}
function closeExpForm() { ef = null; $('expForm').classList.add('hidden'); }
function updateNoteHint() { $('efNoteHint').textContent = ef && ef.category && needsNote(ef.category) ? '(required: what was it for?)' : '(optional)'; }
$('newExpBtn').addEventListener('click', () => openExpForm(null));
$('efCancel').addEventListener('click', closeExpForm);
$('meDate').addEventListener('change', () => run(null, loadMyExp));
$('expList').addEventListener('click', e => {
  const c = e.target.closest('[data-eid]'); if (!c) return;
  openExpForm(myExps.find(x => x.id === Number(c.dataset.eid)));
});
$('efCats').addEventListener('click', e => {
  const b = e.target.closest('[data-cat]'); if (!b) return;
  ef.category = b.dataset.cat;
  $('efCats').querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x === b));
  updateNoteHint();
  if (!$('efAmount').value) $('efAmount').focus();
});
$('expForm').addEventListener('submit', e => {
  e.preventDefault();
  if (!ef.category) return toast('Choose a category: ' + CATS.join(', '), true);
  const amount = Number($('efAmount').value);
  if (!(amount > 0)) { $('efAmount').focus(); return toast('Enter the amount spent', true); }
  if (needsNote(ef.category) && !$('efNote').value.trim()) { $('efNote').focus(); return toast(`For '${ef.category}', write what the money was spent on`, true); }
  run($('efSave'), async () => {
    const body = {date: $('efDate').value, category: ef.category, amount, note: $('efNote').value};
    if (ef.id) await api('/api/my/expenses/' + ef.id, {method: 'PUT', body});
    else await api('/api/my/expenses', {method: 'POST', body});
    toast(`✔ ${body.category} expense of ${money(amount)} saved`);
    $('meDate').value = body.date; await loadMyExp();
  });
});
$('efDelete').addEventListener('click', () => {
  if (!ef || !ef.id || !confirm('Delete this expense? This cannot be undone.')) return;
  run($('efDelete'), async () => { await api('/api/my/expenses/' + ef.id, {method: 'DELETE'}); toast('Expense deleted'); await loadMyExp(); });
});

/* ============================== admin: Expenses ============================== */
function expQuery() {
  const q = new URLSearchParams({from: $('aeFrom').value, to: $('aeTo').value});
  if ($('aeUser').value) q.set('user_id', $('aeUser').value);
  if ($('aeCat').value) q.set('category', $('aeCat').value);
  return q.toString();
}
async function loadAdminExp() {
  const [r, cats] = await Promise.all([api('/api/admin/expenses?' + expQuery()), api('/api/admin/expense-categories')]);
  const C = r.categories;
  fillSelect($('aeUser'), 'All salespeople', r.salespeople.map(u => ({id: u.id, label: u.name})));
  fillSelect($('aeCat'), 'All categories', C.map(c => ({id: esc(c), label: c})));
  $('aeKpis').innerHTML = kpi('Total expenses', money(r.total)) + C.map(c => kpi(catIcon(c) + ' ' + c, money(r.by_category[c]))).join('');
  $('aeUserHead').innerHTML = '<tr>' + th('Salesperson') + C.map(c => th(c + ' ₹', 1)).join('') + th('Total ₹', 1) + '</tr>';
  $('aeUsers').innerHTML = r.users.length ? r.users.map(u => `<tr><td>${esc(u.name)}</td>
    ${C.map(c => `<td class="num">${fmt(u.by_category[c])}</td>`).join('')}<td class="num"><b>${fmt(u.total)}</b></td></tr>`).join('')
    : `<tr><td colspan="${C.length + 2}" class="empty">No expenses in this period.</td></tr>`;
  drawCats(cats.categories);
  $('aeList').innerHTML = r.expenses.length ? r.expenses.map(e => `<tr><td>${esc(e.date)}</td><td>${esc(e.user_name)}</td>
    <td style="white-space:nowrap">${catIcon(e.category)} ${esc(e.category)}</td><td>${esc(e.note)}</td><td class="num">${fmt(e.amount)}</td></tr>`).join('')
    : '<tr><td colspan="5" class="empty">No expenses in this period.</td></tr>';
}
['aeFrom', 'aeTo', 'aeUser', 'aeCat'].forEach(id => $(id).addEventListener('change', () => run(null, loadAdminExp)));
$('aeExport').addEventListener('click', () => { window.location = '/api/admin/expenses.csv?' + expQuery(); });

function drawCats(list) {
  CATS = list.map(c => c.name);
  $('noteCatName').textContent = NOTE_CAT;
  $('catBody').innerHTML = list.map(c => `<tr>
    <td><div style="display:flex;align-items:center;gap:8px;max-width:360px"><span>${catIcon(c.name)}</span><input class="cell" maxlength="40" data-cid="${c.id}" data-orig="${esc(c.name)}" value="${esc(c.name)}"></div></td>
    <td class="num">${c.uses}</td>
    <td class="num"><button class="btn ghost sm danger" data-delcat="${c.id}" data-name="${esc(c.name)}">Remove</button></td></tr>`).join('');
}
$('addCatForm').addEventListener('submit', e => {
  e.preventDefault();
  run(e.submitter, async () => {
    const name = $('ncName').value.trim(); if (!name) return toast('Enter a category name', true);
    await api('/api/admin/expense-categories', {method: 'POST', body: {name}});
    $('ncName').value = ''; toast('✔ Category added: ' + name); await loadAdminExp();
  });
});
$('catBody').addEventListener('change', e => {
  const el = e.target; if (!el.dataset.cid) return;
  run(null, async () => {
    try {
      await api('/api/admin/expense-categories/' + el.dataset.cid, {method: 'PATCH', body: {name: el.value}});
      toast('✔ Renamed to ' + el.value.trim()); await loadAdminExp();
    } catch (err) { el.value = el.dataset.orig; throw err; }
  });
});
$('catBody').addEventListener('click', e => {
  const b = e.target.closest('[data-delcat]'); if (!b) return;
  if (!confirm(`Remove the category "${b.dataset.name}"? Salespeople won't see it any more. Past expenses keep it.`)) return;
  run(b, async () => { await api('/api/admin/expense-categories/' + b.dataset.delcat, {method: 'DELETE'}); toast('Category removed'); await loadAdminExp(); });
});

/* ============================== admin: Shops ============================== */
let adminShops = [];
async function loadShopsAdmin() {
  adminShops = (await api('/api/admin/shops')).shops;
  drawShopsAdmin();
}
function drawShopsAdmin() {
  const q = $('shopFilter').value.trim().toLowerCase();
  const list = adminShops.filter(s => !q || (s.name + ' ' + s.area + ' ' + s.phone).toLowerCase().includes(q));
  $('shopBody').innerHTML = list.length ? list.map(s => `<tr data-sid="${s.id}">
    <td><input class="cell" data-f="name" maxlength="80" value="${esc(s.name)}"></td>
    <td><input class="cell" data-f="area" maxlength="80" value="${esc(s.area)}"></td>
    <td><input class="cell" data-f="phone" maxlength="20" value="${esc(s.phone)}" style="min-width:100px"></td>
    <td>${esc(s.added_by || '–')}</td><td class="num">${s.visits}</td><td class="num">${fmt(s.revenue)}</td><td>${esc(s.last_sale || '–')}</td>
    <td class="num" style="white-space:nowrap"><button class="btn ghost sm" data-hist="${s.id}">🏪 History</button> <button class="btn ghost sm danger" data-del="${s.id}">Remove</button></td></tr>`).join('')
    : `<tr><td colspan="8" class="empty">${adminShops.length ? 'No shop matches your search.' : 'No shops yet. Salespeople add shops when they record a sale.'}</td></tr>`;
}
$('shopFilter').addEventListener('input', drawShopsAdmin);
$('shopBody').addEventListener('change', e => {
  const tr = e.target.closest('tr[data-sid]'); if (!tr || !e.target.dataset.f) return;
  const s = adminShops.find(x => x.id === Number(tr.dataset.sid));
  const val = f => tr.querySelector(`[data-f="${f}"]`).value;
  run(null, async () => {
    try {
      await api('/api/admin/shops/' + s.id, {method: 'PATCH', body: {name: val('name'), area: val('area'), phone: val('phone')}});
      Object.assign(s, {name: val('name').trim(), area: val('area').trim(), phone: val('phone').trim()}); toast('✔ Shop updated');
    } catch (err) { drawShopsAdmin(); throw err; }
  });
});
$('shopBody').addEventListener('click', e => { const h = e.target.closest('[data-hist]'); if (h) openShopHistory(Number(h.dataset.hist)); });
$('shopBody').addEventListener('click', e => {
  const b = e.target.closest('[data-del]'); if (!b) return;
  const s = adminShops.find(x => x.id === Number(b.dataset.del));
  if (!confirm(`Remove "${s.name}"? Salespeople will no longer see it. Its past sales stay in reports.`)) return;
  run(b, async () => { await api('/api/admin/shops/' + s.id, {method: 'DELETE'}); toast('Shop removed'); await loadShopsAdmin(); });
});

/* ============================== admin: Products ============================== */
async function loadProducts() {
  const {products: list} = await api('/api/admin/products');
  $('sizeList').textContent = SIZES.join(', ');
  $('prodHead').innerHTML = '<tr>' + th('Product name') + th('Sizes') + th('Avg selling price ₹ (' + SIZES.join(' / ') + ')', 1) + '<th></th></tr>';
  const avg = v => v == null ? '–' : fmt(v);
  $('prodBody').innerHTML = list.length ? list.map(p => `<tr>
    <td><input class="name" maxlength="80" data-pid="${p.id}" data-orig="${esc(p.name)}" value="${esc(p.name)}"></td>
    <td style="white-space:nowrap">${SIZES.map(s => pill(s)).join(' ')}</td>
    <td class="num">${SIZES.map(s => avg(p.avg_price[s])).join(' / ')}</td>
    <td class="num"><button class="btn ghost sm danger" data-del="${p.id}" data-name="${esc(p.name)}">Remove</button></td></tr>`).join('')
    : '<tr><td colspan="4" class="empty">No products yet. Add your first product above.</td></tr>';
}
$('addProdForm').addEventListener('submit', e => {
  e.preventDefault();
  run(e.submitter, async () => {
    const name = $('npName').value.trim(); if (!name) return toast('Enter a product name', true);
    await api('/api/admin/products', {method: 'POST', body: {name}});
    $('npName').value = ''; toast('✔ ' + name + ' added in ' + SIZES.join(', ')); await loadProducts();
  });
});
$('prodBody').addEventListener('change', e => {
  const el = e.target; if (!el.dataset.pid) return;
  run(null, async () => {
    try {
      await api('/api/admin/products/' + el.dataset.pid, {method: 'PATCH', body: {name: el.value}});
      el.dataset.orig = el.value.trim(); toast('✔ Renamed to ' + el.value.trim());
    } catch (err) { el.value = el.dataset.orig; throw err; }
  });
});
$('prodBody').addEventListener('click', e => {
  const b = e.target.closest('[data-del]'); if (!b) return;
  if (!confirm(`Remove "${b.dataset.name}"? Salespeople will no longer see it. Its past sales stay in reports.`)) return;
  run(b, async () => { await api('/api/admin/products/' + b.dataset.del, {method: 'DELETE'}); toast('Product removed'); await loadProducts(); });
});

/* ============================== admin: Users ============================== */
async function loadUsers() {
  const {users, me: myId} = await api('/api/admin/users');
  $('userBody').innerHTML = users.map(u => `<tr>
    <td>${esc(u.full_name)}${u.id === myId ? ' <span class="muted">(you)</span>' : ''}</td><td>${esc(u.username)}</td>
    <td>${u.role === 'admin' ? 'Admin' : 'Salesperson'}</td>
    <td><span class="pill ${u.active ? 'on' : 'off'}">${u.active ? 'Active' : 'Inactive'}</span></td>
    <td class="num" style="white-space:nowrap">
      <button class="btn ghost sm" data-reset="${u.id}" data-name="${esc(u.full_name)}">Reset password</button>
      ${u.id === myId ? '' : `<button class="btn ghost sm ${u.active ? 'danger' : ''}" data-toggle="${u.id}" data-active="${u.active}">${u.active ? 'Deactivate' : 'Activate'}</button>`}
    </td></tr>`).join('');
}
$('addUserForm').addEventListener('submit', e => {
  e.preventDefault();
  run(e.submitter, async () => {
    const body = {full_name: $('nuName').value, username: $('nuUser').value.trim(), password: $('nuPass').value, role: $('nuRole').value};
    await api('/api/admin/users', {method: 'POST', body});
    toast(`✔ User ${body.username} created. Share the password with them.`); e.target.reset(); await loadUsers();
  });
});
$('userBody').addEventListener('click', e => {
  const reset = e.target.closest('[data-reset]'), tog = e.target.closest('[data-toggle]');
  if (reset) {
    const pw = prompt(`New password for ${reset.dataset.name} (min 6 characters):`); if (!pw) return;
    run(reset, async () => { await api('/api/admin/users/' + reset.dataset.reset, {method: 'PATCH', body: {password: pw}}); toast('✔ Password changed'); });
  } else if (tog) {
    const active = tog.dataset.active !== '1';
    run(tog, async () => { await api('/api/admin/users/' + tog.dataset.toggle, {method: 'PATCH', body: {active}}); await loadUsers(); });
  }
});

/* ============================== account ============================== */
$('pwForm').addEventListener('submit', e => {
  e.preventDefault();
  if ($('pwNew').value !== $('pwNew2').value) return toast('New passwords do not match', true);
  run(e.submitter, async () => {
    await api('/api/me/password', {method: 'POST', body: {current: $('pwCur').value, new: $('pwNew').value}});
    e.target.reset(); toast('✔ Password changed');
  });
});

/* ============================== start ============================== */
api('/api/me').then(showApp).catch(() => showLogin());
