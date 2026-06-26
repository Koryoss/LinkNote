import html
import json
import os
import streamlit as st

from pdf_loader import extract_pdf_text
from rag import (
    add_pdf_pages_to_db,
    answer_question,
    answer_with_connections,
    delete_chunks_by_filter,
    get_filter_label,
    get_library_overview,
    reset_collection
)


UPLOAD_DIR = "./data/uploads"
TIMETABLE_PATH = "./data/timetable.json"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(TIMETABLE_PATH), exist_ok=True)


def load_timetable():
    if not os.path.exists(TIMETABLE_PATH):
        return []

    try:
        with open(TIMETABLE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_timetable(timetable):
    with open(TIMETABLE_PATH, "w", encoding="utf-8") as f:
        json.dump(timetable, f, ensure_ascii=False, indent=2)


def render_timetable_sidebar():
    st.sidebar.subheader("내 시간표")

    timetable = load_timetable()
    days = ["월", "화", "수", "목", "금", "토", "일"]
    saved_semesters = sorted(
        {
            item.get("semester")
            for item in timetable
            if item.get("semester")
        }
    )

    with st.sidebar.expander("시간표 추가", expanded=False):
        with st.form("timetable_form"):
            semester_options = ["새 학기 입력"] + saved_semesters
            semester_choice = st.selectbox("시간표 학기", semester_options)

            if semester_choice == "새 학기 입력":
                schedule_semester = st.text_input("새 학기", placeholder="예: 2026-1, 여름계절학기")
            else:
                schedule_semester = semester_choice

            schedule_days = st.multiselect("요일", days, placeholder="수업 요일 선택")
            schedule_time = st.text_input("시간", placeholder="예: 09:00-10:30")
            schedule_course = st.text_input("과목명", placeholder="예: 병리생리학1")
            schedule_memo = st.text_input("메모", placeholder="예: 2공학관 301호")
            submitted = st.form_submit_button("시간표 저장")

        if submitted:
            if not schedule_semester.strip() or not schedule_course.strip() or not schedule_days:
                st.warning("학기, 요일, 과목명을 입력해주세요.")
            else:
                for schedule_day in schedule_days:
                    timetable.append({
                        "semester": schedule_semester.strip(),
                        "day": schedule_day,
                        "time": schedule_time.strip(),
                        "course": schedule_course.strip(),
                        "memo": schedule_memo.strip()
                    })

                save_timetable(timetable)
                st.success("시간표를 저장했습니다.")
                st.rerun()

    if not timetable:
        st.sidebar.info("아직 설정된 시간표가 없습니다.")
        return

    semesters = sorted(
        {
            item.get("semester") or "학기 미정"
            for item in timetable
        }
    )

    for semester in semesters:
        semester_items = [
            item
            for item in timetable
            if (item.get("semester") or "학기 미정") == semester
        ]

        with st.sidebar.expander(f"{semester} 시간표", expanded=True):
            for day in days:
                day_items = [item for item in semester_items if item.get("day") == day]

                if not day_items:
                    continue

                st.markdown(f"**{day}요일**")

                for item in sorted(day_items, key=lambda row: row.get("time", "")):
                    time = item.get("time") or "시간 미정"
                    course = item.get("course") or "과목 미정"
                    memo = item.get("memo")

                    if memo:
                        st.markdown(f"- {time} / {course} - {memo}")
                    else:
                        st.markdown(f"- {time} / {course}")

    if st.sidebar.button("시간표 전체 삭제"):
        save_timetable([])
        st.rerun()


def render_user_id_sidebar():
    st.sidebar.subheader("사용자")
    user_id = st.sidebar.text_input("사용자명", placeholder="예: yujin")

    if not user_id.strip():
        st.sidebar.info("사용자명을 입력해주세요")

    return user_id.strip()


def get_search_options(library_overview):
    semesters = sorted(library_overview["semesters"].keys())
    courses = set()
    files = []

    for semester, semester_courses in library_overview["semesters"].items():
        for course, course_files in semester_courses.items():
            courses.add(course)

            for filename, file_info in course_files.items():
                title = file_info.get("title")
                label = (
                    f"{semester} / {course} / {title} / {filename}"
                    if title
                    else f"{semester} / {course} / {filename}"
                )
                files.append({
                    "label": label,
                    "semester": semester,
                    "course": course,
                    "filename": filename
                })

    return {
        "semesters": semesters,
        "courses": sorted(courses),
        "files": sorted(files, key=lambda item: item["label"])
    }


def get_timetable_course_options():
    timetable = load_timetable()
    options = {}

    for item in timetable:
        semester = item.get("semester") or "학기 미정"
        course = item.get("course") or "과목 미정"

        if not semester.strip() or not course.strip():
            continue

        options.setdefault(semester, set()).add(course)

    return {
        semester: sorted(courses)
        for semester, courses in sorted(options.items())
    }


def get_file_options(library_overview, selected_semester=None, selected_course=None):
    file_options = {}

    for semester, semester_courses in library_overview["semesters"].items():
        if selected_semester and semester != selected_semester:
            continue

        for course, course_files in semester_courses.items():
            if selected_course and course != selected_course:
                continue

            for filename, file_info in course_files.items():
                title = file_info.get("title")
                label = (
                    f"{title} / {filename}"
                    if title
                    else filename
                )
                full_label = f"{semester} / {course} / {label}"
                file_options[full_label] = {
                    "semester": semester,
                    "course": course,
                    "filename": filename
                }

    return dict(sorted(file_options.items()))


def render_search_filter(library_overview):
    st.markdown("**검색 범위**")
    search_options = get_search_options(library_overview)
    search_filter = {}

    semester_choices = ["전체 학기"] + search_options["semesters"]
    selected_semester = st.selectbox("학기", semester_choices)

    if selected_semester != "전체 학기":
        search_filter["semester"] = selected_semester
        course_choices = sorted(
            library_overview["semesters"]
            .get(selected_semester, {})
            .keys()
        )
    else:
        course_choices = search_options["courses"]

    selected_course = st.selectbox("과목", ["전체 과목"] + course_choices)

    if selected_course != "전체 과목":
        search_filter["course"] = selected_course

    file_options = get_file_options(
        library_overview,
        selected_semester=search_filter.get("semester"),
        selected_course=search_filter.get("course")
    )
    selected_file_label = st.selectbox("파일", ["전체 파일"] + list(file_options.keys()))

    if selected_file_label != "전체 파일":
        selected_file = file_options[selected_file_label]
        search_filter = {
            "semester": selected_file["semester"],
            "course": selected_file["course"],
            "filename": selected_file["filename"]
        }

    if not search_filter:
        st.caption("현재 검색 범위: 전체 자료")
        return None, "전체 자료"

    search_scope_label = get_filter_label(search_filter)
    st.caption(f"현재 검색 범위: {search_scope_label}")

    return search_filter, search_scope_label


def render_library_overview(library_overview):
    st.subheader("저장된 자료 현황")
    st.write(f"총 chunk 수: {library_overview['total_chunks']}")

    if library_overview["total_chunks"] == 0:
        st.info("아직 저장된 자료가 없습니다.")
        return

    for saved_semester, courses in sorted(library_overview["semesters"].items()):
        with st.expander(f"{saved_semester} 자료", expanded=True):
            for saved_course, files in sorted(courses.items()):
                st.markdown(f"**{saved_course}**")
                st.markdown(f"- 파일 수: {len(files)}")

                for file_info in sorted(files.values(), key=lambda item: item["filename"]):
                    saved_title = file_info.get("title")
                    saved_filename = file_info.get("filename")

                    if saved_title:
                        st.markdown(f"- {saved_title} / {saved_filename}")
                    else:
                        st.markdown(f"- {saved_filename}")


def render_library_management(library_overview, user_id):
    with st.expander("자료 관리", expanded=False):
        if library_overview["total_chunks"] == 0:
            st.info("관리할 저장 자료가 없습니다.")
            return

        st.markdown("**선택한 범위 삭제**")

        search_options = get_search_options(library_overview)
        selected_semester = st.selectbox(
            "삭제할 학기",
            ["선택 안 함"] + search_options["semesters"],
            key="delete_semester"
        )

        delete_filter = {}

        if selected_semester != "선택 안 함":
            delete_filter["semester"] = selected_semester
            course_choices = sorted(
                library_overview["semesters"]
                .get(selected_semester, {})
                .keys()
            )
        else:
            course_choices = search_options["courses"]

        selected_course = st.selectbox(
            "삭제할 과목",
            ["선택 안 함"] + course_choices,
            key="delete_course"
        )

        if selected_course != "선택 안 함":
            delete_filter["course"] = selected_course

        file_options = get_file_options(
            library_overview,
            selected_semester=delete_filter.get("semester"),
            selected_course=delete_filter.get("course")
        )
        selected_file_label = st.selectbox(
            "삭제할 파일",
            ["선택 안 함"] + list(file_options.keys()),
            key="delete_file"
        )

        if selected_file_label != "선택 안 함":
            selected_file = file_options[selected_file_label]
            delete_filter = {
                "semester": selected_file["semester"],
                "course": selected_file["course"],
                "filename": selected_file["filename"]
            }

        if delete_filter:
            st.warning(f"삭제 범위: {get_filter_label(delete_filter)}")

            if st.button("선택한 자료 삭제"):
                deleted_count = delete_chunks_by_filter(delete_filter, user_id=user_id)
                st.success(f"{deleted_count}개 chunk를 삭제했습니다.")
                st.rerun()
        else:
            st.info("삭제할 학기, 과목, 파일 중 하나를 선택해주세요.")

        st.divider()
        st.markdown("**전체 학습 데이터 초기화**")
        st.warning("현재 사용자에게 저장된 학습 chunk만 삭제합니다. 업로드된 PDF 원본은 삭제하지 않습니다.")
        confirm_reset = st.checkbox("전체 학습 데이터를 삭제하겠습니다.")

        if st.button("전체 학습 데이터 초기화", disabled=not confirm_reset):
            deleted_count = reset_collection(user_id=user_id)
            st.success(f"{deleted_count}개 chunk를 초기화했습니다.")
            st.rerun()


# 지식 갤러리: 과목별 핵심 개념과 중요도(1~5).
# 실제 그래프 대신 "개념들이 모여 하나의 구조가 된다"는 느낌을 주기 위한 영역.
CONCEPT_MAP = {
    "비뇨": [("신부전", 5), ("사구체", 4), ("세뇨관", 3), ("요로폐쇄", 3), ("체액조절", 2)],
    "신장": [("신부전", 5), ("사구체", 4), ("세뇨관", 3), ("네프론", 3), ("요관", 2)],
    "병태": [("신부전", 5), ("염증", 4), ("항상성", 3), ("체액조절", 2), ("전해질", 2)],
    "인체": [("신장", 5), ("사구체", 4), ("세뇨관", 3), ("네프론", 3), ("요로", 2)],
}

DEFAULT_CONCEPTS = [
    ("신부전", 5),
    ("사구체", 4),
    ("세뇨관", 3),
    ("요로폐쇄", 3),
    ("체액조절", 2),
]


def get_concepts_for_course(course):
    if course:
        for keyword, concepts in CONCEPT_MAP.items():
            if keyword in course:
                return concepts

    return DEFAULT_CONCEPTS


def _concept_card_style(weight):
    # 중요도가 높을수록 카드가 조금씩 더 크게 보이도록 단계별 스타일을 둔다.
    tiers = {
        5: ("1.18rem", "16px 22px", "#1e3a8a", "#dbeafe"),
        4: ("1.02rem", "14px 19px", "#1e40af", "#e0ecff"),
        3: ("0.92rem", "12px 16px", "#3730a3", "#eef2ff"),
        2: ("0.82rem", "10px 14px", "#4b5563", "#f3f4f6"),
        1: ("0.78rem", "9px 12px", "#6b7280", "#f6f7f9"),
    }
    return tiers.get(weight, tiers[2])


def render_hero():
    st.markdown(
        """
<div style="
    background: linear-gradient(135deg, #eef4ff 0%, #f7f9ff 60%, #ffffff 100%);
    border: 1px solid #e3eafc;
    border-radius: 16px;
    padding: 22px 26px;
    margin: 4px 0 18px;
">
    <div style="font-size: 1.5rem; font-weight: 700; color: #1e293b; letter-spacing: -0.01em;">
        지식은 연결될 때 이해가 된다
    </div>
    <div style="font-size: 0.92rem; color: #64748b; margin-top: 6px; line-height: 1.6;">
        서로 다른 과목 · 개념 · 자료가 연결되어 하나의 학습 구조가 됩니다.
    </div>
</div>
""",
        unsafe_allow_html=True
    )


def render_concept_gallery(library_overview):
    courses = set()

    for semester_courses in library_overview["semesters"].values():
        courses.update(semester_courses.keys())

    courses = sorted(courses)

    st.markdown("#### 🗂️ 지식 갤러리")

    if courses:
        selected_course = st.selectbox(
            "관련 개념을 볼 과목",
            courses,
            key="gallery_course"
        )
    else:
        selected_course = None
        st.caption("아직 등록된 과목이 없습니다. 아래 예시 개념으로 구조를 미리 살펴보세요.")

    concepts = get_concepts_for_course(selected_course)

    cards_html = ""

    for name, weight in concepts:
        font_size, padding, color, background = _concept_card_style(weight)
        cards_html += (
            f"<div class='concept-card' style=\""
            f"font-size:{font_size};padding:{padding};"
            f"color:{color};background:{background};\">"
            f"{html.escape(name)}</div>"
        )

    st.markdown(
        f"""
<style>
.concept-gallery {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    padding: 8px 2px 2px;
}}
.concept-card {{
    border: 1px solid rgba(30, 64, 175, 0.12);
    border-radius: 14px;
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
    white-space: nowrap;
}}
</style>
<div class='concept-gallery'>{cards_html}</div>
<div style="font-size: 0.8rem; color: #6b7280; margin-top: 10px;">
    개념들이 모여 하나의 학습 구조를 만듭니다. (크기는 중요도 순)
</div>
""",
        unsafe_allow_html=True
    )


st.set_page_config(
    page_title="내 강의자료 공부 도우미",
    page_icon="📚",
    layout="wide"
)


st.title("📚 내 강의자료 공부 도우미")
st.write("API 비용 없이 로컬에서 PDF 기반 질문 답변을 테스트합니다.")

render_hero()

user_id = render_user_id_sidebar()
render_timetable_sidebar()

library_overview = get_library_overview(user_id) if user_id else {
    "total_chunks": 0,
    "semesters": {}
}

render_concept_gallery(library_overview)

st.divider()

render_library_overview(library_overview)
if user_id:
    render_library_management(library_overview, user_id)
else:
    st.info("사용자명을 입력하면 저장된 자료 현황을 볼 수 있습니다.")

st.divider()


st.subheader("1. 자료 정보 입력")

timetable_course_options = get_timetable_course_options()
semester_options = ["직접 입력"] + list(timetable_course_options.keys())
selected_timetable_semester = st.selectbox(
    "시간표 학기 선택",
    semester_options
)

if selected_timetable_semester == "직접 입력":
    semester = st.text_input("학기", placeholder="예: 2025-1")
    course = st.text_input("과목명", placeholder="예: 딥러닝")
else:
    semester = selected_timetable_semester
    course_options = timetable_course_options[selected_timetable_semester]
    course = st.selectbox("시간표 과목 선택", course_options)

    st.text_input("학기", value=semester, disabled=True)
    st.text_input("과목명", value=course, disabled=True)

title = st.text_input("자료명", placeholder="예: CNN 3주차 강의자료")


st.subheader("2. PDF 업로드")

uploaded_file = st.file_uploader("PDF 파일 업로드", type=["pdf"])


if uploaded_file is not None:
    file_path = os.path.join(UPLOAD_DIR, uploaded_file.name)

    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    st.success(f"업로드 완료: {uploaded_file.name}")

    if st.button("이 PDF 학습시키기"):
        if not user_id:
            st.warning("사용자명을 입력해주세요")
        elif not semester.strip() or not course.strip() or not title.strip():
            st.warning("학기, 과목명, 자료명을 모두 입력해주세요.")
        else:
            with st.spinner("PDF 텍스트를 추출하고 로컬 벡터 DB에 저장하는 중..."):
                pages = extract_pdf_text(file_path)

                if not pages:
                    st.error("PDF에서 텍스트를 추출하지 못했습니다. 이미지 기반 PDF일 수 있습니다.")
                else:
                    add_pdf_pages_to_db(
                        pages=pages,
                        filename=uploaded_file.name,
                        semester=semester.strip(),
                        course=course.strip(),
                        title=title.strip(),
                        user_id=user_id
                    )

                    st.success("학습 완료! 이제 질문할 수 있어요.")


st.divider()


st.subheader("3. 질문하기")

answer_mode = st.radio(
    "답변 모드",
    ["일반 질문", "여러 PDF 연결 질문"],
    horizontal=True
)

search_filter, search_scope_label = render_search_filter(library_overview)

question = st.text_input("질문을 입력하세요", placeholder="예: 이 자료에서 CNN의 padding을 설명해줘")

st.markdown(
    """
추천 질문
- 이 자료의 핵심 내용을 요약해줘.
- 이 자료에서 중요한 개념 5개를 뽑아줘.
- 이 자료에서 가장 시험에 나올 만한 부분을 정리해줘.
- 이 자료에서 헷갈리기 쉬운 개념을 비교해서 설명해줘.
- 이 자료와 다른 학기/과목 자료에서 연결되는 개념을 찾아줘.
- <키워드>가 무엇인지 자료를 바탕으로 설명해줘.
"""
)


if st.button("질문하기"):
    if not user_id:
        st.warning("사용자명을 입력해주세요")
    elif not question.strip():
        st.warning("질문을 입력해주세요.")
    else:
        with st.spinner("관련 내용을 찾고 로컬 모델로 답변 생성 중..."):
            if answer_mode == "여러 PDF 연결 질문":
                answer, sources = answer_with_connections(
                    question,
                    search_filter=search_filter,
                    search_scope_label=search_scope_label,
                    user_id=user_id
                )
            else:
                answer, sources = answer_question(
                    question,
                    search_filter=search_filter,
                    search_scope_label=search_scope_label,
                    user_id=user_id
                )

        st.subheader("답변")
        st.write(answer)

        st.subheader("연결된 자료")
        st.caption("관련 자료들이 모여 하나의 이해를 만듭니다.")

        unique_sources = set(
            (
                s["semester"],
                s["course"],
                s["title"],
                s["filename"],
                s["page"]
            )
            for s in sources
        )

        cards_html = ""

        for semester, course, title, filename, page in sorted(unique_sources):
            course_label = html.escape(course or "과목 미정")
            main_label = title or os.path.splitext(filename or "")[0] or "자료"
            main_label = html.escape(main_label)
            file_label = html.escape(filename or "")
            page_label = html.escape(str(page))

            cards_html += f"""
<div class="source-card">
    <div class="source-tag">{course_label}</div>
    <div class="source-title">{main_label}</div>
    <div class="source-meta">{file_label} · p.{page_label}</div>
</div>
"""

        st.markdown(
            f"""
<style>
.source-gallery {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    padding: 6px 2px 2px;
}}
.source-card {{
    flex: 0 1 200px;
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 14px 16px;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}}
.source-tag {{
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 600;
    color: #1e40af;
    background: #eef2ff;
    border-radius: 999px;
    padding: 3px 10px;
    margin-bottom: 8px;
}}
.source-title {{
    font-size: 0.98rem;
    font-weight: 700;
    color: #1f2937;
    line-height: 1.35;
    word-break: keep-all;
}}
.source-meta {{
    font-size: 0.78rem;
    color: #6b7280;
    margin-top: 6px;
}}
</style>
<div class="source-gallery">{cards_html}</div>
""",
            unsafe_allow_html=True
        )
