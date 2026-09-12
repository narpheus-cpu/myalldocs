const state={catalog:[],profiles:new Map(),selectedFolder:null,lastListHash:"search"};
const $=s=>document.querySelector(s); const $$=s=>[...document.querySelectorAll(s)];
const config=window.BOOK_APP_CONFIG||{};

async function load(){
  const [catalog,profiles]=await Promise.all([fetchJSON("data/catalog.json"),fetchJSON("config/analysis-profiles.json")]);
  state.catalog=catalog.books||[]; Object.entries(profiles.profiles||{}).forEach(([k,v])=>state.profiles.set(k,v));
  bind(); route(); renderSearch(); renderFilters(); renderBrowse(); loadJobStatus();
}
async function fetchJSON(url){const r=await fetch(url,{cache:"no-store"});if(!r.ok)throw new Error(`${url}: ${r.status}`);return r.json()}
function bind(){
  window.addEventListener("hashchange",route); $("#search-input").addEventListener("input",renderSearch);
  document.addEventListener("keydown",e=>{if(e.key==="/"&&!/input|textarea/i.test(document.activeElement.tagName)){e.preventDefault();location.hash="search";$("#search-input").focus()}});
  $("#detail-back").onclick=()=>{location.hash=state.lastListHash};
  $("#pick-folder").onclick=openPicker; $("#preview-index").onclick=()=>relay("preview"); $("#start-index").onclick=()=>relay("dispatch");
}
function route(){
  const [page,id]=location.hash.slice(1).split("/"); const key=page||"search";
  $$(".view").forEach(v=>v.hidden=true); $$("[data-nav]").forEach(a=>a.classList.toggle("active",a.dataset.nav===key));
  if(key==="book"&&id){$("#detail-view").hidden=false;showBook(id);return}
  const target=$("#"+(key==="browse"?"browse":key==="indexing"?"indexing":"search")+"-view");target.hidden=false;state.lastListHash=key;
}
function matches(book,q){
  if(!q)return true; const hay=[book.title,book.author,book.genre,book.documentType,...(book.tags||[])].join(" ").toLocaleLowerCase("ko");
  const needle=q.toLocaleLowerCase("ko").replace(/\s+/g,""); return hay.includes(q.toLocaleLowerCase("ko"))||hay.replace(/\s+/g,"").includes(needle)||subsequence(needle,hay.replace(/\s+/g,""));
}
function subsequence(a,b){if(a.length<2)return false;let i=0;for(const c of b)if(c===a[i])i++;return i===a.length}
function renderSearch(){const q=$("#search-input").value.trim();const books=state.catalog.filter(b=>matches(b,q));$("#search-meta").textContent=q?`${books.length}권의 연결된 기록`:`현재 ${books.length}권이 색인되어 있습니다`;renderCards($("#results"),books)}
function renderCards(root,books){root.replaceChildren();if(!books.length){const p=document.createElement("p");p.className="empty";p.textContent="조건에 맞는 책이 없습니다.";root.append(p);return}books.forEach(book=>{const card=$("#book-card-template").content.firstElementChild.cloneNode(true);card.querySelector(".format").textContent=book.format||"book";card.querySelector(".book-type").textContent=profileLabel(book.documentType);card.querySelector("strong").textContent=book.title||"제목 미상";card.querySelector(".author").textContent=book.author||"저자 미상";card.querySelector(".tags").textContent=(book.tags||[]).slice(0,3).map(x=>`#${x}`).join(" ");card.querySelector(".status").textContent=book.metadataStatus==="NEEDS_METADATA_REVIEW"?"메타데이터 확인 필요":"분석 완료";card.onclick=()=>location.hash=`book/${encodeURIComponent(book.bookId)}`;root.append(card)})}
function profileLabel(key){return state.profiles.get(key)?.label||key||"일반 문서"}
function renderFilters(){const root=$("#profile-filters");const values=["all",...new Set(state.catalog.map(b=>b.documentType))];values.forEach((value,i)=>{const b=document.createElement("button");b.textContent=value==="all"?"전체":profileLabel(value);b.classList.toggle("active",i===0);b.onclick=()=>{$$("#profile-filters button").forEach(x=>x.classList.remove("active"));b.classList.add("active");renderBrowse(value)};root.append(b)})}
function renderBrowse(type="all"){const books=type==="all"?state.catalog:state.catalog.filter(b=>b.documentType===type);$("#browse-count").textContent=`${books.length}권`;renderCards($("#browse-results"),books)}
async function showBook(id){
  const base=`data/books/${encodeURIComponent(id)}`;try{const [m,s,a,t,r,c]=await Promise.all(["manifest.json","summary.json","analysis.json","timeline.json","relationships.json","chunks.json"].map(x=>fetchJSON(`${base}/${x}`)));renderDetail(m,{summary:s,analysis:a,timeline:t,relationships:r,chunks:c})}catch(e){$("#detail-content").textContent=`책 정보를 읽지 못했습니다: ${e.message}`}
}
function renderDetail(m,data){const h=$("#detail-header");h.replaceChildren();const left=document.createElement("div");appendText(left,"p",profileLabel(m.documentType),"eyebrow");appendText(left,"h1",m.title||"제목 미상");appendText(left,"p",`${m.author||"저자 미상"} · ${m.genre||m.documentType}`);h.append(left);if(m.source?.webViewLink){const a=document.createElement("a");a.href=m.source.webViewLink;a.target="_blank";a.rel="noopener noreferrer";a.textContent="Google Drive에서 원본 열기 ↗";h.append(a)}
  const tabs=$("#detail-tabs");tabs.replaceChildren();const defs=m.tabs||[{key:"summary",label:"전체 요약"}];defs.forEach((tab,i)=>{const b=document.createElement("button");b.role="tab";b.textContent=tab.label;b.classList.toggle("active",i===0);b.onclick=()=>{$$("#detail-tabs button").forEach(x=>x.classList.remove("active"));b.classList.add("active");showTab(tab.key,data)};tabs.append(b)});showTab(defs[0].key,data)
}
function showTab(key,data){const root=$("#detail-content");root.replaceChildren();let value;if(key==="summary")value=data.summary;else if(key==="sections")value=data.analysis.sections||data.chunks.chunks;else if(key==="timeline")value=data.timeline.timeline;else if(key==="relationships")value=data.relationships.relationships;else value=data.analysis.analysis?.[key]||[];appendValue(root,value,key)}
function appendValue(root,value,label){if(value==null||value===""){appendText(root,"p","이 항목에는 확인된 분석이 없습니다.","empty");return}if(typeof value==="string"){appendText(root,"p",value);return}if(Array.isArray(value)){const list=document.createElement("div");list.className="data-list";value.forEach(v=>{const item=document.createElement("div");item.className="data-item";appendValue(item,v,label);list.append(item)});root.append(list);return}if(typeof value==="object"){Object.entries(value).forEach(([k,v])=>{if(k==="sourceChunkIds"){appendText(root,"small",`근거 구간: ${[].concat(v).join(", ")}`);return}const heading=document.createElement("h3");heading.textContent=humanize(k);root.append(heading);appendValue(root,v,k)});return}appendText(root,"span",String(value))}
function humanize(k){return ({summaryShort:"짧은 요약",summaryLong:"상세 요약",summary:"요약",description:"설명",uncertainties:"불확실성"}[k]||k.replace(/([A-Z])/g," $1"))}
function appendText(root,tag,text,className){const el=document.createElement(tag);if(className)el.className=className;el.textContent=text;root.append(el);return el}
async function loadJobStatus(){try{const s=await fetchJSON("data/job-status.json");const root=$("#job-status");root.replaceChildren();const grid=document.createElement("div");grid.className="status-table";[["상태",s.status],["대상",s.totalFiles??"-"],["완료",s.complete??"-"],["건너뜀",s.skipped??"-"]].forEach(([k,v])=>{const d=document.createElement("div");appendText(d,"small",String(k));appendText(d,"strong",String(v));grid.append(d)});root.append(grid);if(s.message)appendText(root,"p",s.message)}catch(e){$("#job-status").textContent="작업 상태를 불러오지 못했습니다."}}
function openPicker(){
  if(!validConfig())return message("config/public-config.js의 Google 설정을 먼저 완료하세요.");
  if(!window.google?.accounts?.oauth2||!window.gapi)return message("Google 선택기 스크립트를 불러오는 중입니다. 잠시 뒤 다시 눌러 주세요.");
  const tokenClient=google.accounts.oauth2.initTokenClient({client_id:config.googleClientId,scope:"https://www.googleapis.com/auth/drive.readonly",callback:r=>{if(r.error)return message(`Google 인증 실패: ${r.error}`);gapi.load("picker",()=>buildPicker(r.access_token))}});tokenClient.requestAccessToken({prompt:"consent"});
}
function buildPicker(token){const view=new google.picker.DocsView(google.picker.ViewId.FOLDERS).setIncludeFolders(true).setSelectFolderEnabled(true);if(config.driveRootFolderId&&!config.driveRootFolderId.startsWith("REPLACE_"))view.setParent(config.driveRootFolderId);new google.picker.PickerBuilder().setOAuthToken(token).setDeveloperKey(config.googlePickerApiKey).addView(view).setCallback(data=>{if(data.action===google.picker.Action.PICKED){const d=data.docs[0];state.selectedFolder={id:d.id,name:d.name};$("#folder-info dd").textContent=`${d.name} (${d.id})`;$("#preview-index").disabled=false;$("#start-index").disabled=false;message("폴더가 선택되었습니다. 먼저 대상 미리 보기를 권장합니다.")}}).build().setVisible(true)}
function validConfig(){return [config.googleClientId,config.googlePickerApiKey,config.appsScriptWebAppUrl].every(v=>v&&!v.startsWith("REPLACE_"))}
async function relay(route){if(!state.selectedFolder)return;message(route==="preview"?"대상을 확인하는 중…":"GitHub Actions를 요청하는 중…");try{const response=await fetch(config.appsScriptWebAppUrl,{method:"POST",headers:{"Content-Type":"text/plain;charset=utf-8"},body:JSON.stringify({route,folderId:state.selectedFolder.id,recursive:$("#recursive").checked,force:$("#force-reindex").checked})});const data=await response.json();if(!data.ok)throw new Error(data.error||"요청 실패");message(route==="preview"?`대상 TXT/EPUB ${data.targetFiles}개 · 하위 폴더 ${data.folders}개`:`작업 요청 완료 · GitHub 실행 ID ${data.runId||"확인 중"}`)}catch(e){message(`요청 실패: ${e.message}`)}}
function message(text){$("#index-message").textContent=text}
load().catch(e=>{console.error(e);$("#results").textContent=`초기 데이터를 읽지 못했습니다: ${e.message}`});
