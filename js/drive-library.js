const FOLDER_MIME = "application/vnd.google-apps.folder";
const TEXT_MIME = "text/plain";
const EPUB_MIME = "application/epub+zip";
const DB_NAME = "book-drive-library-v1";
const STORE_NAME = "preferences";

export async function listDriveBooks(folderId, accessToken, recursive = true) {
  if (!folderId || !accessToken) throw new Error("Google Drive 폴더와 로그인이 필요합니다.");
  const root = await getMetadata(folderId, accessToken);
  const queue = [{id: folderId, path: [root.name || folderId]}];
  const books = [];
  while (queue.length) {
    const current = queue.shift();
    let pageToken = "";
    do {
      const params = new URLSearchParams({
        q: `'${current.id.replace(/'/g, "\\'")}' in parents and trashed = false`,
        fields: "nextPageToken,files(id,name,mimeType,modifiedTime,md5Checksum,size,webViewLink,parents)",
        pageSize: "1000",
        supportsAllDrives: "true",
        includeItemsFromAllDrives: "true",
      });
      if (pageToken) params.set("pageToken", pageToken);
      const response = await driveFetch(`https://www.googleapis.com/drive/v3/files?${params}`, accessToken);
      for (const item of response.files || []) {
        if (item.mimeType === FOLDER_MIME) {
          if (recursive) queue.push({id: item.id, path: [...current.path, item.name]});
        } else if (isSupportedBook(item)) {
          books.push({...item, folderPath: current.path, format: item.mimeType === EPUB_MIME || item.name.toLocaleLowerCase().endsWith(".epub") ? "epub" : "txt"});
        }
      }
      pageToken = response.nextPageToken || "";
    } while (pageToken);
  }
  return books.sort((a, b) => a.name.localeCompare(b.name, "ko", {numeric: true, sensitivity: "base"}));
}

export function folderNameTag(folderPath) {
  const name = String(folderPath?.at(-1) || "");
  return name.replace(/\d+/g, " ").replace(/[\[\](){}<>#]+/g, " ").replace(/[_\-–—]+/g, " ").replace(/\s+/g, " ").replace(/^[\s.,·:;|/\\]+|[\s.,·:;|/\\]+$/g, "").slice(0, 60);
}

export async function loadDriveFolderPreference() {
  try { return (await dbRequest("readonly", store => store.get("selected-folder"))) || null; }
  catch (_) { return null; }
}

export async function saveDriveFolderPreference(folder) {
  try { await dbRequest("readwrite", store => store.put(folder, "selected-folder")); }
  catch (_) { /* The live folder still works when browser storage is unavailable. */ }
}

function isSupportedBook(item) {
  const name = String(item.name || "").toLocaleLowerCase();
  return item.mimeType === TEXT_MIME || item.mimeType === EPUB_MIME || name.endsWith(".txt") || name.endsWith(".epub");
}

async function getMetadata(fileId, accessToken) {
  return driveFetch(`https://www.googleapis.com/drive/v3/files/${encodeURIComponent(fileId)}?fields=id%2Cname%2CmimeType&supportsAllDrives=true`, accessToken);
}

async function driveFetch(url, accessToken) {
  const response = await fetch(url, {headers: {Authorization: `Bearer ${accessToken}`}, cache: "no-store"});
  if (!response.ok) throw new Error(`Google Drive 목록을 읽지 못했습니다: HTTP ${response.status}`);
  return response.json();
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE_NAME);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function dbRequest(mode, operation) {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode);
    const request = operation(transaction.objectStore(STORE_NAME));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => database.close();
    transaction.onerror = () => reject(transaction.error);
  });
}
