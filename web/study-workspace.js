// 3열 학습 작업대: 목차(구간) · 개념+노트 · 원문(PDF).
// GET /study-workspace 응답만 사용한다 (백엔드/다른 화면 무변경).
(function () {
  'use strict';

  var configuredApi = window.LINKNOTE_API_BASE || localStorage.getItem('ln_api_base') || '';
  var API = (configuredApi || (location.protocol.startsWith('http') ? '' : 'http://127.0.0.1:8000')).replace(/\/$/, '');
  var token = function () { return localStorage.getItem('ln_token') || ''; };
  var headers = function (extra) {
    var base = token() ? { Authorization: 'Bearer ' + token() } : {};
    return Object.assign({}, base, extra || {});
  };
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };

  var Logic = window.StudyWorkspaceLogic;

  var params = new URLSearchParams(location.search);
  var scope = {
    semester: params.get('semester') || '',
    course: params.get('course') || '',
    unit: params.get('unit') || '',
    filename: params.get('filename') || '',
  };

  var state = {
    pages: [],
    concepts: [],
    segments: [],
    activeSegmentIndex: 0,
    activePage: null,
    sourceAvailable: false,
  };

  var noteTimers = {};
  var lastFocusedBeforeDrawer = null;

  function scopeQuery(extra) {
    return new URLSearchParams(Object.assign({}, scope, extra || {})).toString();
  }

  function hasFullScope() {
    return !!(scope.semester && scope.course && scope.unit && scope.filename);
  }

  function getJSON(path) {
    return fetch(API + path, { headers: headers() }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          var err = new Error(d.detail || (path + ' returned ' + r.status));
          err.status = r.status;
          throw err;
        }
        return d;
      });
    });
  }

  function el(id) { return document.getElementById(id); }

  function setStage(kind, html) {
    var app = el('app');
    var workspace = el('workspace');
    if (kind === 'ready') {
      app.hidden = true;
      workspace.hidden = false;
      return;
    }
    workspace.hidden = true;
    app.hidden = false;
    app.className = kind;
    app.innerHTML = html;
  }

  function loginRequiredStage() {
    var next = encodeURIComponent('/study-workspace.html?' + scopeQuery());
    setStage('empty-state', '로그인이 필요합니다.<div style="margin-top:12px"><a class="btn primary" href="/?next=' + next + '">로그인하러 가기</a></div>');
  }

  function missingScopeStage() {
    setStage('empty-state', '필요한 정보(학기·과목·단원·자료)가 없습니다. My Library에서 다시 열어주세요.<div style="margin-top:12px"><a class="btn primary" href="/">My Library로</a></div>');
  }

  function notFoundStage(message) {
    setStage('error-state', esc(message || '해당 자료를 찾을 수 없습니다.') + '<div style="margin-top:12px"><a class="btn primary" href="/">My Library로</a></div>');
  }

  function errorStage(message) {
    setStage('error-state', '학습 작업대를 불러오지 못했습니다: ' + esc(message) + '<div style="margin-top:12px"><button class="btn primary" onclick="StudyWorkspace.init()">다시 시도</button></div>');
  }

  function scopeCrumbHTML() {
    return [scope.semester, scope.course, scope.unit, scope.filename].filter(Boolean).map(esc).join(' · ');
  }

  // ---- TOC (목차) ----

  function renderToc() {
    var list = el('tocList');
    if (!state.segments.length) {
      list.innerHTML = '<div class="muted-note">표시할 페이지가 없습니다.</div>';
      return;
    }
    list.innerHTML = state.segments.map(function (segment, index) {
      var active = index === state.activeSegmentIndex;
      var pagesHTML = segment.pages.map(function (p) {
        var pageActive = p.page === state.activePage;
        return '<button type="button" class="toc-page' + (pageActive ? ' active' : '') + '" onclick="StudyWorkspace.jumpToPage(' + p.page + ')">' +
          '<span class="toc-page-num">p.' + p.page + '</span><span class="toc-page-title">' + esc(p.title) + '</span></button>';
      }).join('');
      return '<div class="toc-segment' + (active ? ' active' : '') + '">' +
        '<button type="button" class="toc-segment-head" onclick="StudyWorkspace.selectSegment(' + index + ')">' + esc(segment.label) + '</button>' +
        '<div class="toc-pages">' + pagesHTML + '</div></div>';
    }).join('');
  }

  function selectSegment(index) {
    var segment = state.segments[index];
    if (!segment) return;
    state.activeSegmentIndex = index;
    var firstPage = segment.pages[0] ? segment.pages[0].page : null;
    setActivePage(firstPage);
  }

  function jumpToPage(pageNumber) {
    var segIndex = state.segments.findIndex(function (s) { return pageNumber >= s.startPage && pageNumber <= s.endPage; });
    if (segIndex >= 0) state.activeSegmentIndex = segIndex;
    setActivePage(pageNumber);
  }

  function setActivePage(pageNumber) {
    state.activePage = pageNumber;
    renderToc();
    renderConcepts();
    renderSource();
  }

  // ---- 개념 + 노트 ----

  function renderConcepts() {
    var container = el('conceptList');
    var segment = state.segments[state.activeSegmentIndex];
    var concepts = segment ? Logic.conceptsForSegment(segment, state.concepts) : [];
    if (!concepts.length) {
      container.innerHTML = '<div class="muted-note">이 구간에서 추출된 개념이 없습니다.</div>';
      return;
    }
    container.innerHTML = concepts.map(conceptCardHTML).join('');
  }

  function conceptCardHTML(concept) {
    var key = esc(concept.name);
    var noteText = concept.note && concept.note.note_text ? concept.note.note_text : '';
    var occurrences = (concept.pages || []).map(function (p) {
      var label = 'p.' + p;
      var inToc = state.pages.some(function (pg) { return pg.page === p; });
      return '<button type="button" class="occurrence-chip" onclick="StudyWorkspace.jumpToPage(' + p + ')">' + label + (inToc ? '' : ' · 목차에 없는 출현 위치') + '</button>';
    }).join('');
    return '<div class="concept-card" data-concept="' + key + '">' +
      '<div class="concept-name">' + key + '</div>' +
      (concept.definition ? '<div class="concept-def">' + esc(concept.definition) + '</div>' : '') +
      '<div class="occurrence-row">' + occurrences + '</div>' +
      '<label class="note-label" for="note-' + key + '">내 노트</label>' +
      '<textarea id="note-' + key + '" class="note-input">' + esc(noteText) + '</textarea>' +
      '<div class="note-status" id="note-status-' + key + '"></div>' +
      '</div>';
  }

  // 개념 이름에 따옴표 등 HTML 특수문자가 있을 수 있어 inline onXXX 속성 대신
  // 위임 리스너(#conceptList)에서 data-concept로 대상을 찾는다.
  function bindNoteInputDelegation() {
    el('conceptList').addEventListener('input', function (e) {
      if (!e.target.classList.contains('note-input')) return;
      var card = e.target.closest('.concept-card');
      var conceptName = card ? card.getAttribute('data-concept') : null;
      if (conceptName == null) return;
      onNoteInput(conceptName, e.target.value);
    });
  }

  function onNoteInput(conceptName, value) {
    if (noteTimers[conceptName]) clearTimeout(noteTimers[conceptName]);
    setNoteStatus(conceptName, 'pending', '저장 대기 중…');
    noteTimers[conceptName] = setTimeout(function () { saveNote(conceptName, value); }, 600);
  }

  function setNoteStatus(conceptName, kind, text) {
    var statusEl = document.getElementById('note-status-' + conceptName);
    if (!statusEl) return;
    statusEl.className = 'note-status ' + kind;
    statusEl.textContent = text;
  }

  function saveNote(conceptName, noteText) {
    var concept = state.concepts.find(function (c) { return c.name === conceptName; });
    setNoteStatus(conceptName, 'saving', '저장 중…');
    var payload = {
      semester: scope.semester,
      course: scope.course,
      unit: scope.unit,
      filename: scope.filename,
      concept: conceptName,
      note_text: noteText,
      source_pages: concept ? concept.pages : [],
    };
    fetch(API + '/concept-notes', {
      method: 'PUT',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.detail || '저장 실패');
        if (concept) concept.note = d.note;
        setNoteStatus(conceptName, 'saved', '저장됨');
      });
    }).catch(function () {
      setNoteStatus(conceptName, 'failed', '저장 실패 · 다시 시도');
    });
  }

  // ---- 원문(PDF) ----

  function fileUrl(withPage) {
    var q = scopeQuery({ token: token() });
    var url = API + '/file?' + q;
    return withPage && state.activePage ? url + '#page=' + state.activePage : url;
  }

  function renderSource() {
    var body = el('sourceBody');
    if (!state.sourceAvailable) {
      body.innerHTML = '<div class="muted-note">원문을 불러올 수 없습니다. 자료 전체에서 다시 열어보세요.</div>';
      return;
    }
    body.innerHTML = '<iframe id="sourceFrame" class="source-frame" title="원문 PDF"></iframe>';
    el('sourceFrame').src = fileUrl(true);
  }

  function openFull() {
    if (!state.sourceAvailable) return;
    window.open(fileUrl(false), '_blank', 'noopener');
  }

  // ---- 900px 드로어(목차/원문) ----

  function focusableIn(panel) {
    return Array.prototype.slice.call(panel.querySelectorAll('button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])'))
      .filter(function (elm) { return !elm.disabled && elm.offsetParent !== null; });
  }

  function onDrawerKeydown(e) {
    if (e.key === 'Escape') {
      toggleToc(false);
      toggleSource(false);
      return;
    }
    if (e.key !== 'Tab') return;
    var openPanel = el('tocPanel').classList.contains('open') ? el('tocPanel') : (el('sourcePanel').classList.contains('open') ? el('sourcePanel') : null);
    if (!openPanel) return;
    var focusable = focusableIn(openPanel);
    if (!focusable.length) return;
    var first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function toggleDrawer(panelId, scrimId, force) {
    var panel = el(panelId);
    var scrim = el(scrimId);
    var shouldOpen = typeof force === 'boolean' ? force : !panel.classList.contains('open');
    if (shouldOpen === panel.classList.contains('open')) return;
    if (shouldOpen) {
      lastFocusedBeforeDrawer = document.activeElement;
      panel.classList.add('open');
      scrim.hidden = false;
      document.addEventListener('keydown', onDrawerKeydown);
      var focusTarget = focusableIn(panel)[0];
      if (focusTarget) focusTarget.focus();
    } else {
      panel.classList.remove('open');
      scrim.hidden = true;
      if (!el('tocPanel').classList.contains('open') && !el('sourcePanel').classList.contains('open')) {
        document.removeEventListener('keydown', onDrawerKeydown);
      }
      if (lastFocusedBeforeDrawer && document.body.contains(lastFocusedBeforeDrawer)) lastFocusedBeforeDrawer.focus();
    }
  }

  function toggleToc(force) { toggleDrawer('tocPanel', 'tocScrim', force); }
  function toggleSource(force) { toggleDrawer('sourcePanel', 'sourceScrim', force); }

  // ---- 초기화 ----

  var noteDelegationBound = false;

  function init() {
    if (!token()) { loginRequiredStage(); return; }
    if (!hasFullScope()) { missingScopeStage(); return; }
    if (!noteDelegationBound) { bindNoteInputDelegation(); noteDelegationBound = true; }
    el('scopeCrumb').textContent = scopeCrumbHTML();
    el('openFullBtn').onclick = openFull;
    setStage('loading', '학습 작업대를 불러오는 중입니다…');
    getJSON('/study-workspace?' + scopeQuery()).then(function (result) {
      state.pages = result.pages || [];
      state.concepts = result.concepts || [];
      state.sourceAvailable = !!result.source_available;
      state.segments = Logic.buildSegments(state.pages, state.concepts);
      state.activeSegmentIndex = 0;
      state.activePage = state.segments[0] && state.segments[0].pages[0] ? state.segments[0].pages[0].page : null;
      setStage('ready');
      renderToc();
      renderConcepts();
      renderSource();
    }).catch(function (err) {
      if (err.status === 404) notFoundStage(err.message);
      else errorStage(err.message || String(err));
    });
  }

  window.StudyWorkspace = {
    init: init,
    selectSegment: selectSegment,
    jumpToPage: jumpToPage,
    onNoteInput: onNoteInput,
    toggleToc: toggleToc,
    toggleSource: toggleSource,
  };

  init();
})();
