'use strict';
const $ = (s, root = document) => root.querySelector(s);
const workspace = $('#workspace');
const page = document.body.dataset.page;
const tossCategories = JSON.parse(document.body.dataset.tossCategories);
const filters = {budget:null, account:'', category:''};
const writable = document.body.dataset.writes === 'true';
if (['investments','settings'].includes(page)) $('.month-picker').hidden=true;
const csrf = $('meta[name="csrf-token"]').content;
const monthInput = $('#month');
monthInput.value = new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Seoul',year:'numeric',month:'2-digit'}).format(new Date());
const today = () => new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Seoul'}).format(new Date());
const money = (n, c='KRW') => `${Number(n || 0).toLocaleString('ko-KR')} ${c === 'KRW' ? '원' : c}`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {name:'이름',type:'유형',institution:'금융기관',currency:'통화',active:'사용 여부',category:'분류',investment_category:'투자 분류',item:'항목',month:'월',amount:'금액',flow_type:'흐름',note:'메모',day:'매월 일자',start_month:'시작월',end_month:'종료월',date:'기준일',account_id:'계좌',asset_id:'자산',loan_id:'대출',original_principal:'최초 원금',annual_rate:'연이율 (소수)',start_date:'시작일',maturity_date:'만기일',balance:'잔액',symbol:'종목 코드',cost_basis:'취득 원가',market_value:'평가액',payment_date:'배당 지급일',gross_amount:'총배당',tax_amount:'세금',net_amount:'순배당',trade_date:'매도일',profit:'실현 손익',version:'버전',status:'상태',revision:'리비전',net_worth:'순자산',closed_at:'마감 시각',cost:'취득 원가',unrealized_profit:'평가 손익',dividend_total:'순배당 누계',realized_profit:'실현 손익',total_profit:'총손익',simple_return_pct:'단순 수익률 (%)',value:'값',key:'설정',description:'설명',source_ref:'원본',assets_total:'자산',liabilities_total:'부채',budget_balance:'예산 잔액',portfolio_profit:'투자 손익',display_name:'표시 이름'};
const words = {income:'수입',expense:'소비',investment:'투자 배분',transfer:'계좌 이체',debt_principal:'원금 상환',bank:'은행',cash:'현금',brokerage:'증권',retirement:'퇴직연금',other:'기타',deposit:'예금',property:'부동산',open:'진행 중',preparing:'마감 중',closed:'마감',true:'사용',false:'미사용'};
const numeric = new Set(['amount','day','original_principal','annual_rate','balance','cost_basis','market_value','gross_amount','tax_amount','net_amount','profit']);
const moneyKeys = new Set(['amount','original_principal','balance','cost_basis','market_value','gross_amount','tax_amount','net_amount','profit','net_worth','assets_total','liabilities_total','unrealized_profit','dividend_total','realized_profit','total_profit','budget_balance','portfolio_profit']);
const schemas = {
 'accounts':['name','type','institution','currency','active'],
 'asset-items':['account_id','category','name','active'],
 'budgets':['month','category','item','amount','flow_type','note'],
 'recurring-items':['item','category','flow_type','day','amount','start_month','end_month','active'],
 'asset-snapshots':['date','asset_id','amount','note'],
 'liabilities':['name','original_principal','annual_rate','start_date','maturity_date','active'],
 'liability-snapshots':['date','loan_id','balance'],
 'holdings':['date','account_id','symbol','name','cost_basis','market_value','currency','investment_category'],
 'dividends':['payment_date','account_id','symbol','gross_amount','tax_amount','net_amount','currency','note','investment_category'],
 'realized-gains':['trade_date','account_id','symbol','profit','currency','note','investment_category']
};
const titles = {'accounts':'계좌','asset-items':'자산 항목','budgets':'예산','recurring-items':'반복 예산','asset-snapshots':'자산 잔액','liabilities':'대출','liability-snapshots':'대출 잔액','holdings':'보유종목','dividends':'배당','realized-gains':'실현 손익'};
const idKeys = {'accounts':'account_id','asset-items':'asset_id','budgets':'budget_id','recurring-items':'recurring_id','asset-snapshots':'snapshot_id','liabilities':'loan_id','liability-snapshots':'snapshot_id','holdings':'holding_id','dividends':'dividend_id','realized-gains':'gain_id'};
let collections = {}, editor = null, closingPreview = null, settings = null, loadSequence = 0;
const pendingRequests = new Map();
async function api(path, method='GET', data) {
 const signature = JSON.stringify([path,method,data]);
 const rid = pendingRequests.get(signature) || crypto.randomUUID();
 if (method !== 'GET') pendingRequests.set(signature,rid);
 let response;
 try { response = await fetch('/api/v1'+path,{method,headers:{'Content-Type':'application/json','X-CSRFToken':csrf,'X-Request-ID':rid},body:data === undefined ? undefined : JSON.stringify(data)}); }
 catch (_) { throw new Error('연결을 확인하고 같은 입력으로 다시 저장하세요. 요청 ID를 유지합니다.'); }
 const result = await response.json();
 if (response.status < 500) pendingRequests.delete(signature);
 if (response.status === 401) { location.href='/login'; throw new Error('로그인이 필요합니다.'); }
 if (!response.ok) throw new Error(`${result.error?.message || '요청에 실패했습니다.'} (요청 ${result.request_id || rid})`);
 return result.data;
}
function message(text, error=false) { const el=$('#message'); el.textContent=text; el.className=error?'error':''; el.hidden=false; }
const button = (resource, text) => `<button data-add="${resource}" ${writable?'':'disabled'}>${esc(text || '+ '+titles[resource])}</button>`;
const empty = (text='아직 기록이 없습니다. 첫 항목을 추가해 시작하세요.') => `<div class="empty"><div class="empty-icon">＋</div>${esc(text)}</div>`;
function panel(title, content, actions='', description='') { return `<section class="panel"><div class="panel-head"><div><h2>${esc(title)}</h2>${description?`<p class="caption">${esc(description)}</p>`:''}</div><div class="actions">${actions}</div></div>${content}</section>`; }
function card(title, value, hint, highlight=false) { return `<article class="card ${highlight?'highlight':''}"><p class="card-label">${esc(title)}</p><div class="metric">${esc(value)}</div><span class="caption">${esc(hint)}</span></article>`; }
function display(row,key) {
 if (moneyKeys.has(key)) return money(row[key],row.currency || 'KRW');
 if (key==='closed_at') return row[key] ? new Date(row[key]).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}) : '—';
 if (key==='account_id') return collections.accounts?.find(a=>a.account_id===row[key])?.name || row[key];
 if (key==='asset_id') return collections['asset-items']?.find(a=>a.asset_id===row[key])?.name || row[key];
 if (key==='loan_id') return collections.liabilities?.find(a=>a.loan_id===row[key])?.name || row[key];
 return words[String(row[key])] || row[key] || (row[key]===0?'0':'—');
}
function table(rows, fields, resource) {
 if (!rows.length) return empty();
 return `<div class="table-wrap"><table><thead><tr>${fields.map(f=>`<th scope="col">${esc(labels[f] || f)}</th>`).join('')}${resource?'<th scope="col">관리</th>':''}</tr></thead><tbody>${rows.map(r=>`<tr>${fields.map(f=>`<td>${esc(display(r,f))}</td>`).join('')}${resource?`<td><button data-edit="${resource}" data-id="${esc(r[idKeys[resource]])}" ${writable?'':'disabled'}>수정</button>${resource==='budgets'?`<button data-delete="${esc(r.budget_id)}" ${writable?'':'disabled'}>삭제</button>`:''}</td>`:''}</tr>`).join('')}</tbody></table></div>`;
}
function chart(rows) {
 if (!rows.length) return empty('월마감을 완료하면 순자산 추이가 여기에 표시됩니다.');
 rows=[...rows].sort((a,b)=>a.month.localeCompare(b.month));
 const values=rows.map(r=>Number(r.net_worth)), min=Math.min(0,...values), max=Math.max(1,...values), span=max-min || 1;
 const points=values.map((v,i)=>`${35+i*530/Math.max(values.length-1,1)},${145-(v-min)/span*110}`).join(' ');
 return `<svg class="chart" viewBox="0 0 600 180" role="img" aria-label="마감 월별 순자산 추이"><line x1="35" x2="565" y1="145" y2="145"/><polyline points="${points}"/>${values.map((v,i)=>`<circle cx="${35+i*530/Math.max(values.length-1,1)}" cy="${145-(v-min)/span*110}" r="4" fill="#176b55"><title>${esc(rows[i].month+': '+money(v))}</title></circle>`).join('')}<text x="35" y="170">${esc(rows[0].month)}</text><text x="510" y="170">${esc(rows.at(-1).month)}</text><text x="35" y="18">${esc(money(max))}</text></svg>`;
}
function budgetBars(b) {const flows=['income','expense','investment','debt_principal']; const max=Math.max(1,...flows.map(f=>b[f]));return flows.map(f=>`<div class="bar-row"><span>${words[f]}</span><progress value="${Number(b[f])}" max="${max}" aria-label="${words[f]}"></progress><strong>${money(b[f])}</strong></div>`).join('');}
async function getCollection(name, query='') {
 const sequence=loadSequence;
 const rows=await api('/'+name+query);
 if (sequence===loadSequence) collections[name]=rows;
 return rows;
}
const investmentAccount = account => ['brokerage','retirement'].includes(account.type);
const tossAccount = account => account && [account.name,account.institution].some(value=>/토스|toss/i.test(value));
const accountById = id => collections.accounts?.find(account=>account.account_id===id);
function selectOptions(choices, selected, placeholder) {
 return `<option value="">${esc(placeholder)}</option>`+choices.map(([value,label])=>
  `<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`).join('');
}
function filterSelect(key, label, choices, placeholder) {
 return `<label>${label}<select data-filter="${key}">${selectOptions(choices,filters[key],placeholder)}</select></label>`;
}
function investmentFilters(accounts) {
 const eligible=accounts.filter(investmentAccount);
 if (!eligible.some(account=>account.account_id===filters.account)) filters.account='';
 const toss=tossAccount(eligible.find(account=>account.account_id===filters.account));
 if (!toss) filters.category='';
 const account=filterSelect('account','계좌',eligible.map(a=>[a.account_id,a.name]),'전체 계좌');
 const category=toss?filterSelect('category','투자 분류',tossCategories.map(c=>[c,c]),'전체 분류'):'';
 return `<div class="record-filters">${account}${category}</div>`;
}
function investmentQuery(params={}) {
 if (filters.account) params.account_id=filters.account;
 if (filters.category) params.investment_category=filters.category;
 return new URLSearchParams(params);
}
function filteredInvestments(rows) {
 return rows.filter(row=>(!filters.account || row.account_id===filters.account)
  && (!filters.category || row.investment_category===filters.category));
}
function budgetCategoryTotals(rows) {
 const totals=new Map();
 for (const row of rows) {
  const category=row.category || '';
  totals.set(category,(totals.get(category) ?? 0)+Number(row.amount));
 }
 return [...totals].sort(([a],[b])=>a.localeCompare(b,'ko'));
}
function budgetFilterButtons(categories) {
 if (!categories.length) return '';
 const choices=[[null,'전체'],...categories.map(([category])=>[category,category || '미분류'])];
 return `<div class="budget-filters" role="group" aria-label="상세 예산 분류">${choices.map(([value,label])=>
  `<button type="button" data-budget-filter="${value===null?'all':'category'}" value="${esc(value)}" aria-pressed="${filters.budget===value}">${esc(label)}</button>`
 ).join('')}</div>`;
}
function budgetDetailTable(rows) {
 const visible=filters.budget===null?rows:rows.filter(row=>(row.category || '')===filters.budget);
 return table(visible,['category','item','flow_type','amount','note'],'budgets');
}
async function render(focusFilter=null) {
 const sequence=++loadSequence;
 $('#loading').hidden=false;
 workspace.setAttribute('aria-busy','true');
 closingPreview=null;
 try {
  const month=monthInput.value;
  if (!month) throw new Error('조회 월을 선택하세요.');
  const year=month.slice(0,4);
  let html='';
  if (page==='dashboard') {
   const d=await api('/dashboard?month='+month), k=d.assets.by_currency.KRW;
   html=`<div class="cards">${card('순자산',money(k.net_worth),'자산 − 부채 · 월말 기준',true)}${card('총자산',money(k.assets_total),'보유종목 평가액 포함')}${card('총부채',money(k.liabilities_total),'최근 대출 잔액 기준')}${card('예산 잔액',money(d.budget.budget_balance),'투자 배분 · 원금 상환 반영')}</div><div class="columns">${panel('순자산의 흐름',chart(d.history),'','마감된 월의 확정 내역')}${panel('이번 달 예산',budgetBars(d.budget),'<a href="/budgets">예산 관리 ↗</a>')}</div>`;
   const foreign=Object.entries(d.assets.by_currency).filter(([c])=>c!=='KRW');
   if(foreign.length) html+=panel('외화 자산',table(foreign.map(([currency,v])=>({currency,...v})),['currency','assets_total','net_worth']),'','KRW 총액에 합산하지 않습니다.');
   html+=panel('투자 현황',table(d.portfolio,['name','currency','market_value','total_profit']),'<a href="/investments">투자 관리 ↗</a>');
  } else if(page==='budgets') {
   const [rows, recurring, summary]=await Promise.all([
    getCollection('budgets','?month='+month),getCollection('recurring-items'),api('/budget-summary?month='+month),
   ]);
   if (sequence!==loadSequence) return;
   const categories=budgetCategoryTotals(rows);
   if (!categories.some(([category])=>category===filters.budget)) filters.budget=null;
   html=`<div class="cards">${card('수입',money(summary.income),'월 예산')}${card('소비',money(summary.expense),'투자 및 원금 상환 제외')}${card('투자 배분',money(summary.investment),'자산으로 이동하는 금액')}${card('예산 잔액',money(summary.budget_balance),'수입 − 소비 − 투자 − 원금 상환',true)}</div>`;
   html+=panel('월별 예산 항목',table(categories.map(([category,amount])=>({category:category || '미분류',amount})),['category','amount']));
   html+=panel('상세 예산 항목',budgetFilterButtons(categories)+`<div id="budget-details">${budgetDetailTable(rows)}</div>`,
    `<button data-action="copy" ${writable?'':'disabled'}>다른 월에서 복사</button>${button('budgets')}`);
   html+=panel('반복 예산 템플릿',table(recurring,['item','flow_type','day','amount','start_month','end_month','active'],'recurring-items'),button('recurring-items'),'반복 항목은 템플릿으로 저장됩니다. 월 예산에는 자동으로 더하지 않습니다.');
  } else if(page==='assets') {
   const [accounts, items, loans, snaps, debts, summary]=await Promise.all([getCollection('accounts'),getCollection('asset-items'),getCollection('liabilities'),getCollection('asset-snapshots'),getCollection('liability-snapshots'),api('/assets?date='+month+'-'+new Date(Number(year),Number(month.slice(5)),0).getDate())]);
   const k=summary.by_currency.KRW;
   html=`<div class="cards">${card('순자산',money(k.net_worth),'선택 월말 기준',true)}${card('일반 자산 + 투자',money(k.assets_total),'KRW')}${card('대출 잔액',money(k.liabilities_total),'KRW')}${card('등록 자산',items.length+'개','투자 계좌는 보유종목으로 관리')}</div>`;
   html+=panel('자산 항목',table(items,['name','account_id','category','active'],'asset-items'),button('asset-items'));
   html+=panel('자산 잔액 기록',table(snaps,['date','asset_id','amount','note'],'asset-snapshots'),button('asset-snapshots'));
   html+=panel('대출',table(loans,['name','original_principal','annual_rate','maturity_date'],'liabilities'),button('liabilities'),'대출은 KRW로 관리합니다. 연이율 예: 4.012% → 0.04012');
   html+=panel('대출 잔액 기록',table(debts,['date','loan_id','balance'],'liability-snapshots'),button('liability-snapshots'));
  } else if(page==='investments') {
   const [accounts, holdings, gains]=await Promise.all([getCollection('accounts'),getCollection('holdings'),getCollection('realized-gains')]);
   if (sequence!==loadSequence) return;
   html=investmentFilters(accounts);
   const summary=await api('/portfolio-summary?'+investmentQuery());
   html+=panel('계좌별 수익',table(summary,['name','currency','cost_basis','market_value','unrealized_profit','dividend_total','realized_profit','total_profit','simple_return_pct']),'','계좌·종목별 최신 평가액. 순배당과 실현 손익은 누적 값이며 단순 수익률은 시간가중 수익률이 아닙니다.');
   html+=panel('보유종목 스냅샷',table(filteredInvestments(holdings),['date','account_id','symbol','investment_category','cost_basis','market_value','currency'],'holdings'),button('holdings'),'청산 시 보유 원가와 평가액을 0으로 기록하세요. 계좌 현금은 CASH 종목으로 관리합니다.');
   html+=panel('실현 손익',table(filteredInvestments(gains),['trade_date','account_id','symbol','investment_category','profit','currency'],'realized-gains'),button('realized-gains'));
  } else if(page==='dividends') {
   const [accounts, rows]=await Promise.all([getCollection('accounts'),getCollection('dividends','?year='+year)]);
   if (sequence!==loadSequence) return;
   html=investmentFilters(accounts);
   const summary=await api('/dividend-summary?'+investmentQuery({year}));
   html+=panel(year+'년 월별 배당',table(summary,['month','currency','gross_amount','tax_amount','net_amount']),'','배당은 순자산에 별도 가산하지 않습니다. 현금 잔액에서 한 번 반영하세요.');
   html+=panel('배당 내역',table(filteredInvestments(rows),['payment_date','account_id','symbol','investment_category','gross_amount','tax_amount','net_amount','currency'],'dividends'),button('dividends'));
  } else if(page==='history') {
   const [rows, history]=await Promise.all([api('/closings'),api('/net-worth/history')]);
   collections.closings=rows;
   html=panel('월마감',`<p class="section-note">${esc(month)}월의 잔액과 예산을 검토하고 확정합니다. 마감한 기간은 잠기며 재개할 때 새 리비전을 생성합니다.</p><div id="close-preview"></div>`,`<button data-action="preview" ${writable?'':'disabled'}>마감 미리보기</button><button data-action="reopen" ${writable?'':'disabled'}>선택 월 재개</button>`);
   html+=panel('순자산 추이',chart(history))+panel('리비전 이력',table(rows,['month','revision','status','net_worth','budget_balance','closed_at']));
  } else if(page==='settings') {
   const [rows,s]=await Promise.all([getCollection('accounts'),api('/settings')]);settings=s;
   html=panel('계좌 관리',table(rows,['name','type','institution','currency','active'],'accounts'),button('accounts'));
   html+=panel('데이터와 표시 설정',table(s.items,['key','value','description']),`<button data-action="display-name" ${writable?'':'disabled'}>표시 이름</button><a href="/api/v1/backup">JSON 백업 내려받기 ↗</a>`);
   html+=panel('분류 기준과 보안','<p class="section-note">예산 흐름: 수입 / 소비 / 투자 배분 / 계좌 이체 / 원금 상환<br>자산 분류: 현금 / 예금 / 부동산 / 기타<br>기준 통화: KRW · 표시 시간대: Asia/Seoul<br>비밀번호 변경: 서버에서 password-hash 명령으로 새 해시를 생성하고 환경 설정을 변경하세요.<br>재무 자료는 외부로 전송되는 차트 라이브러리 없이 이 화면에서 표시합니다.</p>');
  }
  const preferences=page==='settings'?settings:await api('/settings');
  const displayName=preferences.items.find(r=>r.key==='display_name')?.value || '나의 재무';
  if(sequence===loadSequence) {workspace.innerHTML=html;if(focusFilter) $('[data-filter='+focusFilter+']',workspace)?.focus();$('.brand > span:last-child').firstChild.nodeValue=displayName;}
 } catch(e) {if(sequence===loadSequence)message(e.message,true);} finally {if(sequence===loadSequence){$('#loading').hidden=true;workspace.removeAttribute('aria-busy');}}
}
async function openEditor(resource, id) {
 const record=id?collections[resource]?.find(r=>r[idKeys[resource]]===id):null;
 for(const related of ['accounts','asset-items','liabilities']) if(schemas[resource].some(k=>({accounts:'account_id','asset-items':'asset_id',liabilities:'loan_id'}[related])===k) && !collections[related]) await getCollection(related);
 editor={resource,record};
 $('#editor-title').textContent=titles[resource]+(record?' 수정':' 추가');
 $('#editor-error').textContent='';
 const investment=schemas[resource].includes('investment_category');
 $('#editor-fields').innerHTML=schemas[resource].map(key=>{
  if(key==='investment_category') return `<label hidden>투자 분류<select name="investment_category" disabled>${selectOptions(tossCategories.map(c=>[c,c]),record?.investment_category ?? '','미분류')}</select></label>`;
  let value=record?.[key] ?? ({active:true,currency:investment?(accountById(filters.account)?.currency || 'KRW'):'KRW',account_id:investment?filters.account:'',category:resource==='budgets'?(filters.budget ?? ''):'',month:monthInput.value,start_month:monthInput.value,date:today(),payment_date:today(),trade_date:today(),start_date:today(),tax_amount:0}[key] ?? '');
  let choices=key==='type'?['bank','cash','brokerage','retirement','other']:key==='currency'?['KRW','USD']:key==='active'?['true','false']:key==='flow_type'?['income','expense','investment','transfer','debt_principal']:key==='category'&&resource==='asset-items'?['cash','deposit','property','other']:null;
  let options=choices?.map(c=>[c,words[c] || c]);
  const fk={account_id:'accounts',asset_id:'asset-items',loan_id:'liabilities'}[key];
  if(fk) options=(collections[fk] || []).filter(r=>!investment || key!=='account_id' || investmentAccount(r)).map(r=>[r[idKeys[fk]],r.name]);
  const optional=['note','institution','end_month'].includes(key), required=optional?'':'required';
  let field;
  if(options)field=`<select name="${key}" ${required}>${fk?'<option value="">선택하세요</option>':''}${options.map(([v,label])=>`<option value="${esc(v)}" ${String(value)===String(v)?'selected':''}>${esc(label)}</option>`).join('')}</select>`;
  else {const type=numeric.has(key)?'number':key.includes('month')?'month':(key==='date'||key.endsWith('_date'))?'date':'text';field=`<input name="${key}" type="${type}" value="${esc(value)}" ${required} ${type==='number'?`step="${key==='annual_rate'?'any':'1'}" ${key==='profit'?'':'min="0"'}`:''} ${type==='text'?`maxlength="${key==='note'?1000:200}"`:''}>`;}
  return `<label>${esc(labels[key] || key)}${field}</label>`;
 }).join('');
 if(investment) {
  const account=$('[name="account_id"]',$('#editor-fields'));
  const category=$('[name="investment_category"]',$('#editor-fields'));
  const syncCategory=()=>{
   category.disabled=!tossAccount(accountById(account.value));
   category.closest('label').hidden=category.disabled;
   if(category.disabled) category.value='';
  };
  account.addEventListener('change',syncCategory);
  syncCategory();
 }
 $('#editor').showModal();
}
$('#editor-close').onclick=$('#editor-cancel').onclick=()=>$('#editor').close();
$('#editor-form').addEventListener('submit',async e=>{
 e.preventDefault();const save=$('#editor-save');save.disabled=true;$('#editor-error').textContent='';
 try {const data=Object.fromEntries(new FormData(e.target));for(const k in data){if(numeric.has(k))data[k]=Number(data[k]);if(k==='active')data[k]=data[k]==='true';}data.expected_version=editor.record?Number(editor.record.version):0;
  await api('/'+editor.resource+(editor.record?'/'+editor.record[idKeys[editor.resource]]:''),editor.record?'PATCH':'POST',data);
  $('#editor').close();message('저장했습니다.');await render();
 }catch(err){$('#editor-error').textContent=err.message;}finally{save.disabled=false;}
});
workspace.addEventListener('click',async e=>{
 const el=e.target.closest('button');if(!el || el.disabled)return;
 if (el.dataset.budgetFilter) {
  if (workspace.hasAttribute('aria-busy')) return;
  filters.budget=el.dataset.budgetFilter==='all'?null:el.value;
  for (const filter of workspace.querySelectorAll('[data-budget-filter]')) {
   filter.setAttribute('aria-pressed',String(filter===el));
  }
  $('#budget-details').innerHTML=budgetDetailTable(collections.budgets);
  return;
 }
 el.disabled=true;
 try {
  if(el.dataset.add) await openEditor(el.dataset.add);
  else if(el.dataset.edit) await openEditor(el.dataset.edit,el.dataset.id);
  else if(el.dataset.delete){const row=collections.budgets.find(r=>r.budget_id===el.dataset.delete);if(confirm(`“${row.item}” 예산을 삭제할까요?`)){await api('/budgets/'+row.budget_id,'DELETE',{expected_version:Number(row.version)});message('삭제했습니다.');await render();}}
  else if(el.dataset.action==='copy'){const source=prompt('복사할 원본 월 (YYYY-MM)');if(source){await api('/budgets/'+monthInput.value+'/copy','POST',{source_month:source,expected_version:0});message('예산을 복사했습니다.');await render();}}
  else if(el.dataset.action==='display-name'){const name=prompt('표시 이름',settings.items.find(r=>r.key==='display_name')?.value || '나의 재무');if(name){await api('/settings','PATCH',{display_name:name,expected_version:settings.version});message('표시 이름을 저장했습니다.');await render();}}
  else if(el.dataset.action==='preview'){closingPreview=await api('/closings/'+monthInput.value+'/preview','POST',{expected_version:0});$('#close-preview').innerHTML=table([closingPreview.summary],['assets_total','liabilities_total','net_worth','budget_balance','portfolio_profit'])+closingPreview.blockers.map(s=>`<p class="error">${esc(s)}</p>`).join('')+`<p class="caption">상세 내역 ${closingPreview.details.length}개 · 리비전 ${closingPreview.revision}</p><button class="primary" data-action="finalize" ${closingPreview.can_finalize?'':'disabled'}>검토한 내용으로 마감 확정</button>`;}
  else if(el.dataset.action==='finalize'&&closingPreview){await api('/closings/'+closingPreview.month+'/finalize','POST',{expected_version:closingPreview.revision,data_hash:closingPreview.data_hash});message('월마감을 완료했습니다.');await render();}
  else if(el.dataset.action==='reopen'){const rows=collections.closings.filter(r=>r.month===monthInput.value).sort((a,b)=>b.revision-a.revision);if(!rows.length)throw new Error('재개할 마감이 없습니다.');if(confirm('기존 마감은 보존하고 새 리비전으로 재개할까요?')){await api('/closings/'+monthInput.value+'/reopen','POST',{expected_version:Number(rows[0].revision)});message('새 리비전으로 재개했습니다.');await render();}}
 } catch(err){message(err.message,true);} finally{el.disabled=false;}
});
workspace.addEventListener('change',e=>{
 const key=e.target.dataset.filter;
 if (!key) return;
 filters[key]=e.target.value;
 if (key==='account') filters.category='';
 render(key);
});
monthInput.addEventListener('change',()=>render());
render();
