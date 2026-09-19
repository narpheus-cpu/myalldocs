import "./vendor/jszip.min.js";

const STORAGE_KEY="bookmap_jsonl_prompt_options_v1";
const SELECTED_KEY="bookmap_jsonl_prompt_selected_v1";
const DEFAULT_PROMPTS=[
  {title:"출력 형식 없음",content:""},
  {title:"마크다운 표 기본 형식",content:"텍스트 설명 없이 구글 시트에 바로 복사·붙여넣기 할 수 있는 마크다운 표 형식으로만 작성해줘"}
];

const byId=id=>document.getElementById(id);
const extension=name=>String(name||"").split(".").pop().toLocaleLowerCase("en");
const supported=file=>["txt","epub"].includes(extension(file?.name));
const cleanText=text=>String(text||"").replace(/\u0000/g,"").replace(/\r\n?/g,"\n");
const rawName=name=>String(name||"").replace(/\.[^.]+$/,"");

export function makeExcerpts(text,amount){
  const value=String(text||""),length=value.length;
  if(!length)return{start:"",middle:"",late:""};
  const size=Math.min(Math.max(0,Number(amount)||0),length),middleStart=Math.max(0,Math.floor(length/2-size/2));
  return{start:value.slice(0,size),middle:value.slice(middleStart,middleStart+size),late:value.slice(Math.max(0,length-size))};
}

function humanSize(bytes){
  if(bytes<1024)return`${bytes} B`;
  const units=["KB","MB","GB"],index=Math.min(units.length-1,Math.floor(Math.log(bytes)/Math.log(1024))-1);
  return`${(bytes/1024**(index+1)).toFixed(2)} ${units[index]}`;
}

function loadPrompts(){
  try{
    const value=JSON.parse(localStorage.getItem(STORAGE_KEY)||"null");
    if(Array.isArray(value)&&value.length&&value.every(item=>typeof item?.title==="string"&&typeof item?.content==="string"))return value;
  }catch(error){console.warn("JSONL 출력 형식 읽기 실패",error)}
  return DEFAULT_PROMPTS.map(item=>({...item}));
}

function savePrompts(items,selected){
  try{localStorage.setItem(STORAGE_KEY,JSON.stringify(items));localStorage.setItem(SELECTED_KEY,String(selected));return true}
  catch(error){console.warn("JSONL 출력 형식 저장 실패",error);return false}
}

function selectedPromptIndex(items){
  try{const value=Number.parseInt(localStorage.getItem(SELECTED_KEY),10);return Number.isInteger(value)&&value>=0&&value<items.length?value:0}catch{return 0}
}

function copyText(text){
  if(navigator.clipboard?.writeText)return navigator.clipboard.writeText(text).then(()=>true).catch(()=>legacyCopy(text));
  return Promise.resolve(legacyCopy(text));
}

function legacyCopy(text){
  const area=document.createElement("textarea");area.value=text;area.style.cssText="position:fixed;left:-9999px;top:0";document.body.append(area);area.select();
  let copied=false;try{copied=document.execCommand("copy")}catch{}area.remove();return copied;
}

function decodeTxt(buffer){
  try{return{text:cleanText(new TextDecoder("utf-8",{fatal:true}).decode(buffer)),method:"UTF-8"}}
  catch{return{text:cleanText(new TextDecoder("euc-kr").decode(buffer)),method:"CP949 / EUC-KR"}}
}

function localElements(root,name){
  let result=[];
  try{result=[...root.getElementsByTagNameNS("*",name)]}catch{}
  if(!result.length)try{result=[...root.getElementsByTagName(name)]}catch{}
  if(!result.length)try{result=[...root.getElementsByTagName("*")].filter(node=>(node.localName||node.nodeName.split(":").pop())===name)}catch{}
  return result;
}

function parseAttributes(tag){
  const attributes={},pattern=/([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"|([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*'([^']*)'/g;
  let match;while((match=pattern.exec(tag))!==null)attributes[(match[1]||match[3]).toLocaleLowerCase("en")]=match[2]??match[4];return attributes;
}

function regexRootfile(xml){
  for(const tag of xml.match(/<[a-zA-Z0-9_:]*rootfile\b[^>]*>/gi)||[]){const attributes=parseAttributes(tag);if(attributes["full-path"])return attributes["full-path"]}return"";
}

function regexManifest(xml){
  const result={};for(const tag of xml.match(/<[a-zA-Z0-9_:]*item\b[^>]*\/?>/gi)||[]){if(/itemref\b/i.test(tag))continue;const a=parseAttributes(tag);if(a.id&&a.href)result[a.id]={href:a.href,mediaType:a["media-type"]||""}}return result;
}

function regexSpine(xml){
  return(xml.match(/<[a-zA-Z0-9_:]*itemref\b[^>]*\/?>/gi)||[]).map(tag=>parseAttributes(tag).idref).filter(Boolean);
}

function joinEpubPath(base,relative){
  let decoded=String(relative||"").split("#")[0];try{decoded=decodeURIComponent(decoded)}catch{}
  const parts=base?base.split("/").filter(Boolean):[];
  for(const part of decoded.replace(/\\/g,"/").split("/")){if(!part||part===".")continue;if(part==="..")parts.pop();else parts.push(part)}return parts.join("/");
}

function zipFile(zip,path){
  const clean=String(path||"").replace(/^\/+/,"");return zip.file(clean)||zip.file(Object.keys(zip.files).find(key=>key.replace(/^\/+/,"").toLocaleLowerCase("en")===clean.toLocaleLowerCase("en"))||"");
}

function htmlText(html){
  const doc=new DOMParser().parseFromString(String(html).replace(/<br\s*\/?>/gi,"\n"),"text/html"),root=doc.body||doc.documentElement;
  const blocks=[...root.querySelectorAll("p,h1,h2,h3,h4,h5,h6,li,blockquote")],paragraphs=blocks.length?blocks.map(node=>node.textContent.replace(/[ \t]+/g," ").trim()).filter(Boolean):[root.textContent.replace(/[ \t]+/g," ").trim()].filter(Boolean);
  return paragraphs.join("\n\n");
}

async function parseEpub(file){
  if(!window.JSZip)throw new Error("EPUB 처리 도구를 불러오지 못했습니다. 인터넷 연결을 확인하세요.");
  const zip=await window.JSZip.loadAsync(await file.arrayBuffer()),container=zipFile(zip,"META-INF/container.xml");
  if(!container)throw new Error("유효한 EPUB이 아닙니다(container.xml 없음).");
  const containerXml=await container.async("text"),containerDoc=new DOMParser().parseFromString(containerXml,"application/xml");
  const opfPath=localElements(containerDoc,"rootfile")[0]?.getAttribute("full-path")||regexRootfile(containerXml);
  if(!opfPath)throw new Error("EPUB의 본문 위치를 찾지 못했습니다.");
  const opf=zipFile(zip,opfPath);if(!opf)throw new Error("EPUB OPF 파일을 찾지 못했습니다.");
  const opfXml=await opf.async("text"),opfDoc=new DOMParser().parseFromString(opfXml,"application/xml"),manifest={};
  localElements(opfDoc,"item").forEach(item=>{const id=item.getAttribute("id"),href=item.getAttribute("href");if(id&&href)manifest[id]={href,mediaType:item.getAttribute("media-type")||""}});
  const finalManifest=Object.keys(manifest).length?manifest:regexManifest(opfXml),spine=localElements(opfDoc,"itemref").map(item=>item.getAttribute("idref")).filter(Boolean),order=spine.length?spine:regexSpine(opfXml),readingOrder=order.length?order:Object.keys(finalManifest),opfDir=opfPath.includes("/")?opfPath.slice(0,opfPath.lastIndexOf("/")):"",parts=[];
  for(const id of readingOrder){
    const item=finalManifest[id];if(!item)continue;
    if(item.mediaType&&(!/(?:html|xml)/i.test(item.mediaType)||/ncx/i.test(item.mediaType)))continue;
    const entry=zipFile(zip,joinEpubPath(opfDir,item.href));if(!entry)continue;
    const text=htmlText(await entry.async("text")).trim();if(text)parts.push(text);
  }
  if(!parts.length)throw new Error("EPUB에서 본문을 추출하지 못했습니다.");
  return{text:cleanText(parts.join("\n\n")),method:"EPUB / OPF 읽기 순서"};
}

async function readBook(file){
  if(extension(file.name)==="txt")return decodeTxt(await file.arrayBuffer());
  if(extension(file.name)==="epub")return parseEpub(file);
  throw new Error("지원하지 않는 파일 형식입니다.");
}

function relativePath(file,mode){
  if(mode!=="folder"||!file.webkitRelativePath)return"";
  const parts=file.webkitRelativePath.split("/");return parts.length>2?parts.slice(1,-1).join("/"):"";
}

export function makeBookObject(file,text,amount,{extended=true,mode="files"}={}){
  const excerpts=makeExcerpts(text,amount),object={filename:file.name,relative_path:relativePath(file,mode),excerpt_start:excerpts.start,excerpt_middle:excerpts.middle,excerpt_late:excerpts.late};
  if(extended){const name=rawName(file.name);object.filename_raw=name;object.filename_parts=name.split("-").map(value=>value.trim()).filter(Boolean);object.text_length=text.length}return object;
}

function promptDialog(){
  let dialog=byId("jsonl-prompt-dialog");if(dialog)return dialog;
  dialog=document.createElement("dialog");dialog.id="jsonl-prompt-dialog";dialog.className="editor-dialog generator-prompt-dialog";
  dialog.innerHTML='<div class="dialog-header"><div><p class="eyebrow">JSONL 작업용 문구</p><h2 id="jsonl-prompt-dialog-title">출력 형식 편집</h2></div><button class="icon-button" type="button" data-close aria-label="닫기">×</button></div><div class="generator-prompt-form"><label>출력 형식 이름<input id="jsonl-prompt-title" type="text" maxlength="80"></label><label>복사할 프롬프트<textarea id="jsonl-prompt-content" rows="7" maxlength="8000"></textarea></label></div><div class="dialog-actions"><button class="primary" type="button" data-save>저장</button><button type="button" data-close>취소</button></div>';
  document.body.append(dialog);dialog.querySelectorAll("[data-close]").forEach(button=>button.onclick=()=>dialog.close("cancel"));return dialog;
}

export function initJsonGenerator(){
  const root=byId("json-generator-view");if(!root||root.dataset.ready)return;root.dataset.ready="1";
  document.querySelectorAll('a[href="#json-generator"]').forEach(link=>link.addEventListener("click",()=>{const manual=byId("upload-manual-dialog");if(manual?.open)manual.close()}));
  const el={file:byId("jsonl-file-input"),folder:byId("jsonl-folder-input"),reset:byId("jsonl-reset"),mode:byId("jsonl-selection-mode"),count:byId("jsonl-file-count"),txt:byId("jsonl-txt-count"),epub:byId("jsonl-epub-count"),root:byId("jsonl-root-name"),size:byId("jsonl-total-size"),amount:byId("jsonl-excerpt-length"),extended:byId("jsonl-extended-fields"),build:byId("jsonl-build"),cancel:byId("jsonl-cancel"),download:byId("jsonl-download"),progress:byId("jsonl-progress"),status:byId("jsonl-status"),preview:byId("jsonl-preview"),table:byId("jsonl-result-table"),select:byId("jsonl-prompt-select"),list:byId("jsonl-prompt-list"),copy:byId("jsonl-copy-prompt"),edit:byId("jsonl-edit-prompt"),add:byId("jsonl-add-prompt"),remove:byId("jsonl-delete-prompt")};
  let files=[],mode="",lines=[],cancelled=false,prompts=loadPrompts(),selected=selectedPromptIndex(prompts),dragged=-1;
  const say=text=>{el.status.textContent=text};

  function renderPrompts(){
    el.list.replaceChildren();prompts.forEach((prompt,index)=>{const row=document.createElement("button");row.type="button";row.className="generator-prompt-option";row.draggable=true;row.dataset.index=String(index);row.title=prompt.content||"프롬프트 없음";row.innerHTML='<span></span><b aria-hidden="true">≡</b>';row.querySelector("span").textContent=prompt.title;row.classList.toggle("selected",index===selected);
      row.onclick=()=>{selected=index;savePrompts(prompts,selected);renderPrompts();closePromptList()};
      row.ondragstart=()=>{dragged=index;row.classList.add("dragging")};row.ondragend=()=>{dragged=-1;row.classList.remove("dragging")};row.ondragover=event=>event.preventDefault();row.ondrop=event=>{event.preventDefault();if(dragged<0||dragged===index)return;const current=prompts[selected],moved=prompts.splice(dragged,1)[0];prompts.splice(index,0,moved);selected=Math.max(0,prompts.indexOf(current));savePrompts(prompts,selected);renderPrompts()};el.list.append(row)});
    el.select.textContent=prompts[selected]?.title||"선택된 출력 형식 없음";
  }
  function closePromptList(){el.list.hidden=true;el.select.setAttribute("aria-expanded","false")}
  el.select.onclick=event=>{event.stopPropagation();el.list.hidden=!el.list.hidden;el.select.setAttribute("aria-expanded",String(!el.list.hidden))};
  document.addEventListener("click",event=>{if(!root.contains(event.target)||!event.target.closest(".generator-prompt-row"))closePromptList()});

  async function editPrompt(isNew){
    const dialog=promptDialog(),title=byId("jsonl-prompt-title"),content=byId("jsonl-prompt-content"),heading=byId("jsonl-prompt-dialog-title"),current=prompts[selected]||{title:"",content:""};
    heading.textContent=isNew?"새 출력 형식 추가":"출력 형식 수정";title.value=isNew?"":current.title;content.value=isNew?"":current.content;
    dialog.querySelector("[data-save]").onclick=()=>{const name=title.value.trim();if(!name){title.focus();return}const item={title:name,content:content.value.trim()};if(isNew){prompts.push(item);selected=prompts.length-1}else prompts[selected]=item;if(!savePrompts(prompts,selected))say("브라우저에 출력 형식을 저장하지 못했습니다.");renderPrompts();dialog.close("save")};
    dialog.showModal();title.focus();
  }
  el.add.onclick=()=>editPrompt(true);el.edit.onclick=()=>editPrompt(false);el.remove.onclick=()=>{if(prompts.length<=1){say("출력 형식은 최소 1개가 필요합니다.");return}if(!confirm(`‘${prompts[selected].title}’ 출력 형식을 삭제할까요?`))return;prompts.splice(selected,1);selected=Math.min(selected,prompts.length-1);savePrompts(prompts,selected);renderPrompts();say("출력 형식을 삭제했습니다.")};
  el.copy.onclick=async()=>{const text=prompts[selected]?.content?.trim();if(!text){say("선택한 출력 형식에는 복사할 프롬프트가 없습니다.");return}say(await copyText(text)?"프롬프트를 복사했습니다.":"프롬프트 복사에 실패했습니다.")};

  function resetResults(){lines=[];el.preview.value="";el.table.replaceChildren();el.progress.value=0;el.progress.max=Math.max(1,files.length);el.download.disabled=true}
  function choose(inputFiles,nextMode){
    files=[...(inputFiles||[])].filter(supported).sort((a,b)=>(a.webkitRelativePath||a.name).localeCompare(b.webkitRelativePath||b.name,"ko"));mode=nextMode;if(mode==="files")el.folder.value="";else el.file.value="";
    const txt=files.filter(file=>extension(file.name)==="txt").length,epub=files.length-txt,bytes=files.reduce((sum,file)=>sum+file.size,0),path=files[0]?.webkitRelativePath||"";
    el.mode.textContent=mode==="folder"?"폴더 선택":"파일 선택";el.count.textContent=`${files.length.toLocaleString()}개`;el.txt.textContent=`${txt.toLocaleString()}개`;el.epub.textContent=`${epub.toLocaleString()}개`;el.root.textContent=mode==="folder"?(path.includes("/")?path.split("/")[0]:"선택 폴더"):"개별 파일 선택";el.size.textContent=humanSize(bytes);el.build.disabled=!files.length;resetResults();say(files.length?`${files.length.toLocaleString()}개의 TXT·EPUB를 찾았습니다.`:"지원되는 TXT·EPUB를 찾지 못했습니다.")
  }
  el.file.onchange=()=>choose(el.file.files,"files");el.folder.onchange=()=>choose(el.folder.files,"folder");
  el.reset.onclick=()=>{el.file.value="";el.folder.value="";files=[];mode="";el.mode.textContent="-";el.count.textContent="0개";el.txt.textContent="0개";el.epub.textContent="0개";el.root.textContent="-";el.size.textContent="-";el.build.disabled=true;el.cancel.disabled=true;cancelled=false;resetResults();say("파일 또는 폴더를 선택하세요.")};

  function addRow(file,length,method,result,error=false){
    const row=document.createElement("tr");[file.webkitRelativePath||file.name,extension(file.name).toLocaleUpperCase("en"),length??"-",method||"-",result].forEach((value,index)=>{const cell=document.createElement("td");cell.textContent=String(value);if(index===4)cell.className=error?"generator-error":"generator-success";row.append(cell)});el.table.prepend(row);while(el.table.children.length>200)el.table.lastElementChild.remove();
  }
  el.build.onclick=async()=>{
    if(!files.length)return;const amount=Number(el.amount.value);if(!Number.isFinite(amount)||amount<200||amount>10000){say("발췌 길이는 200자 이상 10,000자 이하로 입력하세요.");el.amount.focus();return}
    cancelled=false;resetResults();el.table.replaceChildren();el.build.disabled=true;el.cancel.disabled=false;let success=0,failed=0;
    for(let index=0;index<files.length&&!cancelled;index++){
      const file=files[index];say(`처리 중 ${index+1} / ${files.length} · [${extension(file.name).toLocaleUpperCase("en")}] ${file.name}`);
      try{const parsed=await readBook(file),text=cleanText(parsed.text);if(!text.trim())throw new Error("추출된 본문이 비어 있습니다.");const object=makeBookObject(file,text,amount,{extended:el.extended.checked,mode});lines.push(JSON.stringify(object));success++;addRow(file,text.length.toLocaleString(),parsed.method,"완료")}
      catch(error){failed++;addRow(file,null,"-",error?.message||"읽기 실패",true)}
      el.progress.value=index+1;if((index+1)%5===0)await new Promise(resolve=>setTimeout(resolve,0));
    }
    el.cancel.disabled=true;el.build.disabled=false;el.preview.value=lines.slice(0,5).join("\n")+(lines.length>5?`\n\n… 총 ${lines.length.toLocaleString()}행`:"");el.download.disabled=!lines.length;say(`${cancelled?"중지됨":"완료"} · 정상 ${success.toLocaleString()}개 · 오류 ${failed.toLocaleString()}개`);
  };
  el.cancel.onclick=()=>{cancelled=true;el.cancel.disabled=true;say("현재 파일을 마친 뒤 중지합니다.")};
  el.download.onclick=()=>{if(!lines.length)return;const blob=new Blob([`${lines.join("\n")}\n`],{type:"application/x-ndjson;charset=utf-8"}),now=new Date(),pad=value=>String(value).padStart(2,"0"),name=`서재지도_도서목록_${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.jsonl`,url=URL.createObjectURL(blob),anchor=document.createElement("a");anchor.href=url;anchor.download=name;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);say(`${name} 다운로드를 시작했습니다.`)};
  renderPrompts();
}
