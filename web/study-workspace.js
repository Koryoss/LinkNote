// 3열 학습 작업대: 원문(PDF) · 목차(구간) · 개념+노트.
// 학습 작업대와 수업 필기 API를 함께 사용한다.
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
  var requestedPage = Number(params.get('page')) || null;

  var state = {
    pages: [],
    concepts: [],
    lectureNotes: [],
    segments: [],
    activeSegmentIndex: 0,
    activePage: null,
    sourceAvailable: false,
    library: null,
    navigationUnits: [],
  };

  var noteTimers = {};
  var lectureNoteTimers = {};
  var lectureNoteDrafts = {};
  var lastFocusedBeforeDrawer = null;
  var lastScopeMenuAnchor = null;
  var sourceWidthStorageKey = 'ln_study_workspace_source_width_v2';
  var tocWidthStorageKey = 'ln_study_workspace_toc_width_v2';
  var panelVisibilityStorageKey = 'ln_study_workspace_panels_v1';
  var layoutResizeBound = false;
  var panelVisibility = loadPanelVisibility();

  function loadPanelVisibility() {
    var defaults = { source: true, toc: true, notes: true };
    try {
      var stored = JSON.parse(localStorage.getItem(panelVisibilityStorageKey) || '{}');
      Object.keys(defaults).forEach(function (key) {
        if (typeof stored[key] === 'boolean') defaults[key] = stored[key];
      });
    } catch (error) {}
    if (!Object.keys(defaults).some(function (key) { return defaults[key]; })) defaults.notes = true;
    return defaults;
  }

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

  function renderScopeNavigation() {
    var nav = el('scopeNav');
    if (!nav) return;
    var levels = [
      ['semester', scope.semester, '학기'],
      ['course', scope.course, '과목'],
    ];
    nav.innerHTML = '<button type="button" class="scope-pill home" data-scope-home>홈</button>' +
      levels.filter(function (item) { return item[1]; }).map(function (item) {
        return '<span class="scope-sep" aria-hidden="true">›</span>' +
          '<button type="button" class="scope-pill menu" data-scope-level="' + item[0] + '" aria-label="' + item[2] + ' 선택" aria-haspopup="listbox" aria-expanded="false" title="' + esc(item[1]) + '">' + esc(item[1]) + '</button>';
      }).join('');
    nav.querySelector('[data-scope-home]').addEventListener('click', function () { location.href = '/'; });
    nav.querySelectorAll('[data-scope-level]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        openScopeMenu(button.dataset.scopeLevel, button, event);
      });
    });
    renderDocumentContext();
  }

  // 현재 학습 범위: 실제 단원명과 실제 자료명만 표시한다. 과목 전체 이름은 상단
  // breadcrumb(.scope-nav)의 과목 pill에서 이미 보이므로 여기서 다시 반복하지
  // 않고, "· 단원"처럼 정보 없는 고정 문구도 남기지 않는다. 단원 변경/자료 변경
  // 버튼은 각자의 현재 정보와 같은 줄에 두어 시각적으로 연결한다.
  function renderDocumentContext() {
    var context = el('documentContext');
    if (!context) return;
    context.innerHTML =
      '<div class="scope-row scope-row--unit">' +
        '<h1 class="scope-row-title">' + esc(scope.unit || '단원 미지정') + '</h1>' +
        '<button type="button" class="context-picker" data-scope-level="unit" aria-label="단원 선택" aria-haspopup="listbox" aria-expanded="false" title="' + esc(scope.unit) + '">단원 변경</button>' +
      '</div>' +
      '<div class="scope-row scope-row--material">' +
        '<p class="scope-row-file">' + esc(scope.filename || '자료 미지정') + '</p>' +
        '<button type="button" class="context-picker" data-scope-level="filename" aria-label="자료 선택" aria-haspopup="listbox" aria-expanded="false" title="' + esc(scope.filename) + '">자료 변경</button>' +
      '</div>';
    context.querySelectorAll('[data-scope-level]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        openScopeMenu(button.dataset.scopeLevel, button, event);
      });
    });
  }

  function closeScopeMenu(returnFocus) {
    var menu = el('scopeMenu');
    if (menu) menu.remove();
    document.querySelectorAll('[data-scope-level][aria-expanded="true"]').forEach(function (button) {
      button.setAttribute('aria-expanded', 'false');
    });
    if (returnFocus && lastScopeMenuAnchor && document.body.contains(lastScopeMenuAnchor)) {
      lastScopeMenuAnchor.focus();
    }
    lastScopeMenuAnchor = null;
  }

  function openScopeMenu(level, anchor, event) {
    event.stopPropagation();
    var wasOpen = !!el('scopeMenu') && anchor.getAttribute('aria-expanded') === 'true';
    closeScopeMenu();
    if (wasOpen) return;
    var options = Logic.scopeNavigationOptions(level, scope, state.library, state.navigationUnits);
    if (!options.length) return;
    var current = scope[level];
    var menu = document.createElement('div');
    menu.id = 'scopeMenu';
    menu.className = 'scope-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-label', anchor.getAttribute('aria-label'));
    menu.innerHTML = options.map(function (option) {
      return '<button type="button" class="scope-menu-item" role="option" aria-selected="' + (option === current) + '" data-scope-value="' + esc(option) + '">' + (option === current ? '✓ ' : '') + esc(option) + '</button>';
    }).join('');
    document.body.appendChild(menu);
    var rect = anchor.getBoundingClientRect();
    menu.style.left = Math.min(rect.left, window.innerWidth - menu.offsetWidth - 12) + 'px';
    menu.style.top = Math.min(rect.bottom + 6, window.innerHeight - menu.offsetHeight - 12) + 'px';
    anchor.setAttribute('aria-expanded', 'true');
    lastScopeMenuAnchor = anchor;
    menu.querySelectorAll('[data-scope-value]').forEach(function (button, index) {
      button.addEventListener('click', function (pickEvent) {
        pickEvent.stopPropagation();
        selectScopeOption(level, options[index]);
      });
    });
    var selected = menu.querySelector('[aria-selected="true"]') || menu.querySelector('button');
    if (selected) selected.focus();
  }

  function workspaceUrl(nextScope) {
    return '/study-workspace.html?' + new URLSearchParams(nextScope).toString();
  }

  function navigateToScope(nextScope) {
    closeScopeMenu();
    if (!nextScope || !nextScope.semester || !nextScope.course || !nextScope.unit || !nextScope.filename) {
      location.href = '/';
      return;
    }
    location.href = workspaceUrl(nextScope);
  }

  function loadNavigationUnits(semester, course) {
    return getJSON('/units?' + new URLSearchParams({ semester: semester, course: course }).toString())
      .then(function (result) { return result.units || []; });
  }

  function navigateWithinCourse(semester, course) {
    if (!semester || !course) { location.href = '/'; return; }
    loadNavigationUnits(semester, course).then(function (units) {
      var selectedUnit = units.find(function (item) { return item.unit === scope.unit; }) || units[0];
      var files = selectedUnit && Array.isArray(selectedUnit.files) ? selectedUnit.files : [];
      var filename = files.indexOf(scope.filename) >= 0 ? scope.filename : files[0];
      navigateToScope({ semester: semester, course: course, unit: selectedUnit && selectedUnit.unit, filename: filename });
    }).catch(function () { location.href = '/'; });
  }

  function selectScopeOption(level, value) {
    if (value === scope[level]) { closeScopeMenu(); return; }
    if (level === 'semester') {
      var semester = (state.library && state.library.semesters || []).find(function (item) { return item.semester === value; });
      var courses = semester && Array.isArray(semester.courses) ? semester.courses : [];
      var selectedCourse = courses.find(function (item) { return item.course === scope.course; }) || courses[0];
      navigateWithinCourse(value, selectedCourse && selectedCourse.course);
      return;
    }
    if (level === 'course') { navigateWithinCourse(scope.semester, value); return; }
    if (level === 'unit') {
      var unit = state.navigationUnits.find(function (item) { return item.unit === value; });
      var files = unit && Array.isArray(unit.files) ? unit.files : [];
      navigateToScope({ semester: scope.semester, course: scope.course, unit: value, filename: files.indexOf(scope.filename) >= 0 ? scope.filename : files[0] });
      return;
    }
    if (level === 'filename') {
      navigateToScope({ semester: scope.semester, course: scope.course, unit: scope.unit, filename: value });
    }
  }

  function loadScopeNavigation() {
    renderScopeNavigation();
    Promise.all([
      getJSON('/library').catch(function () { return null; }),
      loadNavigationUnits(scope.semester, scope.course).catch(function () { return []; }),
    ]).then(function (results) {
      state.library = results[0];
      state.navigationUnits = results[1];
      renderScopeNavigation();
    });
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
        var displayTitle = Logic.pageDisplayTitle(p, state.pages, scope.filename);
        var accessibleLabel = 'p.' + p.page + (displayTitle ? ' ' + displayTitle : ' 페이지');
        return '<button type="button" class="toc-page' + (pageActive ? ' active' : '') + '" aria-label="' + esc(accessibleLabel) + '" onclick="StudyWorkspace.jumpToPage(' + p.page + ')">' +
          '<span class="toc-page-num">p.' + p.page + '</span>' +
          (displayTitle ? '<span class="toc-page-title">' + esc(displayTitle) + '</span>' : '') + '</button>';
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
    renderLectureNote();
    renderConcepts();
    renderSource();
  }

  // ---- 구간 수업 필기 ----

  function segmentKey(segment) {
    return segment ? segment.startPage + '-' + segment.endPage : '';
  }

  function storedLectureNote(segment) {
    if (!segment) return null;
    return state.lectureNotes.find(function (note) {
      return Number(note.start_page) === segment.startPage && Number(note.end_page) === segment.endPage;
    }) || null;
  }

  function lectureNoteDraft(segment) {
    var key = segmentKey(segment);
    if (lectureNoteDrafts[key]) return lectureNoteDrafts[key];
    var stored = storedLectureNote(segment);
    return {
      note_text: stored && stored.note_text ? stored.note_text : '',
      tags: stored && Array.isArray(stored.tags) ? stored.tags.slice() : [],
    };
  }

  function renderLectureNote() {
    var panel = el('lectureNotePanel');
    var segment = state.segments[state.activeSegmentIndex];
    if (!segment) {
      panel.innerHTML = '';
      return;
    }
    var draft = lectureNoteDraft(segment);
    var tagLabels = { important: '중요', exam: '시험', question: '질문' };
    var tagsHTML = Object.keys(tagLabels).map(function (tag) {
      var pressed = draft.tags.indexOf(tag) >= 0;
      return '<button type="button" class="note-tag" aria-pressed="' + pressed + '" onclick="StudyWorkspace.toggleLectureTag(\'' + tag + '\')">' + tagLabels[tag] + '</button>';
    }).join('');
    panel.innerHTML = '<div class="lecture-note-card">' +
      '<div class="lecture-note-head"><h2 class="lecture-note-title">이 구간 수업 필기</h2>' +
      '<span class="lecture-note-range">p.' + segment.startPage + (segment.endPage === segment.startPage ? '' : '–' + segment.endPage) + '</span></div>' +
      '<p class="lecture-note-hint">교수님 설명, 강조점, 이해되지 않은 부분을 자유롭게 적어두세요.</p>' +
      '<textarea id="lectureNoteInput" class="lecture-note-input" placeholder="예: 이 부분은 시험에 자주 나온다고 강조하심">' + esc(draft.note_text) + '</textarea>' +
      '<div class="lecture-note-footer">' + tagsHTML + '<div class="note-status" id="lecture-note-status" role="status" aria-live="polite"></div></div>' +
      '</div>';
  }

  function setLectureNoteStatus(kind, text) {
    var status = el('lecture-note-status');
    if (!status) return;
    status.className = 'note-status ' + kind;
    status.textContent = text;
  }

  function scheduleLectureNoteSave(segment, draft) {
    var key = segmentKey(segment);
    lectureNoteDrafts[key] = { note_text: draft.note_text, tags: draft.tags.slice() };
    if (lectureNoteTimers[key]) clearTimeout(lectureNoteTimers[key]);
    setLectureNoteStatus('pending', '저장 대기 중…');
    lectureNoteTimers[key] = setTimeout(function () {
      saveLectureNote(segment, lectureNoteDrafts[key]);
    }, 600);
  }

  function onLectureNoteInput(value) {
    var segment = state.segments[state.activeSegmentIndex];
    if (!segment) return;
    var draft = lectureNoteDraft(segment);
    scheduleLectureNoteSave(segment, { note_text: value, tags: draft.tags });
  }

  function toggleLectureTag(tag) {
    var segment = state.segments[state.activeSegmentIndex];
    if (!segment) return;
    var draft = lectureNoteDraft(segment);
    var tags = draft.tags.slice();
    var index = tags.indexOf(tag);
    if (index >= 0) tags.splice(index, 1);
    else tags.push(tag);
    var input = el('lectureNoteInput');
    scheduleLectureNoteSave(segment, {
      note_text: input ? input.value : draft.note_text,
      tags: tags,
    });
    renderLectureNote();
    setLectureNoteStatus('pending', '저장 대기 중…');
  }

  function saveLectureNote(segment, draft) {
    var key = segmentKey(segment);
    var draftIsCurrent = function () {
      var current = lectureNoteDrafts[key];
      return current && current.note_text === draft.note_text && current.tags.join(',') === draft.tags.join(',');
    };
    if (state.segments[state.activeSegmentIndex] === segment) setLectureNoteStatus('saving', '저장 중…');
    fetch(API + '/lecture-notes', {
      method: 'PUT',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        semester: scope.semester,
        course: scope.course,
        unit: scope.unit,
        filename: scope.filename,
        start_page: segment.startPage,
        end_page: segment.endPage,
        note_text: draft.note_text,
        tags: draft.tags,
      }),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.detail || '저장 실패');
        var previous = storedLectureNote(segment);
        if (previous) state.lectureNotes[state.lectureNotes.indexOf(previous)] = d.note;
        else state.lectureNotes.push(d.note);
        if (draftIsCurrent()) {
          delete lectureNoteDrafts[key];
        }
        if (state.segments[state.activeSegmentIndex] === segment) {
          setLectureNoteStatus(lectureNoteDrafts[key] ? 'pending' : 'saved', lectureNoteDrafts[key] ? '저장 대기 중…' : '저장됨');
        }
      });
    }).catch(function () {
      if (state.segments[state.activeSegmentIndex] === segment && draftIsCurrent()) {
        setLectureNoteStatus('failed', '저장 실패 · 다시 입력해 주세요');
      }
    });
  }

  // ---- 개념 + 개념 노트 ----

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
    el('lectureNotePanel').addEventListener('input', function (e) {
      if (e.target.id === 'lectureNoteInput') onLectureNoteInput(e.target.value);
    });
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

  // ---- 데스크톱 3열 너비 조절 ----

  function visiblePanelCount() {
    return ['source', 'toc', 'notes'].filter(function (key) { return panelVisibility[key]; }).length;
  }

  function layoutChromeWidth() {
    var count = visiblePanelCount();
    return count > 1 ? ((count - 1) * 10) + ((count * 2 - 2) * 12) : 0;
  }

  function layoutContentWidth() {
    var workspace = el('workspace');
    if (!workspace) return window.innerWidth;
    var style = window.getComputedStyle(workspace);
    return workspace.getBoundingClientRect().width - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0);
  }

  function panelWidthBounds(kind) {
    var source = el('sourcePanel');
    var toc = el('tocPanel');
    var sourceWidth = source ? source.getBoundingClientRect().width : 620;
    var tocWidth = toc ? toc.getBoundingClientRect().width : 220;
    var available = layoutContentWidth() - layoutChromeWidth();
    if (kind === 'source') {
      return { min: 280, max: Math.max(280, available - (panelVisibility.toc ? tocWidth : 0) - (panelVisibility.notes ? 320 : 0)) };
    }
    return { min: 180, max: Math.max(180, available - (panelVisibility.source ? sourceWidth : 0) - (panelVisibility.notes ? 320 : 0)) };
  }

  function defaultSourceWidth() {
    return Math.min(720, Math.max(360, Math.round(layoutContentWidth() * 0.46)));
  }

  function setPanelWidth(kind, width, persist) {
    var workspace = el('workspace');
    var handle = el(kind === 'source' ? 'sourceResizer' : 'tocResizer');
    if (!workspace || !handle) return;
    var bounds = panelWidthBounds(kind);
    var fallback = kind === 'source' ? defaultSourceWidth() : 220;
    var next = Math.round(Math.min(bounds.max, Math.max(bounds.min, Number(width) || fallback)));
    workspace.style.setProperty(kind === 'source' ? '--source-width' : '--toc-width', next + 'px');
    handle.setAttribute('aria-valuemin', String(bounds.min));
    handle.setAttribute('aria-valuemax', String(bounds.max));
    handle.setAttribute('aria-valuenow', String(next));
    if (persist) localStorage.setItem(kind === 'source' ? sourceWidthStorageKey : tocWidthStorageKey, String(next));
  }

  function bindPanelResizer(kind, handle, panel) {
    handle.addEventListener('pointerdown', function (event) {
      if (window.innerWidth < 900) return;
      var startX = event.clientX;
      var startWidth = panel.getBoundingClientRect().width;
      handle.setPointerCapture(event.pointerId);
      document.body.classList.add('layout-resizing');
      var move = function (moveEvent) {
        setPanelWidth(kind, startWidth + moveEvent.clientX - startX, false);
      };
      var finish = function (finishEvent) {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', finish);
        handle.removeEventListener('pointercancel', finish);
        document.body.classList.remove('layout-resizing');
        if (handle.hasPointerCapture(finishEvent.pointerId)) handle.releasePointerCapture(finishEvent.pointerId);
        setPanelWidth(kind, panel.getBoundingClientRect().width, true);
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', finish);
      handle.addEventListener('pointercancel', finish);
      event.preventDefault();
    });
    handle.addEventListener('keydown', function (event) {
      var current = panel.getBoundingClientRect().width;
      var bounds = panelWidthBounds(kind);
      if (event.key === 'ArrowLeft') setPanelWidth(kind, current - 24, true);
      else if (event.key === 'ArrowRight') setPanelWidth(kind, current + 24, true);
      else if (event.key === 'Home') setPanelWidth(kind, bounds.min, true);
      else if (event.key === 'End') setPanelWidth(kind, bounds.max, true);
      else return;
      event.preventDefault();
    });
    handle.addEventListener('dblclick', function () {
      setPanelWidth(kind, kind === 'source' ? defaultSourceWidth() : 220, true);
    });
  }

  function bindLayoutResizers() {
    if (layoutResizeBound) return;
    var sourceHandle = el('sourceResizer');
    var tocHandle = el('tocResizer');
    var sourcePanel = el('sourcePanel');
    var tocPanel = el('tocPanel');
    if (!sourceHandle || !tocHandle || !sourcePanel || !tocPanel) return;
    layoutResizeBound = true;
    setPanelWidth('source', localStorage.getItem(sourceWidthStorageKey) || defaultSourceWidth(), false);
    setPanelWidth('toc', localStorage.getItem(tocWidthStorageKey) || 220, false);
    bindPanelResizer('source', sourceHandle, sourcePanel);
    bindPanelResizer('toc', tocHandle, tocPanel);
    window.addEventListener('resize', function () {
      applyPanelVisibility(false);
      if (panelVisibility.source) setPanelWidth('source', sourcePanel.getBoundingClientRect().width, false);
      if (panelVisibility.toc) setPanelWidth('toc', tocPanel.getBoundingClientRect().width, false);
    });
  }

  function panelGridTemplate() {
    var visible = ['source', 'toc', 'notes'].filter(function (key) { return panelVisibility[key]; });
    if (visible.length === 1) {
      return visible[0] === 'source' ? 'minmax(280px, 1fr)' : (visible[0] === 'toc' ? 'minmax(180px, 1fr)' : 'minmax(320px, 1fr)');
    }
    var tracks = [];
    visible.forEach(function (key, index) {
      var isLast = index === visible.length - 1;
      if (key === 'source') tracks.push(isLast ? 'minmax(280px, 1fr)' : 'minmax(280px, var(--source-width))');
      else if (key === 'toc') tracks.push(isLast ? 'minmax(180px, 1fr)' : 'minmax(180px, var(--toc-width))');
      else tracks.push('minmax(320px, 1fr)');
      if (!isLast) tracks.push('10px');
    });
    return tracks.join(' ');
  }

  function updatePanelToggleButtons() {
    var labels = { source: '원문', toc: '목차', notes: '필기·설명' };
    document.querySelectorAll('[data-panel-toggle]').forEach(function (button) {
      var key = button.dataset.panelToggle;
      var visible = !!panelVisibility[key];
      button.setAttribute('aria-pressed', String(visible));
      button.setAttribute('aria-label', labels[key] + (visible ? ' 숨기기' : ' 열기'));
      button.textContent = button.hasAttribute('data-panel-short') ? (visible ? '숨기기' : '열기') : labels[key] + (visible ? ' 숨기기' : ' 열기');
    });
  }

  function applyPanelVisibility(persist) {
    var desktop = window.innerWidth >= 900;
    var effective = desktop ? panelVisibility : { source: true, toc: true, notes: true };
    el('sourcePanel').hidden = !effective.source;
    el('tocPanel').hidden = !effective.toc;
    el('conceptPanel').hidden = !effective.notes;
    el('sourceResizer').hidden = !desktop || !effective.source || !(effective.toc || effective.notes);
    el('tocResizer').hidden = !desktop || !effective.toc || !effective.notes;
    if (desktop) el('workspace').style.gridTemplateColumns = panelGridTemplate();
    else el('workspace').style.removeProperty('grid-template-columns');
    updatePanelToggleButtons();
    if (persist) localStorage.setItem(panelVisibilityStorageKey, JSON.stringify(panelVisibility));
  }

  function togglePanel(kind) {
    if (!Object.prototype.hasOwnProperty.call(panelVisibility, kind)) return;
    var status = el('panelToggleStatus');
    if (panelVisibility[kind] && visiblePanelCount() === 1) {
      status.textContent = '하나 이상의 학습 영역은 열어 두어야 합니다.';
      return;
    }
    panelVisibility[kind] = !panelVisibility[kind];
    status.textContent = '';
    applyPanelVisibility(true);
    if (panelVisibility.source) setPanelWidth('source', localStorage.getItem(sourceWidthStorageKey) || defaultSourceWidth(), false);
    if (panelVisibility.toc) setPanelWidth('toc', localStorage.getItem(tocWidthStorageKey) || 220, false);
  }

  function bindPanelToggles() {
    document.querySelectorAll('[data-panel-toggle]').forEach(function (button) {
      if (button.dataset.panelToggleBound) return;
      button.dataset.panelToggleBound = '1';
      button.addEventListener('click', function () { togglePanel(button.dataset.panelToggle); });
    });
    updatePanelToggleButtons();
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
    loadScopeNavigation();
    el('openFullBtn').onclick = openFull;
    setStage('loading', '학습 작업대를 불러오는 중입니다…');
    Promise.all([
      getJSON('/study-workspace?' + scopeQuery()),
      getJSON('/lecture-notes?' + scopeQuery()),
    ]).then(function (results) {
      var result = results[0];
      state.pages = result.pages || [];
      state.concepts = result.concepts || [];
      state.lectureNotes = results[1].items || [];
      state.sourceAvailable = !!result.source_available;
      state.segments = Logic.buildSegments(state.pages, state.concepts);
      state.activeSegmentIndex = 0;
      var requestedPageExists = requestedPage && state.pages.some(function (page) { return page.page === requestedPage; });
      state.activePage = requestedPageExists
        ? requestedPage
        : (state.segments[0] && state.segments[0].pages[0] ? state.segments[0].pages[0].page : null);
      if (requestedPageExists) {
        var requestedSegment = state.segments.findIndex(function (segment) {
          return requestedPage >= segment.startPage && requestedPage <= segment.endPage;
        });
        if (requestedSegment >= 0) state.activeSegmentIndex = requestedSegment;
      }
      setStage('ready');
      bindPanelToggles();
      applyPanelVisibility(false);
      bindLayoutResizers();
      renderToc();
      renderLectureNote();
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
    toggleLectureTag: toggleLectureTag,
    toggleToc: toggleToc,
    toggleSource: toggleSource,
    togglePanel: togglePanel,
    closeScopeMenu: closeScopeMenu,
  };

  document.addEventListener('click', closeScopeMenu);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && el('scopeMenu')) closeScopeMenu(true);
  });

  init();
})();
