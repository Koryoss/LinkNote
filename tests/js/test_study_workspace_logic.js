// Run with: node --test tests/js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { buildSegments, conceptsForSegment, pageDisplayTitle, scopeNavigationOptions } = require('../../web/study-workspace-logic.js');

function page(n, concepts) {
  return { page: n, title: 'file.pdf', text_preview: 'p' + n, concepts: concepts || [] };
}

test('buildSegments groups pages by concept first_page boundaries', () => {
  const pages = [page(1), page(2, ['A']), page(3, ['A']), page(4, ['B']), page(5, ['B', 'C']), page(6, ['C'])];
  const concepts = [
    { name: 'A', first_page: 2 },
    { name: 'B', first_page: 4 },
    { name: 'C', first_page: 5 },
  ];
  const segments = buildSegments(pages, concepts);
  assert.equal(segments.length, 3);
  assert.deepEqual(segments.map((s) => [s.startPage, s.endPage]), [[1, 3], [4, 4], [5, 6]]);
  assert.equal(segments[0].representativeConcept, 'A');
  assert.equal(segments[0].label, '1~3p · 대표 개념: A');
});

test('buildSegments folds leading pages (before the first concept) into the first segment', () => {
  const pages = [page(1), page(2), page(3, ['A']), page(4, ['A'])];
  const concepts = [{ name: 'A', first_page: 3 }];
  const segments = buildSegments(pages, concepts);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].startPage, 1);
  assert.equal(segments[0].endPage, 4);
});

test('buildSegments returns one unlabeled segment when no concept has a valid first_page', () => {
  const pages = [page(1), page(2), page(3)];
  const segments = buildSegments(pages, []);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].representativeConcept, null);
  assert.equal(segments[0].label, '1~3p');
});

test('buildSegments ignores concept first_page values outside the page range', () => {
  const pages = [page(1, ['A']), page(2, ['A'])];
  const concepts = [
    { name: 'A', first_page: 1 },
    { name: 'Ghost', first_page: 99 },
  ];
  const segments = buildSegments(pages, concepts);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].endPage, 2);
});

test('buildSegments deduplicates multiple concepts sharing the same first_page', () => {
  const pages = [page(1), page(2, ['A', 'B']), page(3, ['A', 'B'])];
  const concepts = [
    { name: 'A', first_page: 2 },
    { name: 'B', first_page: 2 },
  ];
  const segments = buildSegments(pages, concepts);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].representativeConcept, 'A');
});

test('buildSegments returns an empty array for an empty page list', () => {
  assert.deepEqual(buildSegments([], []), []);
});

test('conceptsForSegment returns only concepts occurring on the segment pages, in source order', () => {
  const concepts = [
    { name: 'A', first_page: 2 },
    { name: 'B', first_page: 4 },
    { name: 'C', first_page: 5 },
  ];
  const segment = { pages: [page(4, ['B']), page(5, ['B', 'C'])] };
  const result = conceptsForSegment(segment, concepts);
  assert.deepEqual(result.map((c) => c.name), ['B', 'C']);
});

test('conceptsForSegment returns an empty array when the segment has no concept pages', () => {
  const concepts = [{ name: 'A', first_page: 2 }];
  const segment = { pages: [page(1, [])] };
  assert.deepEqual(conceptsForSegment(segment, concepts), []);
});

test('pageDisplayTitle suppresses a repeated week or file title', () => {
  const pages = [page(1), page(2), page(3)];
  assert.equal(pageDisplayTitle(pages[0], pages, 'file.pdf'), '');
  const weekPages = [
    { page: 1, title: '02주_심리학적유형과_자기이해', concepts: [] },
    { page: 2, title: '02주_심리학적유형과_자기이해', concepts: [] },
  ];
  assert.equal(pageDisplayTitle(weekPages[0], weekPages, 'different.pdf'), '');
});

test('pageDisplayTitle prefers concise page concepts over the document title', () => {
  const pages = [{ page: 1, title: 'week.pdf', concepts: ['성격유형론', '자기이해', '세 번째'] }];
  assert.equal(pageDisplayTitle(pages[0], pages, 'week.pdf'), '성격유형론 · 자기이해');
});

test('scopeNavigationOptions exposes semester, course, unit, and file choices', () => {
  const scope = { semester: '2026-2', course: '간호학', unit: '1주', filename: 'a.pdf' };
  const library = { semesters: [
    { semester: '2026-2', courses: [{ course: '간호학', files: [{ filename: 'a.pdf' }] }, { course: '약리학', files: [] }] },
    { semester: '2026-1', courses: [] },
  ] };
  const units = [
    { unit: '1주', files: ['a.pdf', 'b.pdf'] },
    { unit: '2주', files: ['c.pdf'] },
  ];
  assert.deepEqual(scopeNavigationOptions('semester', scope, library, units), ['2026-2', '2026-1']);
  assert.deepEqual(scopeNavigationOptions('course', scope, library, units), ['간호학', '약리학']);
  assert.deepEqual(scopeNavigationOptions('unit', scope, library, units), ['1주', '2주']);
  assert.deepEqual(scopeNavigationOptions('filename', scope, library, units), ['a.pdf', 'b.pdf']);
});
