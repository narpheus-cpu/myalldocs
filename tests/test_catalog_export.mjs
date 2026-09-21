import assert from "node:assert/strict";
import test from "node:test";
import {catalogToCsv} from "../js/catalog-export.mjs";

test("exports every supplied book with Korean headers, Unicode and quoted commas", () => {
  const csv = catalogToCsv([
    {title: '한글, "제목"', author: "홍길동", tags: ["소설", "한국 문학"], genre: "novel", documentType: "fiction", bookId: "a", createdAt: "2026-09-20"},
    {title: "두 번째", bookId: "b"},
  ], {genre: value => value === "novel" ? "소설" : "", profile: value => value === "fiction" ? "소설" : ""});
  assert.ok(csv.startsWith('\ufeff"작품명","저자","장르"'));
  assert.match(csv, /"한글, ""제목""","홍길동","소설","소설 · 한국 문학"/);
  assert.match(csv, /"두 번째"/);
  assert.equal(csv.split("\r\n").length, 4);
});

test("does not allow spreadsheet formulas in untrusted catalog fields", () => {
  const csv = catalogToCsv([{title: "=HYPERLINK(\"https://example.org\")", author: "  +SUM(1,2)", tags: ["@user"]}]);
  assert.match(csv, /"'=HYPERLINK/);
  assert.match(csv, /"'  \+SUM/);
  assert.match(csv, /"'@user"/);
});

test("empty list exports only the header", () => {
  const csv = catalogToCsv([]);
  assert.equal(csv.split("\r\n").length, 2);
});
