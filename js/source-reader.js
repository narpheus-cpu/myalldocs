const BLOCK_TAGS = new Set(["p", "div", "h1", "h2", "h3", "li"]);
const HIDDEN_TAGS = new Set(["script", "style", "nav"]);

export async function loadDriveChunks(manifest, accessToken, targetChars = 5000, overlapChars = 250) {
  if (!manifest?.driveFileId) throw new Error("원본 Google Drive 파일 ID가 없습니다.");
  if (!accessToken) throw new Error("Google 계정 연결이 필요합니다.");
  const url = `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(manifest.driveFileId)}?alt=media&supportsAllDrives=true`;
  const response = await fetch(url, {headers: {Authorization: `Bearer ${accessToken}`}});
  if (!response.ok) throw new Error(`Google Drive 원문 다운로드 실패: HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  const text = manifest.format === "epub" ? await extractEpubText(bytes) : decodeTxt(bytes);
  return chunkSourceText(text, targetChars, overlapChars);
}

export function chunkSourceText(text, targetChars = 5000, overlapChars = 250) {
  if (targetChars < 200) throw new Error("청크 크기는 200자 이상이어야 합니다.");
  const clean = String(text).replace(/\r\n?/g, "\n").trim();
  if (!clean) return [];
  const paragraphs = clean.split(/\n\s*\n/u).map(value => value.trim()).filter(Boolean);
  const chunks = [];
  let current = "";
  for (const paragraph of paragraphs) {
    for (const piece of splitLong(paragraph, targetChars)) {
      const candidate = current ? `${current}\n\n${piece}`.trim() : piece;
      if (current && codePoints(candidate).length > targetChars) {
        chunks.push(current);
        const chars = codePoints(current);
        const overlap = overlapChars ? chars.slice(-overlapChars).join("").replace(/^\s+/u, "") : "";
        current = `${overlap}\n\n${piece}`.trim();
      } else {
        current = candidate;
      }
    }
  }
  if (current) chunks.push(current);
  return chunks.map((value, index) => ({
    chunkId: index + 1,
    text: value,
    charCount: codePoints(value).length,
    position: Number((index / Math.max(1, chunks.length - 1)).toFixed(4)),
  }));
}

function splitLong(text, limit) {
  if (codePoints(text).length <= limit) return [text];
  const sentences = text.split(/(?<=[.!?。！？])\s+/u);
  const result = [];
  let buffer = "";
  for (const sentence of sentences) {
    const sentenceLength = codePoints(sentence).length;
    if (sentenceLength > limit) {
      if (buffer) result.push(buffer);
      buffer = "";
      const chars = codePoints(sentence);
      for (let index = 0; index < chars.length; index += limit) result.push(chars.slice(index, index + limit).join(""));
    } else if (!buffer) {
      buffer = sentence;
    } else if (codePoints(buffer).length + 1 + sentenceLength <= limit) {
      buffer += ` ${sentence}`;
    } else {
      result.push(buffer);
      buffer = sentence;
    }
  }
  if (buffer) result.push(buffer);
  return result;
}

function codePoints(value) {
  return Array.from(value);
}

function decodeTxt(bytes) {
  for (const encoding of ["utf-8", "euc-kr"]) {
    try {
      return new TextDecoder(encoding, {fatal: true}).decode(bytes).replace(/^\uFEFF/, "");
    } catch (_) {
      // Try the next local text encoding.
    }
  }
  return new TextDecoder("utf-8").decode(bytes).replace(/^\uFEFF/, "");
}

async function extractEpubText(bytes) {
  const archive = readZipDirectory(bytes);
  const container = parseXml(await archive.text("META-INF/container.xml"), "EPUB container.xml");
  const rootfile = firstByLocalName(container, "rootfile");
  const opfName = rootfile?.getAttribute("full-path");
  if (!opfName) throw new Error("EPUB 내부에서 OPF 파일을 찾지 못했습니다.");
  const opf = parseXml(await archive.text(opfName), "EPUB OPF");
  const manifest = new Map();
  for (const item of allByLocalName(opf, "item")) {
    if (item.getAttribute("id") && item.getAttribute("href")) manifest.set(item.getAttribute("id"), item.getAttribute("href"));
  }
  const base = opfName.includes("/") ? opfName.slice(0, opfName.lastIndexOf("/") + 1) : "";
  const sections = [];
  for (const itemref of allByLocalName(opf, "itemref")) {
    const href = manifest.get(itemref.getAttribute("idref"));
    if (!href) continue;
    const entryName = resolveZipPath(base, href.split("#", 1)[0]);
    const document = new DOMParser().parseFromString(await archive.text(entryName), "text/html");
    const section = extractHtmlText(document.body || document.documentElement);
    if (section) sections.push(section);
  }
  return sections.join("\n\n");
}

function parseXml(text, label) {
  const document = new DOMParser().parseFromString(text, "application/xml");
  if (document.querySelector("parsererror")) throw new Error(`${label} 문법을 읽지 못했습니다.`);
  return document;
}

function allByLocalName(document, name) {
  return Array.from(document.getElementsByTagNameNS("*", name));
}

function firstByLocalName(document, name) {
  return allByLocalName(document, name)[0] || null;
}

function extractHtmlText(root) {
  const parts = [];
  const visit = node => {
    if (node.nodeType === 3) {
      parts.push(node.nodeValue || "");
      return;
    }
    if (node.nodeType !== 1) return;
    const tag = node.localName?.toLowerCase() || "";
    if (HIDDEN_TAGS.has(tag)) return;
    if (tag === "br" || BLOCK_TAGS.has(tag)) parts.push("\n");
    for (const child of node.childNodes) visit(child);
    if (BLOCK_TAGS.has(tag)) parts.push("\n");
  };
  visit(root);
  return parts.join("").replace(/[ \t]+/g, " ").replace(/\n\s*\n\s*\n+/g, "\n\n").trim();
}

function resolveZipPath(base, href) {
  let decoded = href;
  try { decoded = decodeURIComponent(href); } catch (_) { /* Preserve literal filename. */ }
  const parts = `${base}${decoded}`.split("/");
  const normalized = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") normalized.pop();
    else normalized.push(part);
  }
  return normalized.join("/");
}

function readZipDirectory(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  const lowerBound = Math.max(0, bytes.length - 65557);
  for (let offset = bytes.length - 22; offset >= lowerBound; offset--) {
    if (view.getUint32(offset, true) === 0x06054b50) { eocd = offset; break; }
  }
  if (eocd < 0) throw new Error("올바른 EPUB ZIP 구조가 아닙니다.");
  const count = view.getUint16(eocd + 10, true);
  let offset = view.getUint32(eocd + 16, true);
  const entries = new Map();
  for (let index = 0; index < count; index++) {
    if (view.getUint32(offset, true) !== 0x02014b50) throw new Error("EPUB ZIP 목록이 손상되었습니다.");
    const method = view.getUint16(offset + 10, true);
    const compressedSize = view.getUint32(offset + 20, true);
    const nameLength = view.getUint16(offset + 28, true);
    const extraLength = view.getUint16(offset + 30, true);
    const commentLength = view.getUint16(offset + 32, true);
    const localOffset = view.getUint32(offset + 42, true);
    const name = new TextDecoder("utf-8").decode(bytes.slice(offset + 46, offset + 46 + nameLength));
    entries.set(name, {method, compressedSize, localOffset});
    offset += 46 + nameLength + extraLength + commentLength;
  }
  return {
    async text(name) {
      const entry = entries.get(name);
      if (!entry) throw new Error(`EPUB 내부 파일을 찾지 못했습니다: ${name}`);
      if (view.getUint32(entry.localOffset, true) !== 0x04034b50) throw new Error("EPUB ZIP 항목이 손상되었습니다.");
      const nameLength = view.getUint16(entry.localOffset + 26, true);
      const extraLength = view.getUint16(entry.localOffset + 28, true);
      const start = entry.localOffset + 30 + nameLength + extraLength;
      const compressed = bytes.slice(start, start + entry.compressedSize);
      let plain;
      if (entry.method === 0) plain = compressed;
      else if (entry.method === 8) {
        if (typeof DecompressionStream === "undefined") throw new Error("이 브라우저는 EPUB 압축 해제를 지원하지 않습니다.");
        const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        plain = new Uint8Array(await new Response(stream).arrayBuffer());
      } else throw new Error(`지원하지 않는 EPUB 압축 방식입니다: ${entry.method}`);
      return new TextDecoder("utf-8").decode(plain);
    },
  };
}
