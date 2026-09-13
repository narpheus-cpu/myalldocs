const DB_NAME = "library-map-reader";
const STORE_NAME = "settings";
const FORMAT_KEY = "copy-prompt-formats";

const DEFAULT_FORMATS = [
  {id: "plain-original", name: "원문만 복사", instruction: ""},
];

export async function loadPromptFormats() {
  try {
    const saved = await readSetting(FORMAT_KEY);
    return normalizeFormats(saved);
  } catch (error) {
    console.warn("출력 형식 저장소를 열지 못해 기본값을 사용합니다.", error);
    return structuredClone(DEFAULT_FORMATS);
  }
}

export async function savePromptFormats(formats) {
  const normalized = normalizeFormats(formats);
  await writeSetting(FORMAT_KEY, normalized);
  return normalized;
}

export function composeCopyText(instruction, sourceText) {
  const prompt = String(instruction || "").trim();
  const source = String(sourceText || "");
  if (!prompt) return source;
  const separator = /[:：]\s*$/.test(prompt) ? " " : ": ";
  return prompt + separator + source;
}

export function createFormatId() {
  return globalThis.crypto?.randomUUID?.() || `format-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function normalizeFormats(value) {
  if (!Array.isArray(value) || !value.length) return structuredClone(DEFAULT_FORMATS);
  const result = value.map(item => ({
    id: String(item?.id || createFormatId()).slice(0, 100),
    name: String(item?.name || "이름 없는 출력 형식").trim().slice(0, 80),
    instruction: String(item?.instruction || "").trim().slice(0, 4000),
  })).filter(item => item.name);
  return result.length ? result : structuredClone(DEFAULT_FORMATS);
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) request.result.createObjectStore(STORE_NAME);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("브라우저 저장소를 열지 못했습니다."));
  });
}

async function readSetting(key) {
  const db = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

async function writeSetting(key, value) {
  const db = await openDatabase();
  try {
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE_NAME, "readwrite");
      transaction.objectStore(STORE_NAME).put(value, key);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error("출력 형식을 저장하지 못했습니다."));
    });
  } finally {
    db.close();
  }
}
