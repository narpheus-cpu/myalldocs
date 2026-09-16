const DB_NAME="bookmap-browser-state";
const STORE_NAME="values";
const DB_VERSION=1;
const writeQueues=new Map();

function openDatabase(){
  return new Promise((resolve,reject)=>{
    if(!globalThis.indexedDB){reject(new Error("이 브라우저는 IndexedDB를 지원하지 않습니다."));return}
    const request=indexedDB.open(DB_NAME,DB_VERSION);
    request.onupgradeneeded=()=>{const db=request.result;if(!db.objectStoreNames.contains(STORE_NAME))db.createObjectStore(STORE_NAME,{keyPath:"key"})};
    request.onsuccess=()=>resolve(request.result);
    request.onerror=()=>reject(request.error||new Error("브라우저 저장소를 열지 못했습니다."));
    request.onblocked=()=>reject(new Error("브라우저 저장소가 다른 탭에서 잠겨 있습니다."));
  })
}

async function transaction(mode,operation){
  const db=await openDatabase();
  try{
    return await new Promise((resolve,reject)=>{
      const tx=db.transaction(STORE_NAME,mode),store=tx.objectStore(STORE_NAME);
      let result;
      try{result=operation(store)}catch(error){reject(error);return}
      tx.oncomplete=()=>resolve(result?.result);
      tx.onerror=()=>reject(tx.error||result?.error||new Error("브라우저 저장소 작업에 실패했습니다."));
      tx.onabort=()=>reject(tx.error||new Error("브라우저 저장소 작업이 취소되었습니다."));
    })
  }finally{db.close()}
}

export async function loadBrowserValues(){
  const rows=await transaction("readonly",store=>store.getAll());
  return new Map((rows||[]).map(row=>[String(row.key),row.value]))
}

function enqueue(key,operation){
  const normalized=String(key),previous=writeQueues.get(normalized)||Promise.resolve(),next=previous.catch(()=>{}).then(operation);
  writeQueues.set(normalized,next);
  const cleanup=()=>{if(writeQueues.get(normalized)===next)writeQueues.delete(normalized)};
  next.then(cleanup,cleanup);
  return next
}

export function writeBrowserValue(key,value){
  return enqueue(key,()=>transaction("readwrite",store=>store.put({key:String(key),value,updatedAt:new Date().toISOString()})))
}

export function removeBrowserValue(key){
  return enqueue(key,()=>transaction("readwrite",store=>store.delete(String(key))))
}
