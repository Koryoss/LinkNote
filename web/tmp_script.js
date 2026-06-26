
  const API = 'http://127.0.0.1:8000';
  let state = { page: 'home', semester: null, course: null, unit: null };

  const DEMO = {
    library: { total_chunks: 0, semesters: [
      { semester: "2026-1", courses: [
        { course: "병태생리학 1", files: [{filename:"예시.pdf", title:"예시"}] },
        { course: "인체 구조와 기능 2", files: [{filename:"예시.pdf", title:"예시"}] },
      ]},
    ]},
    units: {
      "병태생리학 1": [
        { unit: "관상동맥질환, 울혈성 심부전", file_count: 1, page_count: 12 },
        { unit: "급성 신부전, 만성 신부전", file_count: 1, page_count: 8 },
      ],
      "인체 구조와 기능 2": [ { unit: "신경계", file_count: 1, page_count: 10 } ],
    },
    concepts: {
      "급성 신부전, 만성 신부전": [
        { name:"신부전", weight:5, page:3, links:["급성신부전","만성신부전","체액조절"] },
        { name:"급성신부전", weight:4, page:4, links:["신부전"] },
        { name:"만성신부전", weight:4, page:6, links:["신부전","체액조절"] },
        { name:"체액조절", weight:2, page:5, links:["신부전","만성신부전"] },
      ],
    },
  };

  async function getJSON(path){
    const r = await fetch(API + path, { method:'GET' });
    if(!r.ok) throw new Error('HTTP '+r.status);
    return r.json();
  }
  function setStatus(ok){
    document.getElementById('dot').className = 'dot ' + (ok ? 'on':'off');
    document.getElementById('statusText').textContent = ok ? 'API 연결됨 (8000)' : 'API 미연결 · 데모 표시';
    const b = document.getElementById('banner');
    if(ok){ b.style.display='none'; }
    else { b.style.display='block';
      b.textContent='⚠ API 서버(127.0.0.1:8000)에 연결되지 않아 데모 데이터를 보여줍니다. run_api.sh 로 서버를 켜면 실제 데이터로 바뀝니다.'; }
  }
  const uid = () => document.getElementById('uid').value.trim() || '정유진';
  let latestLibrary = null;
  let latestTimetable = null;

  function updateBackButton(){
    const backBtn = document.getElementById('backBtn');
    if(state.page && state.page !== 'home'){
      backBtn.style.display = 'inline-flex';
    } else {
      backBtn.style.display = 'none';
    }
  }

  function setAppState(page, extra = {}, replace = false){
    state = { page, ...extra };
    const nextState = { page, ...extra };
    if(replace) history.replaceState(nextState, '', '');
    else history.pushState(nextState, '', '');
    updateBackButton();
    renderConfirmPanel();
  }

  function restoreAppState(event){
    const data = event.state;
    if(!data || data.page === 'home'){
      loadHome(false);
      return;
    }
    if(data.page === 'course'){
      state = { page: 'course', semester: data.semester, course: data.course, unit: null };
      openCourse(data.course, false);
      return;
    }
    if(data.page === 'unit'){
      state = { page: 'unit', semester: data.semester, course: data.course, unit: data.unit };
      openUnit(data.unit, false);
      return;
    }
    loadHome(false);
  }

  async function loadSidebar(){
    try {
      latestTimetable = await getJSON(`/timetable?user_id=${encodeURIComponent(uid())}`);
      setStatus(true);
    } catch (err) {
      latestTimetable = { semesters: [] };
    }
    renderTimetableSidebar(latestTimetable);
    renderConfirmPanel();
  }

  function renderTimetableSidebar(data){
    const container = document.getElementById('timetableContent');
    if(!data || !Array.isArray(data.semesters) || data.semesters.length === 0){
      container.innerHTML = '<div class="empty">시간표가 없습니다. 상단 사용자명을 입력하고 새로고침하거나, 로컬 시간표를 먼저 등록하세요.</div>';
      return;
    }
    container.innerHTML = data.semesters.map(semester => {
      const courses = Array.isArray(semester.courses) ? semester.courses.join(' · ') : '';
      return `<div class="timetable-item"><strong>${esc(semester.semester)}</strong><div>${esc(courses || '등록된 과목이 없습니다.')}</div></div>`;
    }).join('');
  }

  function renderConfirmPanel(){
    const container = document.getElementById('confirmContent');
    const lines = [];
    lines.push(`<span>사용자: ${esc(uid())}</span>`);
    if(state.page === 'home'){
      if(latestLibrary){
        const semesterCount = latestLibrary.semesters.length;
        const courseCount = latestLibrary.semesters.reduce((sum, sem) => sum + (sem.courses || []).length, 0);
        lines.push(`<span>학습된 학기: ${semesterCount}</span>`);
        lines.push(`<span>등록된 과목: ${courseCount}</span>`);
      } else {
        lines.push('<span>학습된 자료를 불러오세요.</span>');
      }
    } else if(state.page === 'course'){
      lines.push(`<span>선택 학기: ${esc(state.semester)}</span>`);
      lines.push(`<span>선택 과목: ${esc(state.course)}</span>`);
      if(latestLibrary){
        const course = latestLibrary.semesters.find(s=>s.semester===state.semester)?.courses?.find(c=>c.course===state.course);
        lines.push(`<span>등록 파일: ${course ? (course.files||[]).length : 0}</span>`);
      }
    } else if(state.page === 'unit'){
      lines.push(`<span>선택 학기: ${esc(state.semester)}</span>`);
      lines.push(`<span>선택 과목: ${esc(state.course)}</span>`);
      lines.push(`<span>선택 단원: ${esc(state.unit)}</span>`);
    }
    container.innerHTML = lines.map(l => `<div class="confirm-line">${l}</div>`).join('');
  }

  function ensureInitialHistory() {
    if (!history.state) {
      history.replaceState({ page: 'home' }, '', '');
    }
    updateBackButton();
  }

  async function loadHome(push=true){
    state = { page: 'home', semester: null, course: null, unit: null };
    if(push) setAppState('home', { page: 'home' });
    latestLibrary = null;
    let data;
    try { data = await getJSON('/library?user_id='+encodeURIComponent(uid())); latestLibrary = data; setStatus(true); }
    catch(e){ data = DEMO.library; latestLibrary = data; setStatus(false); }
    renderHome(data);
    await loadSidebar();
    updateBackButton();
    renderConfirmPanel();
  }

  document.getElementById('loadBtn').addEventListener('click', ()=>loadHome());
  document.getElementById('refreshBtn').addEventListener('click', ()=>loadSidebar());
  document.getElementById('backBtn').addEventListener('click', ()=>history.back());
  window.addEventListener('popstate', restoreAppState);

  function renderHome(data){
    setCrumbs([]);
    const sems = data.semesters || [];
    const view = document.getElementById('view');
    if(!sems.length){ view.innerHTML = emptyBox('이 사용자명으로 학습된 자료가 없습니다. 상단 사용자명을 확인하세요.'); return; }
    let opts = sems.map((s,i)=>`<option value="${i}">${esc(s.semester)}</option>`).join('');
    view.innerHTML = `
      <div class="section-title">학기 <span class="muted">· 선택하면 학습된 과목이 보입니다</span></div>
      <div style="padding:0 24px;"><select id="semSel" style="border:1px solid #d8dee9;border-radius:8px;padding:8px 10px;font-size:0.9rem;">${opts}</select></div>
      <div class="section-title">과목 <span class="muted">· 과목을 누르면 단원이 열립니다</span></div>
      <div class="grid" id="courseGrid"></div>`;
    const sel = document.getElementById('semSel');
    const draw = () => {
      const s = sems[sel.value]; state.semester = s.semester;
      const grid = document.getElementById('courseGrid');
      grid.innerHTML = (s.courses||[]).map(c => `
        <div class="card" onclick='openCourse(${JSON.stringify(c.course)})'>
          <div><div class="ctag">과목</div><div class="ctitle">${esc(c.course)}</div></div>
          <div class="cmeta">파일 ${ (c.files||[]).length } · 단원 보기 →</div>
        </div>`).join('') || emptyInline('이 학기에 학습된 과목이 없습니다.');
    };
    sel.onchange = draw; draw();
  }

  async function openCourse(course, push=true){
    state.page = 'course';
    state.course = course;
    state.unit = null;
    if(push) setAppState('course', { semester: state.semester, course });
    let data;
    try {
      data = await getJSON(`/units?user_id=${encodeURIComponent(uid())}&semester=${encodeURIComponent(state.semester)}&course=${encodeURIComponent(course)}`);
      setStatus(true);
    } catch(e){ data = { status: (DEMO.units[course]?'ready':'empty'), units: DEMO.units[course]||[] }; setStatus(false); }
    renderUnits(course, data);
    await loadSidebar();
    updateBackButton();
    renderConfirmPanel();
  }
  function renderUnits(course, data){
    setCrumbs([['홈', 'loadHome()'], [state.semester], [course]]);
    const view = document.getElementById('view');
    const units = data.units || [];
    if(!units.length || data.status==='empty'){
      view.innerHTML = `<div class="section-title">${esc(course)} · 단원</div>` +
        emptyBox('아직 이 과목에 단원이 없습니다. PDF를 업로드할 때 단원명을 입력하면 여기에 나타납니다.');
      return;
    }
    view.innerHTML = `<div class="section-title">${esc(course)} · 단원 <span class="muted">· 단원을 누르면 개념 지도가 열립니다</span></div>
      <div class="grid">${units.map((u,i)=>`
        <div class="card" onclick='openUnit(${JSON.stringify(u.unit)})'>
          <div><div class="ctag">단원 ${i+1}</div><div class="ctitle">${esc(u.unit)}</div></div>
          <div class="cmeta">파일 ${u.file_count||0} · 페이지 ${u.page_count||0}</div>
        </div>`).join('')}</div>`;
  }

  async function openUnit(unit, push=true){
    state.page = 'unit';
    state.unit = unit;
    if(push) setAppState('unit', { semester: state.semester, course: state.course, unit });
    let nodes = [], demo = false, notReady = false;
    try {
      const d = await getJSON(`/concepts?user_id=${encodeURIComponent(uid())}&semester=${encodeURIComponent(state.semester)}&course=${encodeURIComponent(state.course)}&unit=${encodeURIComponent(unit)}`);
      setStatus(true);
      if(d.status==='ready' && (d.concepts||[]).length){ nodes = d.concepts; }
      else { notReady = true; nodes = DEMO.concepts[unit] || DEMO.concepts["급성 신부전, 만성 신부전"]; demo = true; }
    } catch(e){ setStatus(false); nodes = DEMO.concepts[unit] || DEMO.concepts["급성 신부전, 만성 신부전"]; demo = true; }
    renderConcepts(unit, nodes, demo, notReady);
    await loadSidebar();
    updateBackButton();
    renderConfirmPanel();
  }
  function renderConcepts(unit, nodes, demo, notReady){
    setCrumbs([['홈','loadHome()'], [state.semester], [state.course, `openCourse(${JSON.stringify(state.course)})`], [unit]]);
    const view = document.getElementById('view');
    const note = notReady ? '<span class="badge-demo">개념 추출 전 · 예시</span>' : (demo ? '<span class="badge-demo">데모</span>' : '');
    view.innerHTML = `<div class="section-title">${esc(unit)} · 개념 지도 ${note}</div><div id="canvas"><svg id="lines"></svg></div>`;
    layoutNodes(nodes);
  }
  let lastNodes = null;
  function layoutNodes(nodes){
    lastNodes = nodes;
    const canvas = document.getElementById('canvas'); if(!canvas) return;
    const svg = document.getElementById('lines');
    canvas.querySelectorAll('.node').forEach(n=>n.remove()); svg.innerHTML='';
    const W = canvas.clientWidth, H = canvas.clientHeight;
    const core = nodes.reduce((a,b)=> (b.weight||0)>(a.weight||0)?b:a, nodes[0]);
    const others = nodes.filter(n=>n!==core);
    const pos = {}; pos[core.name] = { x: W/2, y: H/2 };
    others.forEach((n,i)=>{
      const ang = (Math.PI*2*i)/Math.max(others.length,1) - Math.PI/2;
      pos[n.name] = { x: W/2 + Math.cos(ang)*Math.min(W,820)*0.30, y: H/2 + Math.sin(ang)*H*0.32 };
    });
    const drawn = new Set();
    nodes.forEach(n => (n.links||[]).forEach(t => {
      if(!pos[t]) return; const key=[n.name,t].sort().join('|'); if(drawn.has(key))return; drawn.add(key);
      const a=pos[n.name], b=pos[t];
      const l=document.createElementNS('http://www.w3.org/2000/svg','line');
      l.setAttribute('x1',a.x);l.setAttribute('y1',a.y);l.setAttribute('x2',b.x);l.setAttribute('y2',b.y);
      l.setAttribute('class','line'); l.dataset.a=n.name; l.dataset.b=t; svg.appendChild(l);
    }));
    function updateLinesFor(name){
      svg.querySelectorAll('.line').forEach(l => {
        if(l.dataset.a===name){ l.setAttribute('x1',pos[name].x); l.setAttribute('y1',pos[name].y); }
        if(l.dataset.b===name){ l.setAttribute('x2',pos[name].x); l.setAttribute('y2',pos[name].y); }
      });
    }
    nodes.forEach((n,i) => {
      const w = n.weight||3;
      const el = document.createElement('div');
      el.className = `node w${w}` + (n===core?' core':'');
      el.style.left = pos[n.name].x+'px'; el.style.top = pos[n.name].y+'px';
      el.style.animationDelay = (i*0.05)+'s';
      el.innerHTML = `<div class="nm">${esc(n.name)}</div>` + (n.page?`<div class="pg">p.${esc(String(n.page))}</div>`:'');

      // 호버 툴팁
      el.addEventListener('mouseenter', (e)=>{ if(!dragging) showTip(n, e); });
      el.addEventListener('mousemove', (e)=>{ if(!dragging) moveTip(e); });
      el.addEventListener('mouseleave', hideTip);

      // 드래그 (이동 거의 없으면 클릭=선택으로 처리)
      el.addEventListener('pointerdown', (e)=>{
        e.preventDefault(); hideTip();
        const rect = canvas.getBoundingClientRect();
        let moved = 0; dragging = true; el.classList.add('dragging'); el.setPointerCapture(e.pointerId);
        const startX = e.clientX, startY = e.clientY;
        const move = (ev)=>{
          moved += Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY);
          pos[n.name].x = Math.max(20, Math.min(rect.width-20, ev.clientX - rect.left));
          pos[n.name].y = Math.max(20, Math.min(rect.height-20, ev.clientY - rect.top));
          el.style.left = pos[n.name].x+'px'; el.style.top = pos[n.name].y+'px';
          updateLinesFor(n.name);
        };
        const up = (ev)=>{
          el.classList.remove('dragging'); dragging = false;
          el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up);
          if(moved < 6){ selectNode(n.name, nodes); } // 거의 안 움직였으면 클릭으로 간주
        };
        el.addEventListener('pointermove', move); el.addEventListener('pointerup', up);
      });
      canvas.appendChild(el);
    });
    canvas.onclick = (e)=>{ if(e.target.id!=='canvas' && e.target.tagName!=='svg') return;
      canvas.querySelectorAll('.node').forEach(x=>x.classList.remove('dim','active'));
      svg.querySelectorAll('.line').forEach(x=>x.classList.remove('active')); };
  }

  let dragging = false;
  function showTip(n, e){
    const tip = document.getElementById('tip');
    const links = (n.links||[]).length;
    tip.innerHTML = `<div class="t-nm">${esc(n.name)}</div>` +
      `<div class="t-meta">${n.page?('p.'+esc(String(n.page))+' · '):''}연결 개념 ${links}개</div>`;
    tip.classList.add('on'); moveTip(e);
  }
  function moveTip(e){
    const tip = document.getElementById('tip');
    tip.style.left = (e.clientX + 14) + 'px';
    tip.style.top  = (e.clientY + 14) + 'px';
  }
  function hideTip(){ document.getElementById('tip').classList.remove('on'); }
  function selectNode(name, nodes){
    const node = nodes.find(n=>n.name===name);
    const keep = new Set([name, ...(node.links||[])]);
    document.querySelectorAll('#canvas .node').forEach(el => {
      const nm = el.querySelector('.nm').textContent;
      el.classList.toggle('dim', !keep.has(nm));
      el.classList.toggle('active', nm===name);
    });
    document.querySelectorAll('#lines .line').forEach(l => {
      l.classList.toggle('active', l.dataset.a===name || l.dataset.b===name);
    });
  }

  function setCrumbs(items){
    const el = document.getElementById('crumbs');
    if(!items.length){ el.innerHTML=''; return; }
    el.innerHTML = items.map((it,i)=>{
      const piece = it[1] ? `<a onclick="${it[1]}">${esc(it[0])}</a>` : `<span>${esc(it[0])}</span>`;
      return piece + (i<items.length-1 ? ' <span style="color:#cbd5e1">›</span> ' : '');
    }).join('');
  }
  function emptyBox(msg){ return `<div class="empty">${esc(msg)}</div>`; }
  function emptyInline(msg){ return `<div class="empty" style="grid-column:1/-1">${esc(msg)}</div>`; }
  function esc(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  window.addEventListener('resize', ()=>{ if(lastNodes && document.getElementById('canvas')) layoutNodes(lastNodes); });

  ensureInitialHistory();
  loadHome();
