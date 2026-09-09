// Pure grouping/filtering helpers for the study workspace table of contents.
//
// GET /study-workspace does not provide chapter/section headings: the only
// per-page "title" field it returns is the whole file's upload-time title,
// identical on every page (see docs/development-roles.md and rag.py
// add_pdf_pages_to_db). So the left-hand table of contents is built instead
// from concept.first_page boundaries: whenever a new concept first appears,
// that page starts a new "구간" (page range). This file has no DOM/API
// access so it can run unmodified in the browser and under `node --test`.
(function (root) {
  'use strict';

  function normalizePages(pages) {
    return (pages || [])
      .map(function (p) {
        return {
          page: Number(p.page),
          title: p.title,
          text_preview: p.text_preview,
          concepts: Array.isArray(p.concepts) ? p.concepts : [],
        };
      })
      .filter(function (p) { return Number.isFinite(p.page); })
      .sort(function (a, b) { return a.page - b.page; });
  }

  function segmentLabel(startPage, endPage, conceptName) {
    var range = startPage === endPage ? (startPage + 'p') : (startPage + '~' + endPage + 'p');
    return conceptName ? (range + ' · 대표 개념: ' + conceptName) : range;
  }

  function comparableTitle(value) {
    return String(value || '')
      .replace(/\.pdf$/i, '')
      .replace(/[_\-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .toLocaleLowerCase();
  }

  // Upload-time page titles are commonly the same week/file name on every
  // page. Prefer the page's concepts and suppress repeated document titles.
  function pageDisplayTitle(page, pages, filename) {
    var concepts = Array.from(new Set((page && page.concepts || []).filter(Boolean)));
    if (concepts.length) return concepts.slice(0, 2).join(' · ');

    var title = String(page && page.title || '').trim();
    if (!title) return '';
    var normalized = comparableTitle(title);
    if (!normalized || normalized === comparableTitle(filename)) return '';
    var repeated = (pages || []).filter(function (candidate) {
      return comparableTitle(candidate && candidate.title) === normalized;
    }).length > 1;
    return repeated ? '' : title;
  }

  function scopeNavigationOptions(level, scope, library, units) {
    var semesters = library && Array.isArray(library.semesters) ? library.semesters : [];
    var semester = semesters.find(function (item) { return item.semester === scope.semester; });
    var course = semester && Array.isArray(semester.courses)
      ? semester.courses.find(function (item) { return item.course === scope.course; })
      : null;
    var unitItems = Array.isArray(units) ? units : [];
    var unit = unitItems.find(function (item) { return item.unit === scope.unit; });
    if (level === 'semester') return semesters.map(function (item) { return item.semester; });
    if (level === 'course') return (semester && semester.courses || []).map(function (item) { return item.course; });
    if (level === 'unit') return unitItems.map(function (item) { return item.unit; });
    if (level === 'filename') return unit && Array.isArray(unit.files) ? unit.files.slice() : (course ? (course.files || []).map(function (item) { return item.filename; }) : []);
    return [];
  }

  function occurrencePagePreview(pages, activePage, limit) {
    var previewLimit = Math.max(1, Math.floor(Number(limit) || 5));
    var all = Array.from(new Set((pages || [])
      .map(Number)
      .filter(function (page) { return Number.isInteger(page) && page > 0; })))
      .sort(function (a, b) { return a - b; });
    var visible = all.slice(0, previewLimit);
    var active = Number(activePage);
    if (all.indexOf(active) >= 0 && visible.indexOf(active) < 0 && visible.length === previewLimit) {
      visible[visible.length - 1] = active;
      visible.sort(function (a, b) { return a - b; });
    }
    return { all: all, visible: visible, hiddenCount: Math.max(0, all.length - visible.length) };
  }

  // Builds ordered page-range segments ("구간") from concept first-appearance
  // boundaries. Pages before the first concept's first_page join the first
  // segment rather than forming their own unlabeled group. When no concept
  // has a valid first_page in range, returns a single unlabeled segment so
  // callers can still render a flat, ungrouped page list.
  function buildSegments(pages, concepts) {
    var sortedPages = normalizePages(pages);
    if (!sortedPages.length) return [];

    var minPage = sortedPages[0].page;
    var maxPage = sortedPages[sortedPages.length - 1].page;

    var boundaryConcepts = {};
    (concepts || []).forEach(function (c) {
      var fp = Number(c && c.first_page);
      if (!Number.isFinite(fp) || fp < minPage || fp > maxPage) return;
      if (!(fp in boundaryConcepts)) boundaryConcepts[fp] = c;
    });
    var boundaries = Object.keys(boundaryConcepts)
      .map(Number)
      .sort(function (a, b) { return a - b; });

    if (!boundaries.length) {
      return [{
        startPage: minPage,
        endPage: maxPage,
        label: segmentLabel(minPage, maxPage, null),
        representativeConcept: null,
        pages: sortedPages,
      }];
    }

    return boundaries.map(function (boundary, i) {
      var start = i === 0 ? minPage : boundary;
      var end = i === boundaries.length - 1 ? maxPage : boundaries[i + 1] - 1;
      var segPages = sortedPages.filter(function (p) { return p.page >= start && p.page <= end; });
      var conceptName = boundaryConcepts[boundary].name;
      return {
        startPage: start,
        endPage: end,
        label: segmentLabel(start, end, conceptName),
        representativeConcept: conceptName,
        pages: segPages,
      };
    });
  }

  // Concepts that occur on at least one page inside the segment, in the same
  // order as the source concepts[] array (already first_page-sorted by the
  // backend).
  function conceptsForSegment(segment, concepts) {
    var namesInSegment = new Set();
    (segment && segment.pages || []).forEach(function (p) {
      (p.concepts || []).forEach(function (name) { namesInSegment.add(name); });
    });
    return (concepts || []).filter(function (c) { return namesInSegment.has(c && c.name); });
  }

  var api = {
    buildSegments: buildSegments,
    conceptsForSegment: conceptsForSegment,
    segmentLabel: segmentLabel,
    pageDisplayTitle: pageDisplayTitle,
    scopeNavigationOptions: scopeNavigationOptions,
    occurrencePagePreview: occurrencePagePreview,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.StudyWorkspaceLogic = api;
  }
})(typeof window !== 'undefined' ? window : this);
