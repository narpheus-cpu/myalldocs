const COLUMNS = [
  ["작품명", book => book.title],
  ["저자", book => book.author],
  ["장르", (book, labels) => labels.genre(book.genre)],
  ["태그", book => Array.isArray(book.tags) ? book.tags.join(" · ") : ""],
  ["작품 성격", (book, labels) => labels.profile(book.documentType)],
  ["파일 형식", book => book.format],
  ["분석 상태", book => book.indexStatus],
  ["메타데이터 상태", book => book.metadataStatus],
  ["생성일시", book => book.createdAt || book.indexedAt],
  ["수정일시", book => book.updatedAt],
  ["도서 ID", book => book.bookId],
  ["원본 파일 ID", book => book.driveFileId],
  ["Google Drive 원본", book => book.webViewLink || book.source?.webViewLink],
];

function csvCell(value) {
  let text = String(value ?? "");
  // Spreadsheet programs can evaluate formulas even when a CSV field is quoted.
  if (/^\s*[=+\-@]/.test(text)) text = "'" + text;
  return `"${text.replaceAll('"', '""')}"`;
}

export function catalogToCsv(books, labels = {}) {
  const converters = {
    genre: labels.genre || (value => value || ""),
    profile: labels.profile || (value => value || ""),
  };
  const rows = [COLUMNS.map(([heading]) => heading)];
  for (const book of books) rows.push(COLUMNS.map(([, value]) => value(book, converters)));
  return "\ufeff" + rows.map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}
